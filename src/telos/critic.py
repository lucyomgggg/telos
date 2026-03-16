import json
from pathlib import Path
from typing import Any, Dict, Optional
from .llm import LLMService
from .config import TELOS_HOME, settings
from .logger import get_logger
from .schemas import GoalSchema, EvaluationResponse
from .interfaces import TemplateLoader

from .agents import BaseAgent

class CriticAgent(BaseAgent):
    def __init__(self, rubric_path: str = None, cost_tracker: Any = None):
        super().__init__(agent_type="critic", cost_tracker=cost_tracker)
        
        explicit_rubric_path = rubric_path or self.settings.critic.rubric_path
        self.rubric_path = Path(explicit_rubric_path) if explicit_rubric_path else (TELOS_HOME / "rubric.json")
        self.rubric = self._load_rubric()

    def _load_rubric(self) -> dict:
        if not self.rubric_path.exists():
            default_rubric = {
                "axes": [
                    {"name": "novelty", "weight": 0.4, "description": "Is this approach/result novel compared to previous iterations?"},
                    {"name": "completeness", "weight": 0.4, "description": "Is the artifact complete and functional as requested?"},
                    {"name": "coherence", "weight": 0.2, "description": "Does the artifact match the original goal?"}
                ]
            }
            self.rubric_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.rubric_path, "w") as f:
                json.dump(default_rubric, f, indent=4)
            self.log.info("Created default rubric at %s", self.rubric_path)
            return default_rubric
        
        with open(self.rubric_path, "r") as f:
            return json.load(f)

    def evaluate(self, goal: GoalSchema, artifact_path: str, sandbox=None, loop_id: str = "unknown") -> dict:
        """Evaluate the generated artifact using structured LLM response."""
        artifact_content = ""
        if sandbox and artifact_path:
            try:
                artifact_content = sandbox.read_file(artifact_path)
            except Exception as e:
                self.log.warning(f"Could not read artifact: {e}")
                artifact_content = "(file not found)"

        user_prompt = (
            f"Goal: {goal.title}\n"
            f"Success Criteria:\n" + "\n".join(f"- {c}" for c in goal.success_criteria) +
            f"\n\nArtifact Content:\n{artifact_content}"
        )
        
        system_prompt = self.load_template("critic_system", "Evaluate the artifact against the goal.")
        
        try:
            response = self.chat_structured(
                messages=[{"role": "user", "content": user_prompt}],
                response_model=EvaluationResponse,
                system=system_prompt,
                loop_id=loop_id
            )

            # Calculate weighted average based on rubric
            overall_score = 0.0
            scores = {
                "completeness": response.completeness,
                "coherence": response.coherence,
                "novelty": response.novelty,
                "performance": response.performance,
            }
            
            for axis in self.rubric["axes"]:
                name = axis["name"]
                weight = axis["weight"]
                score = scores.get(name, 0.0)
                overall_score += (score * weight)
            
            result = {
                "overall_score": round(overall_score, 2),
                "breakdown": scores,
                "criteria_met": response.criteria_met,
                "reasoning": response.reasoning,
                "failed": False
            }
            self.log.info("Evaluation complete: score=%.2f", result["overall_score"])
            return result

        except Exception as e:
            self.log.error(f"Critic evaluation failed: {e}")
            return {
                "overall_score": 0.0,
                "breakdown": {},
                "criteria_met": [],
                "reasoning": f"Evaluation failed: {e}",
                "failed": True
            }
