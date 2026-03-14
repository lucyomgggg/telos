import pytest
import json
from pathlib import Path
from telos.critic import CriticAgent


class TestCriticAgent:
    @pytest.fixture
    def critic(self, tmp_path):
        rubric_path = tmp_path / "rubric.json"
        return CriticAgent(rubric_path=rubric_path)

    def test_default_rubric_created(self, critic, tmp_path):
        """Should create a default rubric if one doesn't exist."""
        rubric_path = tmp_path / "rubric.json"
        assert rubric_path.exists()
        
        with open(rubric_path) as f:
            rubric = json.load(f)
        
        assert "axes" in rubric
        assert len(rubric["axes"]) == 3
        
        names = [a["name"] for a in rubric["axes"]]
        assert "completeness" in names
        assert "coherence" in names
        assert "novelty" in names

    def test_rubric_weights_sum_to_one(self, critic):
        total = sum(a["weight"] for a in critic.rubric["axes"])
        assert abs(total - 1.0) < 1e-9

    def test_custom_rubric_loaded(self, tmp_path):
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
