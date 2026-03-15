from typing import Any
import litellm
from datetime import datetime, timezone
from sqlalchemy import func
from .db_models import AuditLog, LoopRecord
from .memory import MemoryStore
from .logger import get_logger

log = get_logger("usage")

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
            # handle both litellm response object and direct usage
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
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            first_day = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            cost = session.query(func.sum(AuditLog.cost_usd)).filter(AuditLog.timestamp >= first_day).scalar()
            return float(cost or 0.0)
        finally:
            session.close()

    def get_daily_loop_count(self) -> int:
        session = self.memory_store.Session()
        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
            count = session.query(func.count(LoopRecord.id)).filter(LoopRecord.created_at >= today).scalar()
            return int(count or 0)
        finally:
            session.close()
