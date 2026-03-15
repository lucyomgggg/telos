import os
import json
import uuid
import time
from datetime import datetime, timezone
from typing import List, Optional, Any, Dict, Tuple

from .config import settings, TELOS_HOME
from .logger import get_logger
from .memory import MemoryStore, VectorStore
from .llm import LLMService
from .usage import CostTracker
from .schemas import GoalSchema
from .interfaces import Tool, Critic, TemplateLoader
from .agents import BaseAgent
from .tools import ToolRegistry

log = get_logger("core")

class GoalGenerator(BaseAgent):
    def __init__(self, cost_tracker: Any = None):
        super().__init__(agent_type="goal_gen", cost_tracker=cost_tracker)
        from .deduplicator import GoalDeduplicator
        self.deduplicator = GoalDeduplicator()

    def generate(self, initial_intent: str, history: List[Dict], similar: List[Dict]) -> GoalSchema:
        history_text = "\n".join([f"- Goal: {h['goal']} | Score: {h['score']}" for h in history])
        similar_text = ""
        if similar:
            similar_text = "\nPast artifacts found in vector memory:\n" + "\n".join(
                [f"- {s.get('payload', {}).get('goal', 'N/A')}" for s in similar]
            )
        
        user_prompt = f"Ambient Intent: {initial_intent}\n\nRecent History:\n{history_text}{similar_text}\n\nDecision: Generate the next goal."
        system_prompt = self.load_template("goal_generation_system", "Generate a new and distinct goal.")
        
        try:
            goal = self.chat_structured(
                messages=[{"role": "user", "content": user_prompt}],
                response_model=GoalSchema,
                system=system_prompt
            )
            return goal
        except Exception as e:
            log.error(f"Goal generation failed: {e}")
            return GoalSchema(
                title="Explore the system",
                success_criteria=["Any file is created"],
                output_path="output.txt"
            )

class ProducerAgent(BaseAgent):
    def __init__(self, cost_tracker: Any = None):
        super().__init__(agent_type="producer", cost_tracker=cost_tracker)

    def execute_goal(self, loop_id: str, goal: GoalSchema, registry: ToolRegistry, lessons: List[str]) -> Tuple[List[Dict], str]:
        user_content = (
            f"Goal: {goal.title}\n"
            f"Success Criteria:\n" + "\n".join(f"- {c}" for c in goal.success_criteria) +
            f"\nOutput must be saved to: {goal.output_path}"
        )
        messages = [{"role": "user", "content": user_content}]
        system_prompt = self.load_template("producer_system", "Execute the goal.")
        
        if lessons:
            system_prompt += "\n\nCRITICAL LESSONS FROM RECENT FAILURES:\n" + "\n".join(lessons[:2])

        final_result = ""
        consecutive_errors = 0
        total_tokens = 0
        
        for step in range(self.settings.max_steps):
            if step > 0:
                time.sleep(self.settings.rate_limit_delay)

            response = self.chat(
                system=system_prompt,
                messages=messages,
                loop_id=loop_id
            )
            
            usage = getattr(response, 'usage', None)
            if usage:
                total_tokens += usage.total_tokens
                if not self.llm.validate_token_limit(total_tokens):
                    log.warning(f"Loop {loop_id} exceeded token limit. Aborting.")
                    final_result = "Loop aborted: Exceeded token limit."
                    break

            msg = response.choices[0].message
            messages.append(msg.model_dump())

            if msg.tool_calls:
                final_result, consecutive_errors = self._handle_tool_calls(
                    msg.tool_calls, messages, registry, consecutive_errors
                )
                if consecutive_errors >= self.settings.consecutive_error_limit or final_result.startswith("TASK_COMPLETE:"):
                    break
            else:
                final_result = msg.content or "Completed."
                break
        
        return messages, final_result or "Loop reached max steps."

    def _handle_tool_calls(self, tool_calls, messages, registry, consecutive_errors) -> Tuple[str, int]:
        final_result = ""
        for tool_call in tool_calls:
            name = tool_call.function.name
            raw_args = tool_call.function.arguments
            tool = registry.get(name)
            
            try:
                from .utils import repair_json
                args = json.loads(repair_json(raw_args))
                result = tool.execute(args) if tool else f"Error: Tool {name} not found."
            except Exception as e:
                result = f"Error: {e}"

            if result.startswith("TASK_COMPLETE:"):
                final_result = result

            limit = self.settings.max_output_truncation
            if len(result) > limit:
                result = result[:limit] + "... (truncated)"
            
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": name, "content": result})
            consecutive_errors = (consecutive_errors + 1) if "Error" in result else 0
            
            if consecutive_errors >= self.settings.consecutive_error_limit:
                final_result = "Loop aborted due to errors."
                break
        return final_result, consecutive_errors

class Orchestrator:
    def __init__(self):
        from .sandbox import SandboxManager
        from .critic import CriticAgent
        
        self.sqlite = MemoryStore()
        self.vector = VectorStore()
        self.cost_tracker = CostTracker(self.sqlite)
        self.sandbox = SandboxManager()
        self.registry = ToolRegistry(self.sandbox)
        
        self.goal_gen = GoalGenerator(self.cost_tracker)
        self.producer = ProducerAgent(self.cost_tracker)
        self.critic = CriticAgent(cost_tracker=self.cost_tracker)

    def run_iteration(self, intent: str = "Establish existence and evolve.") -> Dict:
        loop_id = str(uuid.uuid4())
        try:
            self._check_safety()
            self.sandbox.start()
            
            # 1. Goal Generation
            history = self.sqlite.get_recent_history(limit=20)
            similar = self.vector.search_similar(intent, limit=3)
            goal = self.goal_gen.generate(intent, history, similar)
            
            self.sqlite.save_loop({"id": loop_id, "goal": goal.title, "status": "running"})
            log.info(f"Starting loop {loop_id}: {goal.title}")

            # 2. Execution
            lessons = [f"Goal '{h['goal']}' failed: {h.get('reasoning', 'No detail')}" 
                       for h in history if h['score'] is not None and h['score'] < 0.3]
            
            messages, result = self.producer.execute_goal(loop_id, goal, self.registry, lessons)

            # 3. Evaluation
            eval_res = self.critic.evaluate(goal, goal.output_path, self.sandbox, loop_id)
            
            # 4. Finalize
            loop_data = {
                "id": loop_id,
                "goal": goal.title,
                "goal_detail": goal.model_dump(),
                "status": "completed" if not eval_res.get("failed") else "failed",
                "score": eval_res.get("overall_score", 0.0),
                "score_breakdown": eval_res.get("breakdown", {}),
                "reasoning": eval_res.get("reasoning", ""),
                "result": result,
                "messages": messages
            }
            self.sqlite.save_loop(loop_data)
            self.vector.embed_and_store(result, {"loop_id": loop_id, "goal": goal.title})
            return loop_data

        finally:
            self.sandbox.stop()

    def _check_safety(self):
        curr = settings.load()
        if self.cost_tracker.get_daily_loop_count() >= curr.daily_loop_limit:
            raise RuntimeError("Daily loop limit reached.")
        if self.cost_tracker.get_monthly_cost() >= curr.monthly_cost_limit:
            raise RuntimeError("Monthly budget exceeded.")

class AgentLoop(Orchestrator):
    """Legacy wrapper for CLI compatibility."""
    def explain_loop(self, loop_id: str) -> str:
        loop = self.sqlite.get_loop(loop_id)
        if not loop: return "Loop not found."
        prompt = f"Explain this loop:\nGoal: {loop['goal']}\nResult: {loop['result']}"
        resp = self.producer.chat(messages=[{"role": "user", "content": prompt}], system="You are an analyst.")
        return resp.choices[0].message.content or "No explanation."
