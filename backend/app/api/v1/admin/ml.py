"""Model registry for administrators (plan 4.11 and 7.7). Retraining is a background job:
POST /admin/jobs/surplus_retrain/run (also runs weekly in the worker)."""

import uuid

from fastapi import APIRouter

from app.api.deps import AdminUser, AppSettings, Client, DbSession
from app.core.errors import error_responses
from app.schemas.ml import MlModelOut
from app.services import ml_service

router = APIRouter(tags=["admin-ml"])


@router.get("/ml/models", response_model=list[MlModelOut])
def models(admin: AdminUser, db: DbSession):  # noqa: ANN201
    """Every trained model, newest first: algorithm, hold-out metrics against the baselines, whether it was
    trained on synthetic or real data, and which one is active."""
    return ml_service.list_models(db)


@router.post("/ml/models/{model_id}/activate", response_model=MlModelOut, responses=error_responses(404, 409))
def activate(model_id: uuid.UUID, admin: AdminUser, db: DbSession, settings: AppSettings, client: Client):  # noqa: ANN201
    """Roll forward or back to this model (audited)."""
    return ml_service.activate(db, admin, model_id, settings.ml_artifacts_dir, client)
