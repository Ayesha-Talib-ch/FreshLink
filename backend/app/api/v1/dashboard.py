"""GET /me/dashboard: one endpoint, four shapes (plan 4.3). The apps poll it every 30 s (plan M2)."""

from typing import Annotated

from fastapi import APIRouter
from pydantic import Field

from app.api.deps import CurrentUser, DbSession
from app.schemas.dashboard import AdminDashboard, ProviderDashboard, ReceiverDashboard, VolunteerDashboard
from app.services import dashboard_service

router = APIRouter(tags=["dashboard"])

DashboardOut = Annotated[ProviderDashboard | ReceiverDashboard | VolunteerDashboard | AdminDashboard,
                         Field(discriminator="role")]


@router.get("/me/dashboard", response_model=DashboardOut)
def my_dashboard(user: CurrentUser, db: DbSession):  # noqa: ANN201
    """Counters for the logged-in user's role, plus unread and recent notifications.
    Works before approval too (unverified users see their notifications)."""
    return dashboard_service.for_user(db, user)
