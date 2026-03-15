from typing import List
from pydantic import BaseModel, Field

class GoalSchema(BaseModel):
    title: str = Field(..., description="短い目標タイトル（30文字以内）")
    success_criteria: List[str] = Field(..., description="合否判定できる具体的な条件リスト")
    output_path: str = Field(..., description="成果物のファイルパス（例: solution.py）")

class EvaluationResponse(BaseModel):
    completeness: float = Field(..., ge=0, le=1)
    coherence: float = Field(..., ge=0, le=1)
    novelty: float = Field(..., ge=0, le=1)
    criteria_met: List[bool]
    reasoning: str
