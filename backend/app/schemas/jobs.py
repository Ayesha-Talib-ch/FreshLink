"""Background jobs as seen by administrators (Stage 9)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class JobRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job: str
    trigger: str  # schedule / admin / cli
    status: str  # success / failed / skipped
    result: dict[str, Any] | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


class JobOut(BaseModel):
    name: str
    description: str
    every_minutes: int
    last_run: JobRunOut | None
    last_success_at: datetime | None
