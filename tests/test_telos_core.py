import pytest
from unittest.mock import MagicMock, patch
from src.telos.telos_core import CostTracker, AgentLoop
from src.telos.db_models import AuditLog, LoopRecord


@pytest.fixture
def mock_memory():
    mem = MagicMock()
    mem.Session.return_value = MagicMock()
    return mem


def test_cost_tracker_record_usage(mock_memory):
    tracker = CostTracker(mock_memory)

    mock_response = MagicMock()
    mock_response.usage.total_tokens = 100
    mock_response.model = "test-model"

    with patch("litellm.completion_cost", return_value=0.01):
        tracker.record_usage(mock_response, "producer", "loop-1")

    session = mock_memory.Session.return_value
    session.add.assert_called_once()
    args, _ = session.add.call_args
    assert isinstance(args[0], AuditLog)
    assert args[0].agent_type == "producer"
    assert args[0].cost_usd == 0.01


def test_agent_loop_safety_check_daily_limit():
    from src.telos.config import Settings
    mock_settings = Settings()
    mock_settings.daily_loop_limit = 10
    mock_settings.monthly_cost_limit = 50.0

    with patch("src.telos.telos_core.settings") as mock_settings_global, \
         patch("src.telos.telos_core.MemoryStore"), \
         patch("src.telos.telos_core.VectorStore"), \
         patch("src.telos.telos_core.SandboxManager"), \
         patch("src.telos.telos_core.ToolRegistry"), \
         patch("src.telos.telos_core.GoalGenerator"), \
         patch("src.telos.telos_core.ProducerAgent"), \
         patch("src.telos.telos_core.CriticAgent"):
        mock_settings_global.load.return_value = mock_settings
        loop = AgentLoop()

        with patch.object(loop.cost_tracker, "get_daily_loop_count", return_value=11), \
             patch.object(loop.cost_tracker, "get_monthly_cost", return_value=0.0):
            with pytest.raises(RuntimeError, match="Daily loop limit"):
                loop._check_safety()


def test_agent_loop_safety_check_monthly_cost():
    from src.telos.config import Settings
    mock_settings = Settings()
    mock_settings.daily_loop_limit = 10
    mock_settings.monthly_cost_limit = 50.0

    with patch("src.telos.telos_core.settings") as mock_settings_global, \
         patch("src.telos.telos_core.MemoryStore"), \
         patch("src.telos.telos_core.VectorStore"), \
         patch("src.telos.telos_core.SandboxManager"), \
         patch("src.telos.telos_core.ToolRegistry"), \
         patch("src.telos.telos_core.GoalGenerator"), \
         patch("src.telos.telos_core.ProducerAgent"), \
         patch("src.telos.telos_core.CriticAgent"):
        mock_settings_global.load.return_value = mock_settings
        loop = AgentLoop()

        with patch.object(loop.cost_tracker, "get_daily_loop_count", return_value=0), \
             patch.object(loop.cost_tracker, "get_monthly_cost", return_value=51.0):
            with pytest.raises(RuntimeError, match="Monthly budget"):
                loop._check_safety()


def test_shutdown_preserves_workspace():
    from src.telos.config import Settings
    import tempfile
    from pathlib import Path
    mock_settings = Settings()

    with patch("src.telos.telos_core.settings") as mock_settings_global, \
         patch("src.telos.telos_core.MemoryStore") as mock_memory_store, \
         patch("src.telos.telos_core.VectorStore"), \
         patch("src.telos.telos_core.SandboxManager") as mock_sandbox_cls, \
         patch("src.telos.telos_core.ToolRegistry"), \
         patch("src.telos.telos_core.GoalGenerator"), \
         patch("src.telos.telos_core.ProducerAgent"), \
         patch("src.telos.telos_core.CriticAgent"), \
         patch("src.telos.telos_core.JournalWriter"):
        mock_settings_global.load.return_value = mock_settings

        with tempfile.TemporaryDirectory() as tmpdir:
            ws = Path(tmpdir) / "persistent"
            ws.mkdir()
            mock_sandbox = MagicMock()
            mock_sandbox.local_workspace = ws
            mock_sandbox_cls.return_value = mock_sandbox

            sqlite_mock = MagicMock()
            sqlite_mock.list_loops_by_session.return_value = []
            mock_memory_store.return_value = sqlite_mock

            loop = AgentLoop()
            loop.sandbox = mock_sandbox

            sentinel = ws / "output.py"
            sentinel.write_text("# generated code")

            loop.shutdown()

            assert sentinel.exists(), "shutdown() must not delete the workspace"
