from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Integer, DateTime, JSON, Text, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class SessionRecord(Base):
    __tablename__ = 'sessions'

    id = Column(String, primary_key=True)
    name = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    completed_at = Column(DateTime, nullable=True)
    producer_model = Column(String, nullable=True)
    critic_model = Column(String, nullable=True)
    goal_gen_model = Column(String, nullable=True)
    intended_loops = Column(Integer, default=0)
    completed_loops = Column(Integer, default=0)
    status = Column(String, default="running")   # running, completed, failed
    total_cost_usd = Column(Float, default=0.0)
    avg_score = Column(Float, nullable=True)

    __table_args__ = (Index('ix_sessions_created_at', 'created_at'),)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "producer_model": self.producer_model,
            "critic_model": self.critic_model,
            "goal_gen_model": self.goal_gen_model,
            "intended_loops": self.intended_loops,
            "completed_loops": self.completed_loops,
            "status": self.status,
            "total_cost_usd": self.total_cost_usd,
            "avg_score": self.avg_score,
        }

class LoopRecord(Base):
    __tablename__ = 'loops'

    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    goal = Column(String, nullable=False)
    goal_detail = Column(JSON, nullable=True)
    output_path = Column(String, nullable=True)
    score = Column(Float, nullable=True)
    score_breakdown = Column(JSON, nullable=True)
    reasoning = Column(String, nullable=True)
    tokens_used = Column(Integer, default=0)
    cost_usd = Column(Float, default=0.0)
    status = Column(String, default="running")         # running, completed, failed, timeout
    error = Column(String, nullable=True)
    result = Column(Text, nullable=True)               # The final text artifact
    criteria_met = Column(JSON, nullable=True)
    messages = Column(JSON, nullable=True)             # Full interaction trace (for 'explain')
    session_id = Column(String, nullable=True)

    __table_args__ = (Index('ix_loops_created_at', 'created_at'),)

    def to_dict(self):
        return {
            "id": self.id,
            "created_at": self.created_at.isoformat(),
            "goal": self.goal,
            "output_path": self.output_path,
            "score": self.score,
            "score_breakdown": self.score_breakdown,
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
            "status": self.status,
            "error": self.error,
            "criteria_met": self.criteria_met,
            "reasoning": self.reasoning,
            "result": self.result,
            "session_id": self.session_id,
        }

class AuditLog(Base):
    __tablename__ = 'audit_log'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    agent_type = Column(String)  # producer, critic, or other
    model = Column(String)
    tokens_used = Column(Integer)
    cost_usd = Column(Float)
    loop_id = Column(String)

    __table_args__ = (
        Index('ix_audit_log_loop_id', 'loop_id'),
        Index('ix_audit_log_timestamp', 'timestamp'),
    )
