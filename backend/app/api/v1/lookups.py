"""Public lookups: the lists both apps need to build their forms (no login needed,
because the registration form uses them before the user has an account)."""

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import DbSession
from app.domain.enums import (
    COUNTABLE_UNITS,
    PUBLIC_ROLES,
    AccountStatus,
    PerishabilityClass,
    QuantityUnit,
    VehicleType,
    VerificationStatus,
)
from app.models.category import FoodCategory, ProviderCategory, ReceiverCategory
from app.schemas.lookups import CategoryOut, EnumsOut, FoodCategoryOut, ReceiverCategoryOut
from app.schemas.profiles import DAYS

router = APIRouter(prefix="/lookups", tags=["lookups"])


def _active(db: DbSession, model):  # noqa: ANN001, ANN202
    return db.scalars(
        select(model).where(model.is_active.is_(True)).order_by(model.sort_order, model.name)
    ).all()


@router.get("/provider-categories", response_model=list[CategoryOut])
def provider_categories(db: DbSession):  # noqa: ANN201
    return _active(db, ProviderCategory)


@router.get("/receiver-categories", response_model=list[ReceiverCategoryOut])
def receiver_categories(db: DbSession):  # noqa: ANN201
    return _active(db, ReceiverCategory)


@router.get("/food-categories", response_model=list[FoodCategoryOut])
def food_categories(db: DbSession):  # noqa: ANN201
    return _active(db, FoodCategory)


@router.get("/enums", response_model=EnumsOut)
def enums() -> EnumsOut:
    return EnumsOut(
        public_roles=[r.value for r in PUBLIC_ROLES],
        quantity_units=[u.value for u in QuantityUnit],
        countable_units=sorted(u.value for u in COUNTABLE_UNITS),
        vehicle_types=[v.value for v in VehicleType],
        perishability_classes=[p.value for p in PerishabilityClass],
        verification_statuses=[s.value for s in VerificationStatus],
        account_statuses=[s.value for s in AccountStatus],
        days_of_week=list(DAYS),
    )
