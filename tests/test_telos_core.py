import pytest
from unittest.mock import MagicMock, patch
from src.telos.telos_core import CostTracker, AgentLoop, Storage
from src.telos.db_models import AuditLog, LoopRecord

@pytest.fixture
def mock_storage():
    storage = MagicMock(spec=Storage)
    storage.sqlite = MagicMock()
    storage.vector = MagicMock()
    storage.sandbox = MagicMock()
    storage.tool_registry = {}
    return storage

def test_cost_tracker_record_usage(mock_storage):
    tracker = CostTracker(mock_storage.sqlite)
    
    mock_response = MagicMock()
    mock_response.usage.total_tokens = 100
    mock_response.model = "test-model"
    
    with patch("litellm.completion_cost", return_value=0.01):
        tracker.record_usage(mock_response, "producer", "loop-1")
    
    # Verify session interactions
    session = mock_storage.sqlite.Session.return_value
    session.add.assert_called_once()
    args, _ = session.add.call_args
    assert isinstance(args[0], AuditLog)
    assert args[0].agent_type == "producer"
    assert args[0].cost_usd == 0.01

def test_agent_loop_safety_check(mock_storage):
    loop = AgentLoop(storage=mock_storage)
    loop.daily_limit = 10
    loop.monthly_limit = 50.0
    
    with patch.object(loop.cost_tracker, "get_daily_loop_count", return_value=11):
        with pytest.raises(RuntimeError, match="Daily loop limit reached"):
            loop._check_safety()

    with patch.object(loop.cost_tracker, "get_daily_loop_count", return_value=0), \
         patch.object(loop.cost_tracker, "get_monthly_cost", return_value=51.0):
        with pytest.raises(RuntimeError, match="Monthly cost limit reached"):
            loop._check_safety()

@patch("litellm.completion")
@patch("litellm.completion_cost")
def test_run_iteration(mock_cost, mock_completion, mock_storage):
    mock_msg = MagicMock()
    mock_msg.content = "Goal: X"
    mock_msg.tool_calls = None
    
    mock_completion.return_value = MagicMock(
        choices=[MagicMock(message=mock_msg)],
        usage=MagicMock(total_tokens=50),
        model="gemini-2.0-flash"
    )
    mock_cost.return_value = 0.001
    
    loop = AgentLoop(storage=mock_storage)
    loop.run_iteration("Test intent")
    
    assert mock_completion.call_count == 3 # Goal Gen + Producer + Critic
    mock_storage.sqlite.save_loop.assert_called_once()
    mock_storage.vector.embed_and_store.assert_called_once()
