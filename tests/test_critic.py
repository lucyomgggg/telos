import pytest
import json
from pathlib import Path
from src.telos.critic import CriticAgent
from src.telos.telos_core import GoalSchema


class TestCriticAgent:
    @pytest.fixture
    def critic(self, tmp_path):
        rubric_path = tmp_path / "rubric.json"
        return CriticAgent(rubric_path=rubric_path)

    def test_default_rubric_created(self, critic, tmp_path):
        """Should create a default rubric (4 axes) if one doesn't exist."""
        rubric_path = tmp_path / "rubric.json"
        assert rubric_path.exists()

        with open(rubric_path) as f:
            rubric = json.load(f)

        assert "axes" in rubric
        assert len(rubric["axes"]) == 4

        names = [a["name"] for a in rubric["axes"]]
        assert "completeness" in names
        assert "coherence" in names
        assert "novelty" in names
        assert "performance" in names

    def test_rubric_weights_sum_to_one(self, critic):
        total = sum(a["weight"] for a in critic.rubric["axes"])
        assert abs(total - 1.0) < 1e-9

    def test_custom_rubric_loaded(self, tmp_path):
        """Custom rubric axes are loaded and used without code changes."""
        custom_rubric = {
            "axes": [
                {"name": "accuracy", "weight": 0.7, "description": "Is the output accurate?"},
                {"name": "style", "weight": 0.3, "description": "Is it well-written?"}
            ]
        }
        rubric_path = tmp_path / "custom_rubric.json"
        with open(rubric_path, "w") as f:
            json.dump(custom_rubric, f)

        critic = CriticAgent(rubric_path=rubric_path)
        assert len(critic.rubric["axes"]) == 2
        assert critic.rubric["axes"][0]["name"] == "accuracy"

    def test_rubric_driven_scoring(self, tmp_path):
        """evaluate() uses rubric axes dynamically — adding an axis changes scoring."""
        from unittest.mock import patch
        from src.telos.schemas import EvaluationResponse

        custom_rubric = {
            "axes": [
                {"name": "accuracy", "weight": 0.6, "description": "Output accuracy"},
                {"name": "style", "weight": 0.4, "description": "Writing style"},
            ]
        }
        rubric_path = tmp_path / "rubric.json"
        with open(rubric_path, "w") as f:
            json.dump(custom_rubric, f)

        critic = CriticAgent(rubric_path=rubric_path)

        mock_response = EvaluationResponse(
            scores={"accuracy": 1.0, "style": 0.5},
            criteria_met=[True],
            reasoning="test",
        )

        with patch.object(critic, "chat_structured", return_value=mock_response):
            goal = GoalSchema(title="Test", success_criteria=["criterion"], output_path="out.txt")
            result = critic.evaluate(goal, artifact_path=None, sandbox=None)

        # 1.0 * 0.6 + 0.5 * 0.4 = 0.8
        assert result["overall_score"] == pytest.approx(0.8)
        assert result["breakdown"] == {"accuracy": 1.0, "style": 0.5}
        assert not result["failed"]
