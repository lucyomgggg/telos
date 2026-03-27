import pytest
from unittest.mock import patch
from src.telos.telos_core import AgentLoop


class TestAgentLoopExecution:
    """Test AgentLoop run_iteration orchestration."""

    def test_run_iteration_saves_loop_on_failure(self):
        """If an exception occurs mid-run, LoopRecord should be saved as 'failed'."""
        with patch("src.telos.telos_core.MemoryStore"), \
             patch("src.telos.telos_core.VectorStore"), \
             patch("src.telos.telos_core.SandboxManager"), \
             patch("src.telos.telos_core.ToolRegistry"), \
             patch("src.telos.telos_core.GoalGenerator"), \
             patch("src.telos.telos_core.ProducerAgent"):

            loop = AgentLoop()
            mock_sqlite = loop.sqlite

            # get_loop returns a record (simulating loop was saved as "running")
            mock_sqlite.get_loop.return_value = {"id": "abc", "status": "running"}

            # Raise after the loop record is created (during producer execution)
            loop.producer.execute_goal.side_effect = RuntimeError("producer boom")

            with pytest.raises(RuntimeError, match="producer boom"):
                loop.run_iteration()

            # LoopRecord should have been updated to "failed"
            save_calls = mock_sqlite.save_loop.call_args_list
            failed_call = next(
                (c for c in save_calls if c.args[0].get("status") == "failed"),
                None
            )
            assert failed_call is not None, "Expected save_loop to be called with status='failed'"

    def test_run_iteration_shutdown_preserves_workspace(self, tmp_path):
        """shutdown() should NOT delete the persistent workspace."""
        with patch("src.telos.telos_core.MemoryStore"), \
             patch("src.telos.telos_core.VectorStore"), \
             patch("src.telos.telos_core.SandboxManager"), \
             patch("src.telos.telos_core.ToolRegistry"), \
             patch("src.telos.telos_core.GoalGenerator"), \
             patch("src.telos.telos_core.ProducerAgent"):

            loop = AgentLoop()
            # Point workspace to tmp_path so we can verify preservation
            loop.sandbox.local_workspace = tmp_path / "persistent"
            loop.sandbox.local_workspace.mkdir()
            (loop.sandbox.local_workspace / "artifact.txt").write_text("hello")

            loop.shutdown()

            assert loop.sandbox.local_workspace.exists(), "shutdown() must preserve the workspace"