"""Administrator endpoints (all under /admin, all require the admin role)."""

from fastapi import APIRouter

from app.api.v1.admin import dashboard, notifications, urgency, users, verification

router = APIRouter(prefix="/admin")
router.include_router(dashboard.router)
router.include_router(users.router)
router.include_router(verification.router)
router.include_router(urgency.router)
router.include_router(notifications.router)
