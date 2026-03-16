import pytest
import tempfile
from telos.memory import MemoryStore, VectorStore


class TestMemoryStore:
    @pytest.fixture
    def store(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        return MemoryStore(db_path=db_path)

    def test_save_and_get_loop(self, store):
        loop_data = {
            "id": "test-loop-001",
            "goal": "Write hello world",
            "output_path": None,
            "score": 0.85,
            "score_breakdown": {"completeness": 0.9, "coherence": 0.8, "novelty": 0.7},
            "tokens_used": 500,
            "cost_usd": 0.001,
            "status": "completed",
            "error": None
        }
        store.save_loop(loop_data)
        result = store.get_loop("test-loop-001")
        
        assert result is not None
        assert result["id"] == "test-loop-001"
        assert result["goal"] == "Write hello world"
        assert result["score"] == 0.85
        assert result["status"] == "completed"

    def test_update_existing_loop(self, store):
        loop_data = {
            "id": "test-loop-002",
            "goal": "Test goal",
            "status": "running",
            "tokens_used": 0,
            "cost_usd": 0.0,
            "score": None,
            "score_breakdown": None,
            "output_path": None,
            "error": None,
        }
        store.save_loop(loop_data)
        
        # Update
        loop_data["status"] = "completed"
        loop_data["score"] = 0.75
        store.save_loop(loop_data)
        
        result = store.get_loop("test-loop-002")
        assert result["status"] == "completed"
        assert result["score"] == 0.75

    def test_list_loops_ordering(self, store):
        for i in range(3):
            store.save_loop({
                "id": f"loop-{i}",
                "goal": f"Goal {i}",
                "status": "completed",
                "tokens_used": 0,
                "cost_usd": 0.0,
                "score": None,
                "score_breakdown": None,
                "output_path": None,
                "error": None,
            })
        
        loops = store.list_loops(limit=2)
        assert len(loops) == 2

    def test_list_loops_empty(self, store):
        loops = store.list_loops()
        assert loops == []

    def test_get_nonexistent_loop(self, store):
        result = store.get_loop("nonexistent")
        assert result is None


class TestVectorStore:
    def test_graceful_unavailable(self):
        """VectorStore should not crash when Qdrant is unavailable."""
        from unittest.mock import patch
        # Force QdrantClient to raise on ping so we test the fallback path
        with patch("telos.memory.QdrantClient") as MockClient:
            MockClient.return_value.get_collections.side_effect = ConnectionRefusedError("no qdrant")
            vs = VectorStore()

        assert vs.available is False

        # Operations should return gracefully
        assert vs.embed_and_store("test text") is None
        assert vs.search_similar("test query") == []
