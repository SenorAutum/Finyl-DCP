"""Platform operations records (WP8)."""
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, String, Text, UniqueConstraint

from app.core.database import Base


class ScheduledJobRun(Base):
    """One run of a background job for one time slot. The (job_id, slot) pair is
    unique, so with several backend copies running only the first to claim a slot
    runs the job; the others skip it."""
    __tablename__ = "scheduled_job_runs"
    __table_args__ = (UniqueConstraint("job_id", "slot", name="uq_scheduled_job_runs_slot"),)

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    job_id = Column(String(60), nullable=False)
    slot = Column(String(40), nullable=False)
    host = Column(String(120))
    started_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True))
    status = Column(String(20), nullable=False, default="running")
    detail = Column(Text)
