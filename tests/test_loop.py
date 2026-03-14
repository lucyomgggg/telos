import pytest
from telos.loop import LoopState


class TestLoopState:
    def test_init_defaults(self):
        state = LoopState("Test objective")
        assert state.objective == "Test objective"
        assert state.status == "running"
        assert state.tokens_used == 0
        assert state.cost_usd == 0.0
        assert state.score is None
        assert state.score_breakdown is None
        assert state.output_path is None
        assert state.error_msg is None
        assert state.loop_id is not None

    def test_add_tokens(self):
        state = LoopState("Test")
        state.add_tokens(prompt=100, completion=50, cost=0.001)
        assert state.tokens_used == 150
        assert state.cost_usd == 0.001

    def test_add_tokens_accumulates(self):
        state = LoopState("Test")
        state.add_tokens(100, 50, 0.001)
        state.add_tokens(200, 100, 0.002)
        assert state.tokens_used == 450
        assert abs(state.cost_usd - 0.003) < 1e-9

    def test_unique_ids(self):
        s1 = LoopState("Goal 1")
        s2 = LoopState("Goal 2")
        assert s1.loop_id != s2.loop_id


class TestHandleToolCall:
    """Test tool call dispatching without needing a full AgentLoop."""

    def test_unknown_tool(self):
        from telos.loop import AgentLoop
        agent = AgentLoop.__new__(AgentLoop)  # Create without __init__
        result = agent._handle_tool_call("nonexistent_tool", {})
        assert result == "Unknown tool."
