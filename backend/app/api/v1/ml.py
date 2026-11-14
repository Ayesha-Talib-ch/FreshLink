"""Provider daily logs and surplus estimates (plan 4.11 and 7.2).

Daily logs: any provider with a business profile records their own days.
Estimates: approved providers only. An estimate helps plan a listing; the provider always enters
and confirms the real quantity. It is never a statement about food safety."""

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import AppSettings, CurrentUser, DbSession, ProviderUser, VerifiedProvider
from app.core.errors import AppError, error_responses
from app.domain.enums import UserRole
from app.ml.surplus_features import GAP
from app.models.category import FoodCategory
from app.models.ml import SurplusPrediction
from app.models.profiles import ProviderProfile
from app.schemas.common import Page
from app.schemas.ml import DailyLogIn, DailyLogOut, EstimateIn, EstimateOut, ImportResult
from app.services import daily_log_service, ml_service
from app.services.listing_service import provider_profile_or_409

router = APIRouter(tags=["ai"])
PKT = timezone(timedelta(hours=5))
MAX_IMPORT_BYTES = 1024 * 1024


def _today() -> date:
    return datetime.now(PKT).date()  # the provider's calendar day (Pakistan time)


# ------------------------------------------------------------------ daily logs


@router.post("/providers/me/daily-logs", response_model=DailyLogOut, status_code=status.HTTP_201_CREATED,
             responses={**error_responses(409, 422), 200: {"model": DailyLogOut, "description": "Updated an existing day"}})
def record_daily_log(body: DailyLogIn, user: ProviderUser, db: DbSession, response: Response):  # noqa: ANN201
    """Record one day: what you produced and sold of a food category. Recording the same day again
    updates it (200). The surplus is calculated for you."""
    row, created = daily_log_service.record(db, provider_profile_or_409(db, user), body, _today())
    if not created:
        response.status_code = status.HTTP_200_OK
    return row


@router.get("/providers/me/daily-logs", response_model=Page[DailyLogOut], responses=error_responses(409))
def my_daily_logs(  # noqa: ANN201, PLR0913
    user: ProviderUser,
    db: DbSession,
    food_category_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 31,
):
    """Your logs, newest day first."""
    return daily_log_service.list_logs(db, provider_profile_or_409(db, user), food_category_id=food_category_id,
                                       date_from=date_from, date_to=date_to, page=page, page_size=page_size)


@router.delete("/providers/me/daily-logs/{log_id}", status_code=status.HTTP_204_NO_CONTENT,
               responses=error_responses(404, 409))
def delete_daily_log(log_id: uuid.UUID, user: ProviderUser, db: DbSession) -> None:
    daily_log_service.delete_log(db, provider_profile_or_409(db, user), log_id)


@router.post("/providers/me/daily-logs/import", response_model=ImportResult, responses=error_responses(409, 413, 422))
async def import_daily_logs(  # noqa: ANN201
    user: ProviderUser, db: DbSession,
    file: Annotated[UploadFile, File(description="CSV: date,food_category_code,unit,produced,sold (up to 1 MB)")],
):
    """Upload many days at once (e.g. from a spreadsheet). All-or-nothing: if any row has a problem,
    nothing is saved and the rows to fix are listed."""
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        raise AppError(413, "FILE_TOO_LARGE", "The file is larger than 1 MB. Split it into smaller files.")
    return daily_log_service.import_csv(db, provider_profile_or_409(db, user), content, _today())


# ------------------------------------------------------------- surplus estimates


@router.post("/ai/surplus-estimates", response_model=EstimateOut, status_code=status.HTTP_201_CREATED,
             responses=error_responses(409, 422))
def estimate_surplus(body: EstimateIn, user: VerifiedProvider, db: DbSession, settings: AppSettings):  # noqa: ANN201
    """Estimate how much of a food category will be left over on a day (today up to 7 days ahead),
    with a range and how confident it is. Prefills the quantity on a new listing; you must confirm it."""
    today = _today()
    if not today <= body.target_date <= today + timedelta(days=GAP):
        raise AppError(422, "VALIDATION_ERROR", f"Choose a day from today up to {GAP} days ahead.",
                       {"fields": [{"field": "body.target_date", "message": f"today .. today + {GAP} days"}]})
    category = db.get(FoodCategory, body.food_category_id)
    if category is None or not category.is_active:
        raise AppError(422, "VALIDATION_ERROR", "Please choose an active food category.",
                       {"fields": [{"field": "body.food_category_id", "message": "Unknown or inactive category."}]})
    provider = provider_profile_or_409(db, user)
    return ml_service.estimate(db, provider.id, category, body.target_date, body.planned_production,
                               settings.ml_artifacts_dir)


@router.get("/ai/surplus-estimates", response_model=Page[EstimateOut], responses=error_responses(403))
def estimate_history(  # noqa: ANN201
    user: CurrentUser,
    db: DbSession,
    provider_id: Annotated[uuid.UUID | None, Query(description="Admins only: one provider")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Past estimates with the actual surplus once that day is logged (providers: their own; admins: all)."""
    query = select(SurplusPrediction)
    if user.role == UserRole.PROVIDER:
        profile = db.scalar(select(ProviderProfile.id).where(ProviderProfile.user_id == user.id))
        query = query.where(SurplusPrediction.provider_id == profile)
    elif user.role == UserRole.ADMIN:
        if provider_id:
            query = query.where(SurplusPrediction.provider_id == provider_id)
    else:
        raise AppError(403, "FORBIDDEN", "Only providers and administrators have surplus estimates.")
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = list(db.scalars(query.order_by(SurplusPrediction.created_at.desc(), SurplusPrediction.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    return {"items": ml_service.decorate(db, items), "total": total, "page": page, "page_size": page_size}

