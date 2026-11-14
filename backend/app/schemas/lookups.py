"""Public lookup data that fills drop-downs on both apps (Technical Plan section 4.3)."""

import uuid

from pydantic import BaseModel, ConfigDict

from app.domain.enums import PerishabilityClass


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None


class ReceiverCategoryOut(CategoryOut):
    requires_documents: bool


class FoodCategoryOut(CategoryOut):
    perishability_class: PerishabilityClass
    requires_preparation_time: bool
    requires_expiry_date: bool


class EnumsOut(BaseModel):
    """Every fixed list the apps need, so neither app hard-codes them."""

    public_roles: list[str]
    quantity_units: list[str]
    countable_units: list[str]
    vehicle_types: list[str]
    perishability_classes: list[str]
    verification_statuses: list[str]
    account_statuses: list[str]
    days_of_week: list[str]
