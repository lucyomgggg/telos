from typing import List, Dict
from pydantic import BaseModel, Field

class GoalSchema(BaseModel):
    title: str = Field(..., description="短い目標タイトル（30文字以内）")
    success_criteria: List[str] = Field(..., description="合否判定できる具体的な条件リスト")
    output_path: str = Field(..., description="成果物のファイルパス（例: solution.py）")

class EvaluationResponse(BaseModel):
    scores: Dict[str, float] = Field(
        ...,
        description="Rubric axis name → score (0.0-1.0). Keys must match all rubric axes."
    )
    criteria_met: List[bool]
    reasoning: str
