import os
import uuid
from datetime import datetime, timezone
from abc import ABC, abstractmethod
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field
import litellm
from sqlalchemy import func
from .config import settings, TELOS_HOME
from .logger import get_logger
from .memory import MemoryStore, VectorStore
from .db_models import AuditLog, LoopRecord

log = get_logger("core")

# --- Plugin Architecture (ABCs) ---

class Tool(ABC):
    @abstractmethod
    def execute(self, params: Dict[str, Any]) -> str:
        pass

    @property
    @abstractmethod
    def definition(self) -> Dict[str, Any]:
        """Returns the litellm-compatible tool definition."""
        pass

class Memory(ABC):
    @abstractmethod
    def store(self, key: str, value: Any):
        pass
    
    @abstractmethod
    def retrieve(self, key: str) -> Any:
        pass

class Critic(ABC):
    @abstractmethod
    def evaluate(self, result: str) -> Dict[str, Any]:
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
        model = response.model
        cost = litellm.completion_cost(response)
        
        session = self.memory_store.Session()
        try:
            entry = AuditLog(
                agent_type=agent_type,
                model=model,
                tokens_used=tokens,
                cost_usd=cost,
                loop_id=loop_id
            )
            session.add(entry)
            session.commit()
            log.info(f"Recorded cost: ${cost:.6f} for {agent_type} using {model}")
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
        from .config import TEMPLATES_DIR
        template_path = TEMPLATES_DIR / f"{template_name}.txt"
        if template_path.exists():
            return template_path.read_text().strip()
        log.warning(f"Template {template_name} not found at {template_path}. Using fallback.")
        return fallback_text

class Storage:
    def __init__(self):
        self.sqlite = MemoryStore()
        self.vector = VectorStore()
        from .sandbox import SandboxManager
        from .tools import BashTool, WriteFileTool, ReadFileTool
        self.sandbox = SandboxManager()
        self.tool_registry: Dict[str, Tool] = {
            "execute_command": BashTool(self.sandbox),
            "write_file": WriteFileTool(self.sandbox),
            "read_file": ReadFileTool(self.sandbox)
        }

# --- Agent Loop ---

class AgentLoop:
    def __init__(self, storage: Optional[Storage] = None):
        self.storage = storage or Storage()
        self.cost_tracker = CostTracker(self.storage.sqlite)
        self.daily_limit = settings.daily_loop_limit
        self.monthly_limit = settings.monthly_cost_limit
        self.templates = TemplateLoader()

    def _check_safety(self):
        daily_loops = self.cost_tracker.get_daily_loop_count()
        if daily_loops >= self.daily_limit:
            raise RuntimeError(f"Daily loop limit reached: {daily_loops}/{self.daily_limit}")
        
        monthly_cost = self.cost_tracker.get_monthly_cost()
        if monthly_cost >= self.monthly_limit:
            raise RuntimeError(f"Monthly cost limit reached: ${monthly_cost:.2f}/${self.monthly_limit:.2f}")

    def _generate_goal(self, initial_intent: str) -> str:
        """Query memory and LLM to decide the next objective."""
        past_loops = self.storage.sqlite.list_loops(limit=5)
        
        history_summary = []
        for loop in past_loops:
            status = loop['status']
            score = f"{loop['score']:.2f}" if loop['score'] is not None else "N/A"
            history_summary.append(f"- Goal: {loop['goal']} | Status: {status} | Score: {score}")

        history_text = "\n".join(history_summary)
        
        similar = self.storage.vector.search_similar(initial_intent, limit=3)
        similar_text = ""
        if similar:
            similar_text = "\nPast artifact summaries found in memory:\n" + "\n".join(
                [f"- {s.get('payload', {})}" for s in similar]
            )
        
        system_prompt = self.templates.load("goal_generation_system", "Generate a goal.")
        user_prompt = f"Intent: {initial_intent}\n\nPast loop history:\n{history_text}{similar_text}\n\nGenerate the next goal:"
        
        response = litellm.completion(
            model=settings.llm.producer_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        self.cost_tracker.record_usage(response, "producer", "pre-loop-id")
        return response.choices[0].message.content.strip()

    def start(self, loops: int = 1, initial_intent: str = "Explore and create"):
        """Run multiple autonomous iterations."""
        for i in range(loops):
            log.info(f"--- Global Iteration {i+1}/{loops} ---")
            try:
                self.run_iteration(initial_intent)
            except Exception as e:
                log.error(f"Iteration failed: {e}")
                break

    def run_iteration(self, initial_intent: str = "Explore the system and optimize performance"):
        self._check_safety()
        
        # 1. Goal Generation
        goal = self._generate_goal(initial_intent)
        loop_id = str(uuid.uuid4())
        log.info(f"Starting loop {loop_id} with goal: {goal}")
        
        # 2. Execution Phase (Multi-step Tool Calling)
        self.storage.sandbox.start()
        messages = [{"role": "user", "content": f"Achieve the following goal: {goal}"}]
        system_prompt = self.templates.load("producer_system", "Execute the goal.")
        tools = [t.definition for t in self.storage.tool_registry.values()]

        final_result = ""
        for step in range(10): # Max 10 steps
            response = litellm.completion(
                model=settings.llm.producer_model,
                messages=[{"role": "system", "content": system_prompt}] + messages,
                tools=tools,
                tool_choice="auto"
            )
            self.cost_tracker.record_usage(response, "producer", loop_id)
            
            msg = response.choices[0].message
            messages.append(msg)

            if msg.tool_calls:
                for tool_call in msg.tool_calls:
                    import json
                    name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    log.info(f"[Tool] {name}({args})")
                    
                    tool = self.storage.tool_registry.get(name)
                    if tool:
                        result = tool.execute(args)
                    else:
                        result = f"Error: Tool {name} not found."
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": name,
                        "content": result
                    })
            else:
                final_result = msg.content or "Task completed without text output."
                break
        
        # 3. Criticism
        critic_sys = self.templates.load("critic_system", "Evaluate the result.")
        critic_response = litellm.completion(
            model=settings.llm.critic_model,
            messages=[
                {"role": "system", "content": critic_sys},
                {"role": "user", "content": f"Result to evaluate: {final_result}"}
            ]
        )
        self.cost_tracker.record_usage(critic_response, "critic", loop_id)
        evaluation = critic_response.choices[0].message.content
        log.info(f"Critic Evaluation: {evaluation[:100]}...")

        # 4. Save to Memory
        self.storage.sqlite.save_loop({
            "id": loop_id,
            "goal": goal,
            "status": "completed",
            "score": 0.8, # TODO: Parse from critic output
            "cost_usd": 0.0 # TODO: Sum from AuditLog
        })
        self.storage.vector.embed_and_store(final_result, {"loop_id": loop_id, "goal": goal})
        
        self.storage.sandbox.stop()
        log.info(f"Loop {loop_id} finished.")
