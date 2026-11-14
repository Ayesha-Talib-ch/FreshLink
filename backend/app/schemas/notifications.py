"""Notification bodies (Technical Plan sections 4.10 and 4.12)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import UserRole


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category_code: str
    title: str
    body: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    link: str | None = None  # where tapping it should go, e.g. "/requests/<id>"
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class UnreadCountOut(BaseModel):
    unread: int


class MarkedOut(BaseModel):
    marked: int


class AnnouncementIn(BaseModel):
    title: str = Field(min_length=3, max_length=150)
    body: str = Field(min_length=5, max_length=2000)
    audience_type: Literal["all", "role", "users"] = "all"
    audience_role: UserRole | None = None
    user_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)

    @field_validator("title", "body")
    @classmethod
    def clean(cls, value: str) -> str:
        return value.strip()


class AnnouncementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    body: str
    audience_type: str
    audience_role: str | None
    recipient_count: int
    created_at: datetime


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    description: str | None
    is_enabled: bool
    default_push: bool


class CategoryUpdateIn(BaseModel):
    is_enabled: bool
    default_push: bool = False
