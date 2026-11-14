"""System management (spec 4.4.11): categories, verification requirements and platform settings.
Every change is audited. Urgency thresholds per food category: /admin/urgency-rules (Stage 3b)."""

import uuid
from typing import Any

from fastapi import APIRouter, status

from app.api.deps import AdminUser, Client, DbSession
from app.core.errors import error_responses
from app.schemas.system import (
    FoodCategoryAdminOut,
    FoodCategoryCreate,
    FoodCategoryUpdate,
    ProviderCategoryAdminOut,
    ProviderCategoryCreate,
    ProviderCategoryUpdate,
    ReceiverCategoryAdminOut,
    ReceiverCategoryCreate,
    ReceiverCategoryUpdate,
    RequirementAdminOut,
    RequirementCreate,
    RequirementUpdate,
    SettingIn,
    SettingOut,
)
from app.services import settings_service, system_service

router = APIRouter(tags=["admin-system"])


def _category_routes(kind: str, path: str, out: type, create: type, update: type) -> None:
    """The same four endpoints for each kind of category."""
    label = f"{kind} category"

    def list_(admin: AdminUser, db: DbSession) -> Any:  # noqa: ANN401
        return system_service.list_categories(db, kind)

    def create_(body: create, admin: AdminUser, db: DbSession, client: Client) -> Any:  # noqa: ANN401
        return system_service.create_category(db, admin, kind, body, client)

    def update_(category_id: uuid.UUID, body: update, admin: AdminUser, db: DbSession,  # noqa: ANN401
                client: Client) -> Any:
        return system_service.update_category(db, admin, kind, category_id, body, client)

    def delete_(category_id: uuid.UUID, admin: AdminUser, db: DbSession, client: Client) -> None:
        system_service.delete_category(db, admin, kind, category_id, client)

    router.add_api_route(path, list_, methods=["GET"], response_model=list[out], name=f"list_{kind}_categories",
                         summary=f"All {label} rows, active and inactive, with how often each is used")
    router.add_api_route(path, create_, methods=["POST"], response_model=out, status_code=status.HTTP_201_CREATED,
                         responses=error_responses(409), name=f"create_{kind}_category", summary=f"Add a {label}")
    router.add_api_route(f"{path}/{{category_id}}", update_, methods=["PUT"], response_model=out,
                         responses=error_responses(404, 409), name=f"update_{kind}_category",
                         summary=f"Edit a {label} (the code can't change). is_active=false hides it from forms")
    router.add_api_route(f"{path}/{{category_id}}", delete_, methods=["DELETE"],
                         status_code=status.HTTP_204_NO_CONTENT, responses=error_responses(404, 409),
                         name=f"delete_{kind}_category", summary=f"Delete an unused {label} (used ones: deactivate)")


_category_routes("food", "/food-categories", FoodCategoryAdminOut, FoodCategoryCreate, FoodCategoryUpdate)
_category_routes("provider", "/provider-categories", ProviderCategoryAdminOut, ProviderCategoryCreate,
                 ProviderCategoryUpdate)
_category_routes("receiver", "/receiver-categories", ReceiverCategoryAdminOut, ReceiverCategoryCreate,
                 ReceiverCategoryUpdate)


# ------------------------------------------------------------ verification requirements


@router.get("/verification-requirements", response_model=list[RequirementAdminOut])
def list_requirements(admin: AdminUser, db: DbSession):  # noqa: ANN201
    """Which documents each role (and receiver category) must upload."""
    return system_service.list_requirements(db)


@router.post("/verification-requirements", response_model=RequirementAdminOut, status_code=status.HTTP_201_CREATED,
             responses=error_responses(404, 409, 422))
def create_requirement(body: RequirementCreate, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    return system_service.create_requirement(db, admin, body, client)


@router.put("/verification-requirements/{requirement_id}", response_model=RequirementAdminOut,
            responses=error_responses(404))
def update_requirement(requirement_id: uuid.UUID, body: RequirementUpdate, admin: AdminUser, db: DbSession,  # noqa: ANN201
                       client: Client):
    """Rename, make optional / required, or deactivate (is_active=false) a requirement."""
    return system_service.update_requirement(db, admin, requirement_id, body, client)


# -------------------------------------------------------------------- platform settings


@router.get("/settings", response_model=list[SettingOut])
def list_settings(admin: AdminUser, db: DbSession):  # noqa: ANN201
    """Every platform setting: current value, default, and who changed it."""
    return settings_service.list_all(db)


@router.put("/settings/{key}", response_model=SettingOut, responses=error_responses(404, 422))
def update_setting(key: str, body: SettingIn, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Change one setting. Bad values are refused with the reason (e.g. weights must add up to 1)."""
    return settings_service.update(db, admin, key, body.value, client)


@router.delete("/settings/{key}", response_model=SettingOut, responses=error_responses(404))
def reset_setting(key: str, admin: AdminUser, db: DbSession, client: Client):  # noqa: ANN201
    """Go back to the default value."""
    return settings_service.reset(db, admin, key, client)
