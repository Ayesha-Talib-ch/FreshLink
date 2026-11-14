"""Shapes shared by many endpoints."""

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Every list endpoint returns this shape (Technical Plan section 4.1)."""

    items: list[T]
    total: int  # number of matching rows in total (not just this page)
    page: int
    page_size: int
