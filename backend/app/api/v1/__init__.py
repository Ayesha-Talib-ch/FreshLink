"""Version 1 of the FreshLink REST API.

Every router is registered here once, so main.py stays short and both the
website and the mobile app see exactly the same endpoints.
"""

from fastapi import APIRouter

from app.api.v1 import system

api_router = APIRouter()
api_router.include_router(system.router)
