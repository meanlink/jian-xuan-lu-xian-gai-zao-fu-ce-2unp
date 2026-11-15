from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, Integer
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class Job(Base):
    __tablename__ = 'jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    # normal: analyzes one business dataset; blind: analyzes a trial-scoped mirror batch
    mode: Mapped[str] = mapped_column(String(20), default='normal')
    trial_id: Mapped[int | None] = mapped_column(ForeignKey('trials.id'), nullable=True)
    # Round of the trial this blind job was queued against. The worker commits only
    # while the trial is still blind at this round; registration and unblinding bump
    # the round, so stale blind runs can never overwrite scope or formal conclusions.
    trial_round: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    started_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
