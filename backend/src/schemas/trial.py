from pydantic import BaseModel, Field
class TrialInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    params: dict = Field(default_factory=dict)
class TrialBatchInput(BaseModel):
    dataset_id: int
    # The per-batch 甲/乙 → 原/新 direction. Sealed on arrival, never echoed.
    arms: dict = Field(default_factory=dict)
