"""Admin dashboard (the spec's 15 metrics) and the platform activity feed (plan 4.12)."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import AdminUser, DbSession
from app.schemas.dashboard import ActivityItem, AdminDashboard
from app.services import dashboard_service

router = APIRouter(tags=["admin-dashboard"])


@router.get("/dashboard", response_model=AdminDashboard)
def admin_dashboard(admin: AdminUser, db: DbSession):  # noqa: ANN201
    """All dashboard metrics in one call. Quantities are per unit (kg and packages are never added)."""
    return dashboard_service.admin(db, admin)


@router.get("/activity", response_model=list[ActivityItem])
def activity(admin: AdminUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 30):  # noqa: ANN201
    """Recent platform activity, newest first."""
    return dashboard_service.activity(db, limit=limit)
