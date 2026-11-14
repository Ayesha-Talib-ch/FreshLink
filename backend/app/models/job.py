"""One row per run of a background job (plan M8: the worker process), so administrators can see
what ran, when, what it changed and whether it failed."""

from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPrimaryKeyMixin


class JobRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_job_started", "job", text("started_at DESC")),)

    job: Mapped[str] = mapped_column(String(40))  # e.g. "urgency_scan"
    trigger: Mapped[str] = mapped_column(String(20))  # "schedule", "admin" or "cli"
    status: Mapped[str] = mapped_column(String(10))  # "success", "failed" or "skipped" (already running)
    result: Mapped[dict | None] = mapped_column(JSONB)  # e.g. {"expired": 2}
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
