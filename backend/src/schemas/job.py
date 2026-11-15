from pydantic import BaseModel, Field
class JobInput(BaseModel):
    """普通任务传 dataset_id；盲态任务传 trial_id，服务端按已登记批次展开为每批任务。"""
    name: str = Field(min_length=2, max_length=160)
    dataset_id: int | None = None
    trial_id: int | None = None
    params: dict = Field(default_factory=dict)
