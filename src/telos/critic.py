import json
from pathlib import Path
from typing import Any, Dict, Optional
from .llm import LLMInterface
from .config import TELOS_HOME, TEMPLATES_DIR
from .logger import get_logger

log = get_logger("critic")

class CriticAgent:
    def __init__(self, rubric_path: str = None, cost_tracker: Any = None): # FIX: 4
        from .config import settings
        self.settings = settings.load()
        self.cost_tracker = cost_tracker # FIX: 4
        
        explicit_rubric_path = rubric_path or self.settings.critic.rubric_path
        self.rubric_path = Path(explicit_rubric_path) if explicit_rubric_path else (TELOS_HOME / "rubric.json")
        self.llm = LLMInterface(model=self.settings.llm.critic_model)
        self.rubric = self._load_rubric()
        from .telos_core import TemplateLoader
        self.templates = TemplateLoader()

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

    def evaluate(self, goal: "GoalSchema", artifact_path: str, sandbox=None, loop_id: str = "unknown") -> dict: # REDESIGN: 3
        """
        Evaluate the generated artifact against the rubric.
        Returns a dict with 'overall_score', 'breakdown', 'criteria_met', and 'reasoning'.
        """
        # REDESIGN: 3
        artifact_content = ""
        if sandbox and artifact_path:
            try:
                artifact_content = sandbox.read_file(artifact_path)
            except Exception as e:
                log.warning(f"Could not read artifact: {e}")
                artifact_content = "(file not found)"

        # TOOLCALL: Define evaluation tool
        eval_tool = {
            "type": "function",
            "function": {
                "name": "submit_evaluation",
                "description": "Submit the evaluation scores for the artifact.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "scores": {
                            "type": "object",
                            "properties": {
                                "completeness": {"type": "number", "minimum": 0, "maximum": 1},
                                "coherence": {"type": "number", "minimum": 0, "maximum": 1},
                                "novelty": {"type": "number", "minimum": 0, "maximum": 1}
                            },
                            "required": ["completeness", "coherence", "novelty"]
                        },
                        "criteria_met": {
                            "type": "array",
                            "items": {"type": "boolean"}
                        },
                        "reasoning": {"type": "string"}
                    },
                    "required": ["scores", "criteria_met", "reasoning"]
                }
            }
        }
        
        user_prompt = (
            f"Goal: {goal.title}\n"
            f"Success Criteria:\n" + "\n".join(f"- {c}" for c in goal.success_criteria) +
            f"\n\nArtifact Content:\n{artifact_content}"
        )
        
        system_prompt = self.templates.load("critic_system", "Evaluate the artifact against the goal.")
        
        for attempt in range(1, 4):
            try:
                # TOOLCALL: Use forced tool calling instead of json_object
                response = self.llm.chat(
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_prompt}],
                    tools=[eval_tool],
                    tool_choice={"type": "function", "function": {"name": "submit_evaluation"}}
                )
                
                if self.cost_tracker:
                    self.cost_tracker.record_usage(response, "critic", loop_id)

                msg = response.choices[0].message
                evaluation = None

                log.debug(f"Critic raw response content: {msg.content}")
                log.debug(f"Artifact content length: {len(artifact_content)}")

                # Attempt to parse from tool calls first
                if msg.tool_calls:
                    try:
                        tool_call = msg.tool_calls[0]
                        evaluation = json.loads(tool_call.function.arguments)
                        log.debug(f"Parsed evaluation from tool call: {evaluation}")
                    except (ValueError, AttributeError) as e:
                        log.warning(f"Failed to parse tool call arguments (attempt {attempt}): {e}")

                # Fallback: Check content if tool_calls failed or were missing
                if not evaluation and msg.content:
                    try:
                        # Find potential JSON in the string
                        import re
                        match = re.search(r'\{.*\}', msg.content, re.DOTALL)
                        if match:
                            raw_json = json.loads(match.group())
                            log.info(f"Fallback: Parsed JSON from message content (attempt {attempt})")
                            
                            # Handle wrapped tool call format: {"function_name": "...", "arguments": {...}}
                            if "arguments" in raw_json:
                                if isinstance(raw_json["arguments"], dict):
                                    evaluation = raw_json["arguments"]
                                elif isinstance(raw_json["arguments"], str):
                                    try:
                                        evaluation = json.loads(raw_json["arguments"])
                                    except ValueError:
                                        evaluation = raw_json
                                else:
                                    evaluation = raw_json
                            else:
                                evaluation = raw_json
                            
                            log.debug(f"Resolved evaluation object: {evaluation}")
                    except ValueError:
                        pass

                if not evaluation:
                    log.warning(f"No valid evaluation found in response (attempt {attempt}). Content: {msg.content[:100]}...")
                    continue

                # Calculate weighted average
                overall_score = 0.0
                scores = evaluation.get("scores", {})
                if not scores:
                    log.warning(f"Wait, no 'scores' key in evaluation: {evaluation}")
                
                for axis in self.rubric["axes"]:
                    name = axis["name"]
                    weight = axis["weight"]
                    score = scores.get(name)
                    if score is None:
                        log.warning(f"Score for axis '{name}' missing in evaluation. scores keys: {list(scores.keys())}")
                        score = 0.0
                    overall_score += (score * weight)
                
                result = {
                    "overall_score": round(overall_score, 2),
                    "breakdown": scores,
                    "criteria_met": evaluation.get("criteria_met", []),
                    "reasoning": evaluation.get("reasoning", ""),
                    "failed": False
                }
                log.info("Evaluation complete: score=%.2f", result["overall_score"])
                return result
                
            except Exception as e:
                log.error(f"Critic evaluation attempt {attempt} failed: {e}")
                if attempt < 3:
                    import time
                    time.sleep(2)
                continue
        
        return {
            "overall_score": 0.0,
            "breakdown": {},
            "criteria_met": [],
            "reasoning": "Evaluation failed after multiple attempts.",
            "failed": True
        }
