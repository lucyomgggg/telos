import os
import json
import uuid
import time
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict

from .config import settings, TELOS_HOME
from .logger import get_logger
from .memory import MemoryStore, VectorStore
from .llm import LLMService
from .usage import CostTracker
from .schemas import GoalSchema
from .interfaces import Tool, Critic, TemplateLoader

log = get_logger("core")

class Storage:
    def __init__(self, sqlite_store: Optional[MemoryStore] = None, vector_store: Optional[VectorStore] = None):
        self.sqlite = sqlite_store or MemoryStore()
        self.vector = vector_store or VectorStore()
        from .sandbox import SandboxManager
        self.sandbox = SandboxManager()
        self.tool_registry: Dict[str, Tool] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        from .tools import BashTool, WriteFileTool, ReadFileTool, TaskCompleteTool
        self.register_tool("execute_command", BashTool(self.sandbox))
        self.register_tool("write_file", WriteFileTool(self.sandbox))
        self.register_tool("read_file", ReadFileTool(self.sandbox))
        self.register_tool("task_complete", TaskCompleteTool())

    def register_tool(self, name: str, tool: Tool):
        self.tool_registry[name] = tool
        log.debug(f"Registered tool: {name}")

# --- Agent Loop ---

class AgentLoop:
    def __init__(self, storage: Optional[Storage] = None, critic: Optional[Critic] = None):
        self.storage = storage or Storage()
        self.cost_tracker = CostTracker(self.storage.sqlite)
        self.templates = TemplateLoader()
        
        from .critic import CriticAgent
        from .deduplicator import GoalDeduplicator
        
        self.critic_agent = critic or CriticAgent(cost_tracker=self.cost_tracker)
        self.deduplicator = GoalDeduplicator()
        
        current_settings = settings.load() 
        
        # Centralized LLM Services
        self.producer_llm = LLMService(model=current_settings.llm.producer_model, cost_tracker=self.cost_tracker)
        self.goal_gen_llm = LLMService(model=current_settings.llm.goal_gen_model or current_settings.llm.producer_model, cost_tracker=self.cost_tracker)

    def _check_safety(self):
        current_settings = settings.load()
        daily_limit = current_settings.daily_loop_limit
        monthly_limit = current_settings.monthly_cost_limit
        
        daily_loops = self.cost_tracker.get_daily_loop_count()
        if daily_loops >= daily_limit:
            raise RuntimeError(f"Daily loop limit reached: {daily_loops}/{daily_limit}")
        
        monthly_cost = self.cost_tracker.get_monthly_cost()
        if monthly_cost >= monthly_limit:
            raise RuntimeError(f"Monthly cost limit reached: ${monthly_cost:.2f}/${monthly_limit:.2f}")

    def _generate_goal(self, initial_intent: str, loop_id: str = "system") -> GoalSchema:
        """Query memory and LLM to decide the next objective."""
        history = self.storage.sqlite.get_recent_history(limit=20)
        history_text = "\n".join([f"- Goal: {h['goal']} | Score: {h['score']}" for h in history])
        
        similar = self.storage.vector.search_similar(initial_intent, limit=3)
        similar_text = ""
        if similar:
            similar_text = "\nPast artifacts found in vector memory:\n" + "\n".join(
                [f"- {s.get('payload', {}).get('goal', 'N/A')}" for s in similar]
            )
        
        user_prompt = f"Ambient Intent: {initial_intent}\n\nRecent History:\n{history_text}{similar_text}\n\nDecision: Generate the next goal."
        system_prompt = self.templates.load("goal_generation_system", "Generate a new and distinct goal for the autonomous agent.")
        
        # Use structured chat for goal generation
        try:
            goal = self.goal_gen_llm.chat_structured(
                messages=[{"role": "user", "content": user_prompt}],
                response_model=GoalSchema,
                system=system_prompt,
                loop_id=loop_id,
                agent_type="goal_gen"
            )

            if self.deduplicator.is_duplicate(goal.title, [h["goal"] for h in history]):
                log.info(f"Goal '{goal.title}' is a duplicate, but using it as fallback for now or retrying logic could be added here.")
            
            return goal
        except Exception as e:
            log.error(f"Goal generation failed: {e}")
            return GoalSchema(
                title="Explore the system",
                success_criteria=["Any file is created"],
                output_path="output.txt"
            )

    def run_iteration(self, initial_intent: str = "Establish existence and evolve."):
        loop_id, goal = self._prepare_iteration(initial_intent)
        
        try:
            self.storage.sandbox.start()
            messages, final_result = self._execute_loop(loop_id, goal)
            loop_data = self._finalize_iteration(loop_id, goal, messages, final_result)
            return loop_data

        except Exception as e:
            log.error(f"Loop {loop_id} failed: {e}")
            self.storage.sqlite.save_loop({
                "id": loop_id,
                "goal": goal.title,
                "goal_detail": goal.model_dump(),
                "status": "failed",
                "error": str(e)
            })
            raise
        finally:
            self.storage.sandbox.stop()

    def _prepare_iteration(self, initial_intent: str) -> tuple[str, GoalSchema]:
        self._check_safety()
        loop_id = str(uuid.uuid4())
        goal = self._generate_goal(initial_intent, loop_id=loop_id)
        log.info(f"Starting loop {loop_id} with goal: {goal.title}")

        self.storage.sqlite.save_loop({
            "id": loop_id,
            "goal": goal.title,
            "goal_detail": goal.model_dump(),
            "status": "running"
        })
        return loop_id, goal

    def _execute_loop(self, loop_id: str, goal: GoalSchema) -> tuple[List[Dict], str]:
        user_content = (
            f"Goal: {goal.title}\n"
            f"Success Criteria:\n" + "\n".join(f"- {c}" for c in goal.success_criteria) +
            f"\nOutput must be saved to: {goal.output_path}"
        )
        messages = [{"role": "user", "content": user_content}]
        system_prompt = self.templates.load("producer_system", "Execute the goal.")
        
        last_failed = self.storage.sqlite.get_recent_history(limit=5)
        lessons = [f"- Previous Goal '{h['goal']}' failed: {h.get('score_breakdown', {}).get('reasoning', 'No detail')}" 
                   for h in last_failed if h['score'] is not None and h['score'] < 0.3]
        if lessons:
            system_prompt += "\n\nCRITICAL LESSONS FROM RECENT FAILURES (DO NOT REPEAT THESE MISTAKES):\n" + "\n".join(lessons[:2])
            log.info(f"Injected {len(lessons[:2])} failure lessons into system prompt.")

        final_result = ""
        current_settings = settings.load()
        max_steps = current_settings.max_steps
        error_limit = current_settings.consecutive_error_limit
        max_output = current_settings.max_output_truncation

        consecutive_errors = 0
        total_tokens = 0
        for step in range(max_steps):
            if step > 0:
                time.sleep(current_settings.rate_limit_delay)

            response = self.producer_llm.chat(
                system=system_prompt,
                messages=messages,
                loop_id=loop_id,
                agent_type="producer"
            )
            
            usage = getattr(response, 'usage', None)
            if usage:
                total_tokens += usage.total_tokens
                if not self.producer_llm.validate_token_limit(total_tokens):
                    log.warning(f"Loop {loop_id} exceeded token limit ({total_tokens} tokens). Aborting.")
                    final_result = f"Loop aborted: Exceeded token limit."
                    break

            msg = response.choices[0].message
            messages.append(msg.model_dump())

            if msg.tool_calls:
                final_result, consecutive_errors = self._handle_tool_calls(
                    msg.tool_calls, messages, consecutive_errors, error_limit, max_output
                )
                if consecutive_errors >= error_limit or final_result.startswith("TASK_COMPLETE:"):
                    break
            else:
                final_result = msg.content or "Completed."
                break
        
        if not final_result:
            final_result = f"Loop reached max steps ({max_steps}) without a final response."
            
        return messages, final_result

    def _handle_tool_calls(self, tool_calls, messages, consecutive_errors, error_limit, max_output) -> tuple[str, int]:
        final_result = ""
        for tool_call in tool_calls:
            name = tool_call.function.name
            raw_args = tool_call.function.arguments
            log.info(f"[Tool] {name}({str(raw_args)[:100]}...)")
            
            tool = self.storage.tool_registry.get(name)
            try:
                from .utils import repair_json
                repaired_args = repair_json(raw_args)
                args = json.loads(repaired_args)
                result = tool.execute(args) if tool else f"Error: Tool {name} not found."
            except Exception as e:
                log.error(f"Tool execution failed for {name}: {e}")
                result = f"Error: {e}"

            if result.startswith("TASK_COMPLETE:"):
                final_result = result

            if len(result) > max_output:
                result = result[:max_output] + "... (truncated)"
            
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "name": name,
                "content": result
            })
            
            if "Error" in result:
                consecutive_errors += 1
            else:
                consecutive_errors = 0
                
            if consecutive_errors >= error_limit:
                final_result = f"Loop aborted due to errors."
                break
        return final_result, consecutive_errors

    def _finalize_iteration(self, loop_id: str, goal: GoalSchema, messages: List[Dict], final_result: str) -> Dict:
        evaluation = self.critic_agent.evaluate(
            goal=goal,
            artifact_path=goal.output_path,
            sandbox=self.storage.sandbox,
            loop_id=loop_id
        )
        
        loop_data = {
            "id": loop_id,
            "goal": goal.title,
            "goal_detail": goal.model_dump(),
            "status": "completed",
            "score": evaluation.get("overall_score", 0.0),
            "score_breakdown": evaluation.get("breakdown", {}),
            "criteria_met": evaluation.get("criteria_met", []),
            "result": final_result,
            "messages": messages
        }

        if evaluation.get("failed"):
            loop_data["status"] = "failed"
            loop_data["error"] = evaluation.get("reasoning")

        self.storage.sqlite.save_loop(loop_data)
        self.storage.vector.embed_and_store(final_result, {"loop_id": loop_id, "goal": goal.title})
        return loop_data

    def explain_loop(self, loop_id: str) -> str:
        loop = self.storage.sqlite.get_loop(loop_id)
        if not loop:
            return f"Error: Loop {loop_id} not found."

        history_text = "\n".join([f"{m['role']}: {str(m.get('content'))[:500]}..." for m in loop.get("messages", []) if m['role'] != 'system'])
        prompt = f"Please explain this agent loop:\nGoal: {loop.get('goal')}\nResult: {loop.get('result')}\nScore: {loop.get('score')}\nHistory:\n{history_text}"

        try:
            response = self.producer_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system="You are a technical analyst.",
                loop_id=loop_id,
                agent_type="explainer"
            )
            return response.choices[0].message.content or "No explanation."
        except Exception as e:
            return f"Error: {e}"
