"""Version 1 of the FreshLink REST API.

Every router is registered here once, so main.py stays short and both the
website and the mobile app see exactly the same endpoints.
"""

from fastapi import APIRouter

from app.api.v1 import (
    admin,
    allocations,
    assignments,
    auth,
    collections,
    dashboard,
    files,
    issues,
    listings,
    lookups,
    matching,
    notifications,
    profiles,
    requests,
    system,
    verification,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(lookups.router)
api_router.include_router(profiles.router)
api_router.include_router(files.router)
# matching BEFORE listings: /listings/recommended must not be read as /listings/{listing_id}
api_router.include_router(matching.router)
api_router.include_router(listings.router)
api_router.include_router(requests.router)
api_router.include_router(allocations.router)
api_router.include_router(collections.router)
api_router.include_router(assignments.router)
api_router.include_router(notifications.router)
api_router.include_router(dashboard.router)
api_router.include_router(issues.router)
api_router.include_router(verification.router)
api_router.include_router(admin.router)
