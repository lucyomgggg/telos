import json
import os
from pathlib import Path
from .llm import LLMInterface
from .config import TELOS_HOME
from .logger import get_logger

log = get_logger("critic")

class CriticAgent:
    def __init__(self, rubric_path: str = None):
        from .config import settings
        self.settings = settings.load()
        
        explicit_rubric_path = rubric_path or self.settings.critic.rubric_path
        self.rubric_path = Path(explicit_rubric_path) if explicit_rubric_path else (TELOS_HOME / "rubric.json")
        self.llm = LLMInterface(model=self.settings.llm.critic_model)
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
            log.info("Created default rubric at %s", self.rubric_path)
            return default_rubric
        
        with open(self.rubric_path, "r") as f:
            return json.load(f)

    def evaluate(self, goal: str, artifact_content: str) -> dict:
        """
        Evaluate the generated artifact against the rubric.
        Returns a dict with 'overall_score', 'breakdown', and 'reasoning'.
        """
        system_prompt = (
            "You are an objective Critic Agent. Your task is to evaluate the provided artifact based on the rubric.\n"
            "You MUST output your evaluation in valid JSON format ONLY.\n"
            "The response must be a JSON object with the following format:\n"
            "{\n"
            '  "scores": {"completeness": 0.8, "coherence": 0.9, "novelty": 0.5},\n'
            '  "reasoning": "Brief explanation of scores."\n'
            "}\n\n"
            f"Rubric: {json.dumps(self.rubric)}\n"
        )
        
        user_prompt = f"Goal:\n{goal}\n\nArtifact Content:\n{artifact_content}"
        
        try:
            response = self.llm.chat(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"}
            )
            
            result_text = response.choices[0].message.content
            if not result_text:
                raise ValueError("Empty response from LLM")
            
            # strip markdown blocks if present
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0].strip()
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0].strip()

            evaluation = json.loads(result_text)
            
            # Calculate weighted average
            overall_score = 0.0
            scores = evaluation.get("scores", {})
            for axis in self.rubric["axes"]:
                name = axis["name"]
                weight = axis["weight"]
                score = scores.get(name, 0.0)
                overall_score += (score * weight)
            
            result = {
                "overall_score": round(overall_score, 2),
                "breakdown": scores,
                "reasoning": evaluation.get("reasoning", "")
            }
            log.info("Evaluation complete: score=%.2f", result["overall_score"])
            return result
            
        except Exception as e:
            log.error("Critic evaluation failed: %s", e)
            return {
                "overall_score": 0.0,
                "breakdown": {},
                "reasoning": f"Evaluation failed due to error: {e}"
            }
