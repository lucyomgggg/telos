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

log = get_logger("core")

# --- Plugin Architecture (ABCs) ---

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
    def evaluate(self, goal: str, result: str) -> Dict[str, Any]:
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
        cost = litellm.completion_cost(response) or 0.0
        
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
        from .tools import BashTool, WriteFileTool, ReadFileTool
        self.register_tool("execute_command", BashTool(self.sandbox))
        self.register_tool("write_file", WriteFileTool(self.sandbox))
        self.register_tool("read_file", ReadFileTool(self.sandbox))

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
        
        self.critic_agent = critic or CriticAgent()
        self.deduplicator = GoalDeduplicator()
        
        # Pull model choices from settings
        current_settings = settings.load() 
        self.producer_model = current_settings.llm.producer_model
        self.critic_model = current_settings.llm.critic_model
        
        self.llm = LLMInterface(model=self.producer_model)
        self.critic_llm = LLMInterface(model=self.critic_model)

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

    def _generate_goal(self, initial_intent: str) -> str:
        """Query memory and LLM to decide the next objective."""
        history = self.storage.sqlite.get_recent_history(limit=20)
        history_text = "\n".join([f"- Goal: {h['goal']} | Score: {h['score']}" for h in history])
        
        similar = self.storage.vector.search_similar(initial_intent, limit=3)
        similar_text = ""
        if similar:
            similar_text = "\nPast artifacts found in vector memory:\n" + "\n".join(
                [f"- {s.get('payload', {}).get('goal', 'N/A')}" for s in similar]
            )
        
        system_prompt = self.templates.load("goal_generation_system", 
            "You are the Goal Setting Agent. Generate a concise goal in JSON format: {'goal': '...'}")
        user_prompt = f"Ambient Intent: {initial_intent}\n\nRecent History:\n{history_text}{similar_text}\n\nDecision: Generate the next goal."
        
        for attempt in range(1, 4):
            try:
                response = self.llm.chat(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"}
                )
                self.cost_tracker.record_usage(response, "goal_gen", "system")
                
                content = response.choices[0].message.content or "{}"
                # Clean nested JSON strings if LLM returns them
                if "```json" in content:
                    content = content.split("```json")[1].split("```")[0].strip()
                
                goal_data = json.loads(content)
                new_goal = goal_data.get("goal", "Explore and create")
                
                past_goals = [h["goal"] for h in history]
                if self.deduplicator.is_duplicate(new_goal, past_goals):
                    user_prompt += f"\nNote: '{new_goal}' is too similar to history. Be more distinct."
                    continue
                
                return new_goal
            except Exception as e:
                log.error(f"Goal generation attempt {attempt} failed: {e}")
                time.sleep(2)
        
        return "Explore the system further."

    def run_iteration(self, initial_intent: str = "Explore the system"):
        self._check_safety()
        
        goal = self._generate_goal(initial_intent)
        loop_id = str(uuid.uuid4())
        log.info(f"Starting loop {loop_id} with goal: {goal}")

        self.storage.sqlite.save_loop({
            "id": loop_id,
            "goal": goal,
            "status": "running"
        })
        
        try:
            self.storage.sandbox.start()
            messages = [{"role": "user", "content": f"Achieve the following goal: {goal}"}]
            system_prompt = self.templates.load("producer_system", "Execute the goal.")
            tools = [t.definition for t in self.storage.tool_registry.values()]

            final_result = ""
            current_settings = settings.load()
            rate_limit_delay = current_settings.rate_limit_delay

            for step in range(10): # Max steps
                if step > 0:
                    time.sleep(rate_limit_delay)

                response = self.llm.chat(
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto"
                )
                self.cost_tracker.record_usage(response, "producer", loop_id)
                
                msg = response.choices[0].message
                messages.append(msg.model_dump())

                if msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        name = tool_call.function.name
                        args = json.loads(tool_call.function.arguments)
                        
                        log.info(f"[Tool] {name}({str(args)[:100]}...)")
                        
                        tool = self.storage.tool_registry.get(name)
                        result = tool.execute(args) if tool else f"Error: Tool {name} not found."
                        
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": name,
                            "content": result
                        })
                else:
                    final_result = msg.content or "Completed."
                    break
            
            evaluation = self.critic_agent.evaluate(goal, final_result)
            
            loop_data = {
                "id": loop_id,
                "goal": goal,
                "status": "completed",
                "score": evaluation.get("overall_score", 0.0),
                "score_breakdown": evaluation.get("breakdown", {}),
                "result": final_result,
                "messages": messages
            }
            self.storage.sqlite.save_loop(loop_data)
            self.storage.vector.embed_and_store(final_result, {"loop_id": loop_id, "goal": goal})
            return loop_data

        except Exception as e:
            log.error(f"Loop {loop_id} failed: {e}")
            self.storage.sqlite.save_loop({
                "id": loop_id,
                "goal": goal,
                "status": "failed",
                "error": str(e)
            })
            raise
        finally:
            self.storage.sandbox.stop()
