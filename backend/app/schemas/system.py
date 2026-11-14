"""System management: categories, verification requirements and platform settings (spec 4.4.11)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.enums import PerishabilityClass, QuantityUnit

CODE = r"^[a-z][a-z0-9_]{1,39}$"  # lowercase identifier used by the program, e.g. "small_business"


class _CategoryFields(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=1000)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Please enter a name.")
        return cleaned


class _CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    is_active: bool
    sort_order: int
    in_use: int  # how many records use it (profiles, listings, preferences); used ones can't be deleted
    created_at: datetime
    updated_at: datetime


# ------------------------------------------------------------------ provider categories
class ProviderCategoryUpdate(_CategoryFields):
    pass


class ProviderCategoryCreate(ProviderCategoryUpdate):
    code: str = Field(pattern=CODE)


class ProviderCategoryAdminOut(_CategoryOut):
    pass


# ------------------------------------------------------------------ receiver categories
class ReceiverCategoryUpdate(_CategoryFields):
    # Used by matching and allocation (spec 6.4 "configured priority rules")
    priority_weight: Decimal = Field(default=Decimal("1.0"), gt=0, le=10, max_digits=4, decimal_places=2)
    requires_documents: bool = True


class ReceiverCategoryCreate(ReceiverCategoryUpdate):
    code: str = Field(pattern=CODE)


class ReceiverCategoryAdminOut(_CategoryOut):
    priority_weight: float
    requires_documents: bool


# ---------------------------------------------------------------------- food categories
class FoodCategoryUpdate(_CategoryFields):
    perishability_class: PerishabilityClass
    requires_preparation_time: bool = False
    requires_expiry_date: bool = False
    # For the "estimated meals" figure, e.g. {"kg": 4}. Always shown as an estimate.
    meals_per_unit: dict[QuantityUnit, float] = Field(default_factory=dict)

    @field_validator("meals_per_unit")
    @classmethod
    def positive(cls, value: dict[QuantityUnit, float]) -> dict[QuantityUnit, float]:
        if any(v <= 0 or v > 1000 for v in value.values()):
            raise ValueError("Each meals-per-unit figure must be above 0 and at most 1000.")
        return value


class FoodCategoryCreate(FoodCategoryUpdate):
    code: str = Field(pattern=CODE)


class FoodCategoryAdminOut(_CategoryOut):
    perishability_class: PerishabilityClass
    requires_preparation_time: bool
    requires_expiry_date: bool
    meals_per_unit: dict[str, float]


# ---------------------------------------------------------- verification requirements
class RequirementUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    is_required: bool = True
    is_active: bool = True


class RequirementCreate(RequirementUpdate):
    role: Literal["provider", "receiver", "volunteer"]
    # receivers only: None = every receiver; a category id = receivers of that category
    receiver_category_id: uuid.UUID | None = None
    document_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,49}$")


class RequirementAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    receiver_category_id: uuid.UUID | None
    receiver_category_name: str | None
    document_type: str
    name: str
    description: str | None
    is_required: bool
    is_active: bool
    updated_at: datetime


# ------------------------------------------------------------------- platform settings
class SettingOut(BaseModel):
    key: str
    value: Any
    default: Any
    is_default: bool
    description: str
    updated_at: datetime | None
    updated_by_email: str | None


class SettingIn(BaseModel):
    value: Any = Field(description="A number, a text choice or (for matching_weights) an object of weights")
