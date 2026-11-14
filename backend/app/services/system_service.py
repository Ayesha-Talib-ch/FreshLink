"""System management: food / provider / receiver categories and verification requirements
(spec 4.4.11; plan 4.12). Every change is audited.

Categories are never hard-coded lists (spec 2.3). Rules:
- the code is the program's fixed identifier, so it can't be changed after creation;
- a category in use (by a profile, listing or food preference) can't be deleted - deactivate it
  instead: it disappears from sign-up and listing forms, existing records keep it;
- a new food category gets a starting urgency rule, so its listings get urgency levels at once."""

import uuid
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import UserRole
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory, UrgencyRuleRow
from app.models.listing import FoodListing
from app.models.profiles import ProviderProfile, ReceiverFoodPreference, ReceiverProfile
from app.models.user import User
from app.models.verification import VerificationRequirement
from app.services.audit_service import ClientInfo, record

# kind -> (model, [(table that refers to it, its foreign-key column)])
KINDS: dict[str, tuple[type, list[tuple[type, Any]]]] = {
    "food": (FoodCategory, [(FoodListing, FoodListing.food_category_id),
                            (ReceiverFoodPreference, ReceiverFoodPreference.food_category_id)]),
    "provider": (ProviderCategory, [(ProviderProfile, ProviderProfile.provider_category_id)]),
    "receiver": (ReceiverCategory, [(ReceiverProfile, ReceiverProfile.receiver_category_id)]),
}
# A new food category starts with the same thresholds as "other" (admins adjust them in urgency rules).
STARTING_URGENCY = {"medium_hours": Decimal(48), "high_hours": Decimal(24), "critical_hours": Decimal(6)}


def _in_use(db: Session, kind: str, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    counts: dict[uuid.UUID, int] = dict.fromkeys(ids, 0)
    for _table, column in KINDS[kind][1]:
        for cid, n in db.execute(select(column, func.count()).where(column.in_(ids)).group_by(column)).all():
            counts[cid] += n
    return counts


def _decorate(db: Session, kind: str, rows: list[Any]) -> list[Any]:
    counts = _in_use(db, kind, [r.id for r in rows])
    for r in rows:
        r.in_use = counts[r.id]
    return rows


def list_categories(db: Session, kind: str) -> list[Any]:
    model = KINDS[kind][0]
    return _decorate(db, kind, list(db.scalars(select(model).order_by(model.sort_order, model.name))))


def _save(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:  # the UNIQUE constraints on code and name
        db.rollback()
        raise AppError(409, "CATEGORY_EXISTS", "A category with this code or name already exists.") from exc


def create_category(db: Session, admin: User, kind: str, body: BaseModel, client: ClientInfo) -> Any:  # noqa: ANN401
    model = KINDS[kind][0]
    row = model(**body.model_dump(mode="json" if kind == "food" else "python"))
    db.add(row)
    _save(db)
    if kind == "food":
        db.add(UrgencyRuleRow(food_category_id=row.id, **STARTING_URGENCY, updated_by=admin.id))
    record(db, actor=admin, action=f"{kind}_category.create", entity_type=f"{kind}_category", entity_id=row.id,
           client=client, after=body.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return _decorate(db, kind, [row])[0]


def _get(db: Session, kind: str, category_id: uuid.UUID, lock: bool = False) -> Any:  # noqa: ANN401
    model = KINDS[kind][0]
    query = select(model).where(model.id == category_id)
    row = db.scalar(query.with_for_update(of=model) if lock else query)
    if row is None:
        raise AppError(404, "NOT_FOUND", "Category not found.")
    return row


def update_category(db: Session, admin: User, kind: str, category_id: uuid.UUID, body: BaseModel,
                    client: ClientInfo) -> Any:  # noqa: ANN401
    row = _get(db, kind, category_id, lock=True)
    new = body.model_dump(mode="json" if kind == "food" else "python")
    before = {k: _plain(getattr(row, k)) for k in new}
    for key, value in new.items():
        setattr(row, key, value)
    _save(db)
    record(db, actor=admin, action=f"{kind}_category.update", entity_type=f"{kind}_category", entity_id=row.id,
           client=client, before=before, after=body.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return _decorate(db, kind, [row])[0]


def delete_category(db: Session, admin: User, kind: str, category_id: uuid.UUID, client: ClientInfo) -> None:
    row = _get(db, kind, category_id, lock=True)
    used = _in_use(db, kind, [row.id])[row.id]
    if used:
        raise AppError(409, "CATEGORY_IN_USE", f"{used} record(s) use this category. Deactivate it instead.",
                       {"in_use": used})
    record(db, actor=admin, action=f"{kind}_category.delete", entity_type=f"{kind}_category", entity_id=row.id,
           client=client, before={"code": row.code, "name": row.name})
    db.delete(row)
    db.commit()


def _plain(value: Any) -> Any:  # noqa: ANN401
    if isinstance(value, Decimal | uuid.UUID):
        return str(value)
    return getattr(value, "value", value)


# ------------------------------------------------------------ verification requirements


def _requirement_out(db: Session, rows: list[VerificationRequirement]) -> list[VerificationRequirement]:
    names = dict(db.execute(select(ReceiverCategory.id, ReceiverCategory.name)).all())
    for r in rows:
        r.receiver_category_name = names.get(r.receiver_category_id)
    return rows


def list_requirements(db: Session) -> list[VerificationRequirement]:
    return _requirement_out(db, list(db.scalars(select(VerificationRequirement).order_by(
        VerificationRequirement.role, VerificationRequirement.receiver_category_id.nulls_first(),
        VerificationRequirement.document_type))))


def create_requirement(db: Session, admin: User, body: BaseModel, client: ClientInfo) -> VerificationRequirement:
    data = body.model_dump()
    if data["role"] != UserRole.RECEIVER.value and data["receiver_category_id"] is not None:
        raise AppError(422, "CATEGORY_ONLY_FOR_RECEIVERS", "Only receiver requirements can be limited to a category.")
    if data["receiver_category_id"] is not None and db.get(ReceiverCategory, data["receiver_category_id"]) is None:
        raise AppError(404, "NOT_FOUND", "Receiver category not found.")
    duplicate = db.scalar(select(func.count()).select_from(VerificationRequirement).where(
        VerificationRequirement.role == data["role"], VerificationRequirement.document_type == data["document_type"],
        VerificationRequirement.receiver_category_id.is_(None) if data["receiver_category_id"] is None
        else VerificationRequirement.receiver_category_id == data["receiver_category_id"]))
    if duplicate:
        raise AppError(409, "REQUIREMENT_EXISTS", "This document is already a requirement for these users.")
    row = VerificationRequirement(**data)
    db.add(row)
    db.flush()
    record(db, actor=admin, action="verification_requirement.create", entity_type="verification_requirement",
           entity_id=row.id, client=client, after=body.model_dump(mode="json"))
    db.commit()
    db.refresh(row)
    return _requirement_out(db, [row])[0]


def update_requirement(db: Session, admin: User, requirement_id: uuid.UUID, body: BaseModel,
                       client: ClientInfo) -> VerificationRequirement:
    row = db.scalar(select(VerificationRequirement).where(VerificationRequirement.id == requirement_id)
                    .with_for_update(of=VerificationRequirement))
    if row is None:
        raise AppError(404, "NOT_FOUND", "Requirement not found.")
    new = body.model_dump()
    before = {k: getattr(row, k) for k in new}
    for key, value in new.items():
        setattr(row, key, value)
    record(db, actor=admin, action="verification_requirement.update", entity_type="verification_requirement",
           entity_id=row.id, client=client, before=before, after=new)
    db.commit()
    db.refresh(row)
    return _requirement_out(db, [row])[0]
