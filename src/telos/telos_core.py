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

# --- Storage Layer ---

class Storage:
    def __init__(self):
        self.sqlite = MemoryStore()
        self.vector = VectorStore()

# --- Agent Loop ---

class AgentLoop:
    def __init__(self, storage: Optional[Storage] = None):
        self.storage = storage or Storage()
        self.cost_tracker = CostTracker(self.storage.sqlite)
        self.daily_limit = settings.daily_loop_limit
        self.monthly_limit = settings.monthly_cost_limit

    def _check_safety(self):
        daily_loops = self.cost_tracker.get_daily_loop_count()
        if daily_loops >= self.daily_limit:
            raise RuntimeError(f"Daily loop limit reached: {daily_loops}/{self.daily_limit}")
        
        monthly_cost = self.cost_tracker.get_monthly_cost()
        if monthly_cost >= self.monthly_limit:
            raise RuntimeError(f"Monthly cost limit reached: ${monthly_cost:.2f}/${self.monthly_limit:.2f}")

    def run_iteration(self, initial_intent: str = "Explore the system and optimize performance"):
        self._check_safety()
        
        loop_id = str(uuid.uuid4())
        log.info(f"Starting loop {loop_id}")
        
        # 1. Retrieve Memories
        context = self.storage.vector.search_similar(initial_intent, limit=5)
        context_str = "\n".join([str(c['payload']) for c in context])

        # 2. Goal Generation (Producer logic)
        # In a real impl, this would be a completion call.
        # Strict Separation: Critic doesn't see Producer's thought process.
        
        # Producer Execution
        producer_response = litellm.completion(
            model=settings.llm.producer_model,
            messages=[
                {"role": "system", "content": "You are the Producer agent. Generate a goal and execute it."},
                {"role": "user", "content": f"Context: {context_str}\n\nWhat is your goal?"}
            ]
        )
        self.cost_tracker.record_usage(producer_response, "producer", loop_id)
        result = producer_response.choices[0].message.content

        # 3. Criticism (Critic logic)
        critic_response = litellm.completion(
            model=settings.llm.critic_model,
            messages=[
                {"role": "system", "content": "You are the Critic agent. Evaluate the following result only. Do NOT look at the producer's thought process."},
                {"role": "user", "content": f"Result to evaluate: {result}"}
            ]
        )
        self.cost_tracker.record_usage(critic_response, "critic", loop_id)
        evaluation = critic_response.choices[0].message.content

        # 4. Save to Memory
        self.storage.sqlite.save_loop({
            "id": loop_id,
            "goal": initial_intent, # Placeholder or extracted goal
            "status": "completed",
            "score": 0.8, # Placeholder
            "cost_usd": litellm.completion_cost(producer_response) + litellm.completion_cost(critic_response)
        })
        self.storage.vector.embed_and_store(result, {"loop_id": loop_id, "type": "result"})

        log.info(f"Loop {loop_id} finished. Evaluation: {evaluation[:50]}...")
