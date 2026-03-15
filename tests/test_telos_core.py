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
    from src.telos.config import Settings
    mock_settings = Settings()
    mock_settings.daily_loop_limit = 10
    mock_settings.monthly_cost_limit = 50.0
    
    with patch("src.telos.telos_core.settings") as mock_settings_base:
        mock_settings_base.load.return_value = mock_settings
        loop = AgentLoop(storage=mock_storage)
        
        with patch.object(loop.cost_tracker, "get_daily_loop_count", return_value=11):
            with pytest.raises(RuntimeError, match="Daily loop limit reached"):
                loop._check_safety()

        with patch.object(loop.cost_tracker, "get_daily_loop_count", return_value=0), \
             patch.object(loop.cost_tracker, "get_monthly_cost", return_value=51.0):
            with pytest.raises(RuntimeError, match="Monthly cost limit reached"):
                loop._check_safety()

@patch("src.telos.llm.completion")
@patch("litellm.completion_cost")
def test_run_iteration(mock_cost, mock_completion, mock_storage):
    # Goal Gen Response
    mock_msg_goal = MagicMock()
    mock_msg_goal.content = None
    mock_tool_call_goal = MagicMock()
    mock_tool_call_goal.function.name = "set_goal"
    mock_tool_call_goal.function.arguments = '{"title": "Test Goal", "success_criteria": ["Done"], "output_path": "test.txt"}'
    mock_msg_goal.tool_calls = [mock_tool_call_goal]
    
    # Producer Response
    mock_msg_prod = MagicMock()
    mock_msg_prod.content = "Producer done"
    mock_msg_prod.tool_calls = None

    mock_completion.side_effect = [
        MagicMock(choices=[MagicMock(message=mock_msg_goal)], usage=MagicMock(total_tokens=50), model="gemini-2.0-flash"),
        MagicMock(choices=[MagicMock(message=mock_msg_prod)], usage=MagicMock(total_tokens=50), model="gemini-2.0-flash")
    ]
    mock_cost.return_value = 0.001
    
    loop = AgentLoop(storage=mock_storage)
    with patch.object(loop.critic_agent, "evaluate", return_value={"overall_score": 0.8}):
        loop.run_iteration("Test intent")
    
    assert mock_completion.call_count == 2 # Goal Gen + Producer (Critic evaluate is mocked)
    assert mock_storage.sqlite.save_loop.call_count == 2 # Start and End
    mock_storage.vector.embed_and_store.assert_called_once()
