import os
import uuid
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from .config import TELOS_HOME, settings
from .db_models import Base, LoopRecord
from .logger import get_logger

log = get_logger("memory")

# Known embedding model dimensions. Used when embedding_dimensions is not set in config.
_KNOWN_DIMENSIONS: dict = {
    "all-MiniLM-L6-v2": 384,
    "all-mpnet-base-v2": 768,
    "nomic-embed-text": 768,
    "text-embedding-ada-002": 1536,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "openai/text-embedding-ada-002": 1536,
    "openai/text-embedding-3-small": 1536,
    "openai/text-embedding-3-large": 3072,
}

# --- Memory Store (SQLite) ---
class MemoryStore:
    def __init__(self, db_path: str = None):
        if db_path:
            self.db_url = f"sqlite:///{db_path}"
        else:
            self.db_url = f"sqlite:///{TELOS_HOME}/telos.db"

        self.engine = create_engine(self.db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        log.debug("MemoryStore initialized: %s", self.db_url)

    def save_loop(self, loop_data: dict) -> dict:
        """Create or update a LoopRecord in SQLite."""
        session = self.Session()
        try:
            record = session.query(LoopRecord).filter_by(id=loop_data.get('id')).first()
            if not record:
                record = LoopRecord(**loop_data)
                session.add(record)
            else:
                for key, value in loop_data.items():
                    setattr(record, key, value)
            session.commit()
            log.debug("Saved loop %s (status=%s)", loop_data.get('id', '?'), loop_data.get('status', '?'))
            return loop_data
        except Exception as e:
            session.rollback()
            log.error("Error saving to MemoryStore: %s", e)
            raise
        finally:
            session.close()

    def get_loop(self, loop_id: str) -> dict:
        session = self.Session()
        try:
            record = session.query(LoopRecord).filter_by(id=loop_id).first()
            return record.to_dict() if record else None
        finally:
            session.close()

    def list_loops(self, limit: int = 10, offset: int = 0) -> list[dict]:
        session = self.Session()
        try:
            records = session.query(LoopRecord).order_by(LoopRecord.created_at.desc()).limit(limit).offset(offset).all()
            return [r.to_dict() for r in records]
        finally:
            session.close()
    def get_recent_history(self, limit: int = 20) -> list[dict]:
        """Fetch the last N goals and their scores for loop context."""
        session = self.Session()
        try:
            records = session.query(LoopRecord).order_by(LoopRecord.created_at.desc()).limit(limit).all()
            return [{"goal": r.goal, "score": r.score, "reasoning": r.reasoning} for r in records][::-1]  # Return in chronological order
        finally:
            session.close()

    def count_loops(self) -> int:
        """Return the total number of completed loop records."""
        from sqlalchemy import func
        session = self.Session()
        try:
            return int(session.query(func.count(LoopRecord.id)).scalar() or 0)
        finally:
            session.close()

    def get_quality_history(self, top_n: int = 10, recent_failures: int = 5, recent_n: int = 5) -> list[dict]:
        """Fetch a quality-weighted mix of history: high-scoring, recent failures, and recent loops.

        This replaces a naive 'last N' window with a signal-dense context:
        - top_n: loops with score >= 0.7 (success patterns)
        - recent_failures: most recent loops with score < 0.3 (what to avoid)
        - recent_n: most recent loops regardless of score (temporal continuity)
        """
        session = self.Session()
        try:
            top = (session.query(LoopRecord)
                   .filter(LoopRecord.score >= 0.7)
                   .order_by(LoopRecord.score.desc())
                   .limit(top_n).all())

            failures = (session.query(LoopRecord)
                        .filter(LoopRecord.score < 0.3)
                        .order_by(LoopRecord.created_at.desc())
                        .limit(recent_failures).all())

            recent = (session.query(LoopRecord)
                      .order_by(LoopRecord.created_at.desc())
                      .limit(recent_n).all())

            seen: set = set()
            merged: list = []
            for r in top + failures + recent:
                if r.id not in seen:
                    seen.add(r.id)
                    merged.append({
                        "goal": r.goal,
                        "score": r.score,
                        "reasoning": r.reasoning,
                        "error": r.error,
                        "tokens_used": r.tokens_used,
                    })
            return merged
        finally:
            session.close()

    def get_total_spend(self, since: datetime) -> float:
        """Calculate total USD spent since a given datetime."""
        from sqlalchemy import func
        session = self.Session()
        try:
            result = session.query(func.sum(LoopRecord.cost_usd)).filter(LoopRecord.created_at >= since).scalar()
            return float(result or 0.0)
        finally:
            session.close()

    def get_score_progression(self, limit: int = 50) -> list[dict]:
        """Return loops in chronological order with sequence numbers for charting."""
        session = self.Session()
        try:
            records = (session.query(LoopRecord)
                       .order_by(LoopRecord.created_at.asc())
                       .limit(limit).all())
            return [
                {"loop_number": i + 1, "id": r.id, "goal": r.goal,
                 "score": r.score if r.score is not None else 0.0,
                 "status": r.status, "created_at": r.created_at.isoformat()}
                for i, r in enumerate(records)
            ]
        finally:
            session.close()

    def get_goal_diversity(self, limit: int = 200) -> list[dict]:
        """Return goals with scores for diversity view."""
        session = self.Session()
        try:
            records = (session.query(LoopRecord)
                       .order_by(LoopRecord.created_at.desc())
                       .limit(limit).all())
            return [
                {"id": r.id, "goal": r.goal, "score": r.score,
                 "status": r.status, "created_at": r.created_at.isoformat(),
                 "success_criteria": (r.goal_detail or {}).get("success_criteria", [])}
                for r in records
            ]
        finally:
            session.close()

    def get_failure_improvement_pairs(
        self, failure_threshold: float = 0.3, min_delta: float = 0.2, limit: int = 10
    ) -> list[dict]:
        """Return consecutive pairs where a failure is followed by a score improvement."""
        session = self.Session()
        try:
            records = (session.query(LoopRecord)
                       .order_by(LoopRecord.created_at.asc()).all())
            pairs = []
            for i in range(len(records) - 1):
                curr, nxt = records[i], records[i + 1]
                if curr.score is None or nxt.score is None:
                    continue
                if curr.score <= failure_threshold and nxt.score >= curr.score + min_delta:
                    reasoning = curr.reasoning or curr.error or ""
                    lesson = (reasoning.split(".")[0][:120]) or "No reasoning recorded"
                    pairs.append({
                        "failure_loop_number": i + 1,
                        "failure": {
                            "id": curr.id, "goal": curr.goal, "score": curr.score,
                            "lesson": lesson, "reasoning": curr.reasoning or "",
                        },
                        "improvement": {
                            "id": nxt.id, "goal": nxt.goal, "score": nxt.score,
                            "reasoning": nxt.reasoning or "",
                        },
                        "score_delta": round(nxt.score - curr.score, 2),
                    })
            return pairs[:limit]
        finally:
            session.close()

    def get_model_cost_stats(self) -> list[dict]:
        """Aggregate AuditLog by model and agent_type for cost analysis."""
        from sqlalchemy import func, distinct
        from .db_models import AuditLog
        session = self.Session()
        try:
            rows = (session.query(
                        AuditLog.model,
                        AuditLog.agent_type,
                        func.count(distinct(AuditLog.loop_id)).label("loop_count"),
                        func.sum(AuditLog.cost_usd).label("total_cost"),
                        func.sum(AuditLog.tokens_used).label("total_tokens"),
                    )
                    .filter(AuditLog.loop_id != "system")
                    .group_by(AuditLog.model, AuditLog.agent_type)
                    .order_by(func.sum(AuditLog.cost_usd).desc())
                    .all())
            result = []
            for r in rows:
                lc = r.loop_count or 1
                result.append({
                    "model": r.model or "unknown",
                    "agent_type": r.agent_type or "unknown",
                    "loop_count": r.loop_count,
                    "total_cost": float(r.total_cost or 0.0),
                    "avg_cost_per_loop": float(r.total_cost or 0.0) / lc,
                    "total_tokens": int(r.total_tokens or 0),
                    "avg_tokens_per_loop": int(r.total_tokens or 0) // lc,
                })
            return result
        finally:
            session.close()

    def get_score_breakdown_averages(self) -> dict:
        """Average each rubric axis across all loops that have score_breakdown data."""
        session = self.Session()
        try:
            records = (session.query(LoopRecord.score_breakdown)
                       .filter(LoopRecord.score_breakdown.isnot(None)).all())
            sums: dict = {}
            counts: dict = {}
            for (breakdown,) in records:
                if not isinstance(breakdown, dict):
                    continue
                for axis, val in breakdown.items():
                    if isinstance(val, (int, float)):
                        sums[axis] = sums.get(axis, 0.0) + val
                        counts[axis] = counts.get(axis, 0) + 1
            return {axis: round(sums[axis] / counts[axis], 3) for axis in sums if counts[axis] > 0}
        finally:
            session.close()

    def get_dashboard_summary(self) -> dict:
        """Single-query header stats for the dashboard."""
        from sqlalchemy import func, case
        session = self.Session()
        try:
            total, avg_score, total_cost, high_count, fail_count = session.query(
                func.count(LoopRecord.id),
                func.avg(LoopRecord.score),
                func.sum(LoopRecord.cost_usd),
                func.sum(case((LoopRecord.score >= 0.7, 1), else_=0)),
                func.sum(case((LoopRecord.score <= 0.3, 1), else_=0)),
            ).one()
            total = int(total or 0)
            return {
                "total_loops": total,
                "avg_score": round(float(avg_score or 0.0), 3),
                "total_cost_usd": round(float(total_cost or 0.0), 6),
                "high_score_count": int(high_count or 0),
                "failure_count": int(fail_count or 0),
                "high_score_rate": round(int(high_count or 0) / total * 100, 1) if total > 0 else 0.0,
            }
        finally:
            session.close()


# --- Vector Store (Qdrant) ---
class VectorStore:
    def __init__(self, collection_name: str = None):
        self.available = False
        self.client = None
        self._local_model = None

        cfg = settings.load()
        self.embedding_model = cfg.memory.embedding_model
        self.collection_name = collection_name or cfg.memory.collection_name

        # Resolve vector dimensions: explicit config > known model dict > warn and default
        if cfg.memory.embedding_dimensions:
            self.vector_size = cfg.memory.embedding_dimensions
        elif self.embedding_model in _KNOWN_DIMENSIONS:
            self.vector_size = _KNOWN_DIMENSIONS[self.embedding_model]
        else:
            self.vector_size = 1536
            log.warning(
                "Unknown embedding model '%s': defaulting to vector_size=1536. "
                "Set memory.embedding_dimensions in config.yaml to suppress this.",
                self.embedding_model,
            )

        qdrant_url = cfg.memory.qdrant_url
        try:
            self.client = QdrantClient(url=qdrant_url, timeout=2)
            # Explicit ping to verify availability
            self.client.get_collections()
            self._ensure_collection()
            self.available = True
            log.info("Qdrant connected at %s with vector_size=%d", qdrant_url, self.vector_size)
        except Exception as e:
            log.warning("Qdrant unreachable, vector storage disabled (using silent fallback): %s", e)
            self.available = False

    def _ensure_collection(self):
        try:
            # Check if collection exists and has the correct vector size
            collections = [c.name for c in self.client.get_collections().collections]
            if self.collection_name in collections:
                info = self.client.get_collection(self.collection_name)
                # If vector size mismatch, we might need to recreate or use a different collection
                # For now, let's just log it. In a real system we might version the collection.
                existing_size = info.config.params.vectors.size
                if existing_size != self.vector_size:
                    log.warning("Collection vector size mismatch: existing=%d, requested=%d. Recreating...", existing_size, self.vector_size)
                    self.client.delete_collection(self.collection_name)
                    collections.remove(self.collection_name)

            if self.collection_name not in collections:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
                )
                log.info("Created Qdrant collection: %s (size=%d)", self.collection_name, self.vector_size)
        except Exception as e:
            log.warning("Could not ensure Qdrant collection: %s", e)
            self.available = False

    def _get_embedding(self, text: str) -> list[float]:
        if self.embedding_model == "all-MiniLM-L6-v2":
            if not self._local_model:
                from sentence_transformers import SentenceTransformer
                self._local_model = SentenceTransformer(self.embedding_model)
            return self._local_model.encode(text).tolist()
        else:
            from litellm import embedding
            response = embedding(model=self.embedding_model, input=[text])
            return response.data[0]['embedding']

    def embed_and_store(self, text: str, metadata: dict = None) -> str:
        """Store semantic meaning of an artifact. Returns point ID or None."""
        if not self.available:
            return None

        point_id = str(uuid.uuid4())
        try:
            vectors = self._get_embedding(text)
            
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(id=point_id, vector=vectors, payload=metadata or {})
                ]
            )
            log.debug("Stored embedding %s", point_id)
            return point_id
        except Exception as e:
            log.warning("Failed to embed and store: %s", e)
            return None

    def search_similar(self, query: str, limit: int = 3) -> list[dict]:
        """Find past artifacts matching the query."""
        if not self.available:
            return []
        
        try:
            query_vector = self._get_embedding(query)
            
            # Using query_points instead of search for qdrant-client v1.17.1+
            results = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit
            ).points
            return [{"id": r.id, "score": r.score, "payload": r.payload} for r in results]
        except Exception as e:
            log.warning("Failed to search vector store: %s", e)
            return []
