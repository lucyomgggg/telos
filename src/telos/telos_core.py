import os
import json
import uuid
import time
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
import litellm
from sqlalchemy import func
from .config import settings, TELOS_HOME, PID_FILE, TEMPLATES_DIR
from .logger import get_logger
from .memory import MemoryStore, VectorStore
from .db_models import AuditLog, LoopRecord
from .utils import repair_json

log = get_logger("core")

# --- Plugin Architecture (ABCs) ---

class GoalSchema(BaseModel):
    # REDESIGN: 1
    title: str = Field(..., description="短い目標タイトル（30文字以内）")
    success_criteria: List[str] = Field(..., description="合否判定できる具体的な条件リスト")
    output_path: str = Field(..., description="成果物のファイルパス（例: solution.py）")

class Tool(ABC):
    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> str:
        """Execute the tool's core logic."""
        pass

    @property
    @abstractmethod
    def definition(self) -> Dict[str, Any]:
        """Returns the litellm-compatible tool definition."""
        pass

class Critic(ABC):
    @abstractmethod
    def evaluate(self, goal: "GoalSchema", artifact_path: str, sandbox=None, loop_id: str = "unknown") -> Dict[str, Any]:
        """Evaluate the result and return a score breakdown."""
        pass

# --- Cost Tracker ---

class CostTracker:
    def __init__(self, memory_store: MemoryStore):
        self.memory_store = memory_store

    def record_usage(self, response: Any, agent_type: str, loop_id: str):
        """Extract usage from litellm response and save to AuditLog."""
        usage = getattr(response, 'usage', None)
        if not usage:
            return

        tokens = usage.total_tokens
        model = getattr(response, 'model', 'unknown')
        try:
            cost = litellm.completion_cost(response) or 0.0
        except Exception as e:
            log.warning(f"Could not calculate cost for model {model}: {e}")
            cost = 0.0
        
        session = self.memory_store.Session()
        try:
            # Update AuditLog
            entry = AuditLog(
                agent_type=agent_type,
                model=model,
                tokens_used=tokens,
                cost_usd=cost,
                loop_id=loop_id
            )
            session.add(entry)
            
            # Update the LoopRecord's aggregate cost if it exists
            record = session.query(LoopRecord).filter_by(id=loop_id).first()
            if record:
                record.cost_usd += cost
                record.tokens_used += tokens
            
            session.commit()
            log.debug(f"Recorded cost: ${cost:.6f} for {agent_type} using {model}")
        except Exception as e:
            session.rollback()
            log.error(f"Failed to record cost: {e}")
        finally:
            session.close()

    def get_monthly_cost(self) -> float:
        session = self.memory_store.Session()
        try:
            now = datetime.now(timezone.utc)
            first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            cost = session.query(func.sum(AuditLog.cost_usd)).filter(AuditLog.timestamp >= first_day).scalar()
            return float(cost or 0.0)
        finally:
            session.close()

    def get_daily_loop_count(self) -> int:
        session = self.memory_store.Session()
        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            count = session.query(func.count(LoopRecord.id)).filter(LoopRecord.created_at >= today).scalar()
            return int(count or 0)
        finally:
            session.close()

# --- Template Loader ---

class TemplateLoader:
    @staticmethod
    def load(template_name: str, fallback_text: str = "") -> str:
        template_path = TEMPLATES_DIR / f"{template_name}.txt"
        if template_path.exists():
            return template_path.read_text().strip()
        log.warning(f"Template {template_name} not found. Using fallback.")
        return fallback_text

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
        from .llm import LLMInterface
        
        self.critic_agent = critic or CriticAgent(cost_tracker=self.cost_tracker) # FIX: 4
        self.deduplicator = GoalDeduplicator()
        
        # Pull model choices from settings
        current_settings = settings.load() 
        self.producer_model = current_settings.llm.producer_model
        self.critic_model = current_settings.llm.critic_model
        self.goal_gen_model = current_settings.llm.goal_gen_model or self.producer_model
        
        self.llm = LLMInterface(model=self.producer_model)
        self.critic_llm = LLMInterface(model=self.critic_model)
        self.goal_gen_llm = LLMInterface(model=self.goal_gen_model)

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

    def _generate_goal(self, initial_intent: str, loop_id: str = "system") -> GoalSchema: # REDESIGN: 1
        """Query memory and LLM to decide the next objective."""
        history = self.storage.sqlite.get_recent_history(limit=20)
        history_text = "\n".join([f"- Goal: {h['goal']} | Score: {h['score']}" for h in history])
        
        similar = self.storage.vector.search_similar(initial_intent, limit=3)
        similar_text = ""
        if similar:
            similar_text = "\nPast artifacts found in vector memory:\n" + "\n".join(
                [f"- {s.get('payload', {}).get('goal', 'N/A')}" for s in similar]
            )
        
        # TOOLCALL: Define tool for goal generation
        goal_tool = {
            "type": "function",
            "function": {
                "name": "set_goal",
                "description": "Set the next goal for the autonomous loop.",
                "parameters": GoalSchema.model_json_schema()
            }
        }
        user_prompt = f"Ambient Intent: {initial_intent}\n\nRecent History:\n{history_text}{similar_text}\n\nDecision: Generate the next goal."
        
        system_prompt = self.templates.load("goal_generation_system", "Generate a new and distinct goal for the autonomous agent.")
        for attempt in range(1, 4):
            try:
                # TOOLCALL: Use forced tool calling instead of json_object
                response = self.goal_gen_llm.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    tools=[goal_tool],
                    tool_choice={"type": "function", "function": {"name": "set_goal"}}
                )
                self.cost_tracker.record_usage(response, "goal_gen", loop_id) # FIX: 3
                
                # TOOLCALL: Parse from tool calling response
                try:
                    tool_call = response.choices[0].message.tool_calls[0]
                    raw_args = tool_call.function.arguments
                    try:
                        # Attempt repair before parsing
                        repaired_args = repair_json(raw_args)
                        goal_data = json.loads(repaired_args)
                        goal = GoalSchema(**goal_data)
                    except Exception as e:
                        log.error(f"Failed to parse or validate goal tool arguments (Attempt {attempt}): {e}")
                        log.debug(f"Raw arguments that failed: {raw_args}")
                        user_prompt += f"\nError in previous attempt: Your tool call generated invalid JSON. Please follow the schema strictly."
                        continue
                        
                except (AttributeError, IndexError) as e:
                    log.warning(f"Goal generation response didn't contain expected tool call format: {e}")
                    # Attempt to parse from content if tool_calls missing (fallback)
                    content = response.choices[0].message.content
                    if content:
                        try:
                            goal_data = json.loads(repair_json(content))
                            goal = GoalSchema(**goal_data)
                        except Exception:
                            continue
                    else:
                        continue

                if self.deduplicator.is_duplicate(goal.title, [h["goal"] for h in history]): # REDESIGN: 1
                    log.info(f"Goal '{goal.title}' is a duplicate, retrying...")
                    user_prompt += f"\nNote: '{goal.title}' is too similar to history: {[h['goal'] for h in history[-3:]]}. Be more distinct and creative."
                    continue
                
                return goal
            except Exception as e:
                log.error(f"Goal generation attempt {attempt} failed: {e}")
                time.sleep(2)
        
        # REDESIGN: 1
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
        
        # PROMPT ENHANCEMENT: Inject lessons learned from previous failure
        last_failed = self.storage.sqlite.get_recent_history(limit=5)
        lessons = [f"- Previous Goal '{h['goal']}' failed: {h.get('score_breakdown', {}).get('reasoning', 'No detail')}" 
                   for h in last_failed if h['score'] is not None and h['score'] < 0.3]
        if lessons:
            system_prompt += "\n\nCRITICAL LESSONS FROM RECENT FAILURES (DO NOT REPEAT THESE MISTAKES):\n" + "\n".join(lessons[:2])
            log.info(f"Injected {len(lessons[:2])} failure lessons into system prompt.")

        tools = [t.definition for t in self.storage.tool_registry.values()]

        final_result = ""
        current_settings = settings.load()
        rate_limit_delay = current_settings.rate_limit_delay
        max_steps = current_settings.max_steps
        error_limit = current_settings.consecutive_error_limit
        max_output = current_settings.max_output_truncation

        consecutive_errors = 0
        total_tokens = 0
        for step in range(max_steps):
            if step > 0:
                time.sleep(rate_limit_delay)

            response = self.llm.chat(
                system=system_prompt,
                messages=messages,
                tools=tools,
                tool_choice="auto"
            )
            self.cost_tracker.record_usage(response, "producer", loop_id)
            
            # Track tokens and enforce limit
            usage = getattr(response, 'usage', None)
            if usage:
                total_tokens += usage.total_tokens
                if not self.llm.validate_token_limit(total_tokens):
                    log.warning(f"Loop {loop_id} exceeded token limit ({total_tokens} tokens). Aborting.")
                    final_result = f"Loop aborted: Exceeded token limit of {settings.llm.max_tokens_per_loop} tokens."
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
        """Process a list of tool calls from the LLM."""
        final_result = ""
        for tool_call in tool_calls:
            name = tool_call.function.name
            raw_args = tool_call.function.arguments
            log.info(f"[Tool] {name}({str(raw_args)[:100]}...)")
            
            tool = self.storage.tool_registry.get(name)
            try:
                # TOOLCALL repair
                repaired_args = repair_json(raw_args)
                args = json.loads(repaired_args)
            except json.JSONDecodeError as e:
                log.error(f"Failed to decode tool arguments for {name}: {e}")
                log.debug(f"Raw malformed arguments: {raw_args}")
                result = f"Error: Malformed JSON arguments for tool {name}. Please ensure arguments match the schema exactly."
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": name,
                    "content": result
                })
                consecutive_errors += 1
                continue

            result = tool.execute(args) if tool else f"Error: Tool {name} not found."
            
            if result.startswith("TASK_COMPLETE:"):
                final_result = result
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "name": name,
                    "content": result
                })
                break
            
            if len(result) > max_output:
                log.info(f"Truncating long output for tool '{name}' ({len(result)} chars)")
                result = result[:max_output] + f"\n\n[... Output truncated from {len(result)} characters to {max_output} to save tokens ...]"
            
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
                log.warning(f"Aborting loop due to {error_limit} consecutive errors: {result}")
                final_result = f"Loop aborted due to {error_limit} consecutive errors: {result}"
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
        """Provide a narrative summary of what happened in a specific loop."""
        loop = self.storage.sqlite.get_loop(loop_id)
        if not loop:
            return f"Error: Loop {loop_id} not found."

        goal = loop.get("goal", "Unknown")
        result = loop.get("result", "(No result)")
        score = loop.get("score", 0.0)
        messages = loop.get("messages", [])

        # Construct a prompt for the explainer
        history_text = "\n".join([f"{m['role']}: {str(m.get('content'))[:500]}..." for m in messages if m['role'] != 'system'])
        
        prompt = (
            f"Please explain the following autonomous agent loop in a narrative, concise way.\n\n"
            f"Goal: {goal}\n"
            f"Final Result: {result}\n"
            f"Score: {score}/1.0\n\n"
            f"Interaction History:\n{history_text}\n\n"
            f"Explanation:"
        )

        try:
            response = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system="You are a technical analyst explaining an autonomous agent's actions."
            )
            self.cost_tracker.record_usage(response, "explainer", loop_id)
            return response.choices[0].message.content or "Could not generate explanation."
        except Exception as e:
            log.error(f"Failed to generate explanation: {e}")
            return f"Error generating narrative: {e}"
