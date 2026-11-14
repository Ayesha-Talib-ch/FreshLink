"""Admin: per-category urgency thresholds (spec 6.2: "configurable ... appropriate
to the food category"). Changes are audited. Stored listing levels are refreshed
by the next urgency scan; API responses use the new rule immediately."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.api.deps import AdminUser, Client, DbSession
from app.core.errors import AppError, error_responses
from app.models.category import FoodCategory, UrgencyRuleRow
from app.schemas.lookups import CategoryOut
from app.services.audit_service import record

router = APIRouter(tags=["admin-urgency"])


class UrgencyRuleIn(BaseModel):
    medium_hours: float = Field(gt=0, le=2000)
    high_hours: float = Field(gt=0, le=2000)
    critical_hours: float = Field(gt=0, le=2000)
    max_hours_since_preparation: float | None = Field(default=None, gt=0, le=2000)

    @model_validator(mode="after")
    def ordered(self) -> "UrgencyRuleIn":
        if not (self.critical_hours < self.high_hours < self.medium_hours):
            raise ValueError("Thresholds must satisfy critical < high < medium (hours left before the deadline).")
        return self


class UrgencyRuleAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    food_category: CategoryOut
    medium_hours: float
    high_hours: float
    critical_hours: float
    max_hours_since_preparation: float | None
    note: str = "Platform prioritisation rule set by administrators. Not a food-safety limit."


def _out(category: FoodCategory, row: UrgencyRuleRow) -> dict:
    return {"food_category": category, "medium_hours": row.medium_hours, "high_hours": row.high_hours,
            "critical_hours": row.critical_hours, "max_hours_since_preparation": row.max_hours_since_preparation}


@router.get("/urgency-rules", response_model=list[UrgencyRuleAdminOut])
def list_rules(admin: AdminUser, db: DbSession) -> list[dict]:
    rows = db.execute(select(FoodCategory, UrgencyRuleRow).join(
        UrgencyRuleRow, UrgencyRuleRow.food_category_id == FoodCategory.id).order_by(FoodCategory.sort_order)).all()
    return [_out(category, row) for category, row in rows]


@router.put("/urgency-rules/{food_category_id}", response_model=UrgencyRuleAdminOut, responses=error_responses(404))
def save_rule(food_category_id: uuid.UUID, body: UrgencyRuleIn, admin: AdminUser, db: DbSession,
              client: Client) -> dict:
    category = db.get(FoodCategory, food_category_id)
    if category is None:
        raise AppError(404, "NOT_FOUND", "Food category not found.")
    row = db.scalar(select(UrgencyRuleRow).where(UrgencyRuleRow.food_category_id == food_category_id)
                    .with_for_update(of=UrgencyRuleRow))
    before = None
    if row is None:
        row = UrgencyRuleRow(food_category_id=food_category_id)
        db.add(row)
    else:
        before = {k: float(v) if v is not None else None for k, v in (
            ("medium_hours", row.medium_hours), ("high_hours", row.high_hours),
            ("critical_hours", row.critical_hours), ("max_hours_since_preparation", row.max_hours_since_preparation))}
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    row.updated_by = admin.id
    record(db, actor=admin, action="urgency_rule.update", entity_type="food_category", entity_id=category.id,
           client=client, before=before, after=body.model_dump())
    db.commit()
    db.refresh(row)
    return _out(category, row)
