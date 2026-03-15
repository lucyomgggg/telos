import pytest
import json
from src.telos.telos_core import AgentLoop

class TestAgentLoopExecution:
    """Test AgentLoop methods."""

    def test_finalize_minimal(self, mocker):
        # We need to mock storage and critic to test finalize_iteration
        storage = mocker.Mock()
        critic = mocker.Mock()
        critic.evaluate.return_value = {"overall_score": 0.8, "breakdown": {}}
        
        loop = AgentLoop(storage=storage, critic=critic)
        goal = mocker.Mock()
        goal.title = "Test Goal"
        goal.output_path = "test.txt"
        goal.model_dump.return_value = {}
        
        res = loop._finalize_iteration("test-id", goal, [], "Final result")
        assert res["status"] == "completed"
        assert res["score"] == 0.8
        storage.sqlite.save_loop.assert_called()
