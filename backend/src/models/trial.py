from sqlalchemy import String, JSON, Double, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Trial(Base):
    """盲态试验。批次映射在揭盲前只以密文封存，普通接口从不返回明文。"""
    __tablename__ = 'trials'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    params_checksum: Mapped[str] = mapped_column(String(64), default='')
    status: Mapped[str] = mapped_column(String(20), default='blind')
    # Fencing token: bumped by batch registration and by completed unblinding.
    round_no: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    sealed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    unblind_started_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    unblinded_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    unblinded_by: Mapped[str] = mapped_column(String(150), default='')
    # Frozen scope, pinned when unblinding starts; later imports never join it.
    sealed_batches: Mapped[list] = mapped_column(JSON, default=list)
    sealed_params: Mapped[dict] = mapped_column(JSON, default=dict)
    formal_result: Mapped[dict] = mapped_column(JSON, default=dict)
    formal_checksum: Mapped[str] = mapped_column(String(64), default='')
    formal_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TrialBatch(Base):
    """纳入盲态的测时批次。mapping_sealed 为 AES-GCM 密文，仅保管员揭盲时解开。"""
    __tablename__ = 'trial_batches'
    __table_args__ = (UniqueConstraint('trial_id', 'dataset_id', name='uq_trial_dataset'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey('trials.id'))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    dataset_name: Mapped[str] = mapped_column(String(160), default='')
    blind_name: Mapped[str] = mapped_column(String(160), default='')
    dataset_checksum: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    mapping_sealed: Mapped[str] = mapped_column(String(600))
    seal_digest: Mapped[str] = mapped_column(String(64))
    registered_by: Mapped[str] = mapped_column(String(150))
    registered_at: Mapped[float] = mapped_column(Double)
    formal_result: Mapped[dict] = mapped_column(JSON, default=dict)
