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
            return [{"goal": r.goal, "score": r.score} for r in records][::-1]  # Return in chronological order
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


# --- Vector Store (Qdrant) ---
class VectorStore:
    def __init__(self, collection_name: str = "telos_artifacts"):
        self.collection_name = collection_name
        self.vector_size = 1536
        self.available = False
        self.client = None

        from .config import settings
        self.settings = settings.load()
        qdrant_url = self.settings.memory.qdrant_url
        try:
            self.client = QdrantClient(url=qdrant_url, timeout=5)
            self._ensure_collection()
            self.available = True
            log.info("Qdrant connected at %s", qdrant_url)
        except Exception as e:
            log.warning("Qdrant unavailable, vector search disabled: %s", e)

    def _ensure_collection(self):
        try:
            collections = [c.name for c in self.client.get_collections().collections]
            if self.collection_name not in collections:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
                )
                log.info("Created Qdrant collection: %s", self.collection_name)
        except Exception as e:
            log.warning("Could not ensure Qdrant collection: %s", e)
            self.available = False

    def embed_and_store(self, text: str, metadata: dict = None) -> str:
        """Store semantic meaning of an artifact. Returns point ID or None."""
        if not self.available:
            return None

        from litellm import embedding
        
        point_id = str(uuid.uuid4())
        try:
            model = settings.llm.embedding_model
            response = embedding(model=model, input=[text])
            vectors = response.data[0]['embedding']
            
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

        from litellm import embedding
        
        try:
            model = settings.llm.embedding_model
            response = embedding(model=model, input=[query])
            query_vector = response.data[0]['embedding']
            
            results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit
            )
            return [{"id": r.id, "score": r.score, "payload": r.payload} for r in results]
        except Exception as e:
            log.warning("Failed to search vector store: %s", e)
            return []
