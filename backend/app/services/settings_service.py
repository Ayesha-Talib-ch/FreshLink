"""Platform settings (spec 4.4.11 "platform settings"; plan 3.3 / 4.12).

Each setting has a default here, a check that refuses bad values, and a description. Only values an
administrator changed are stored (platform_settings). Every setting below is read by real code -
there are no settings that do nothing."""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.engines.matching import PLATFORM_MAX_RADIUS_KM, WEIGHTS
from app.models.setting import PlatformSetting
from app.models.user import User
from app.services.audit_service import ClientInfo, record


class SettingError(ValueError):
    pass


def _number(low: float, high: float, integer: bool = False) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:  # noqa: ANN401
        if isinstance(value, bool) or not isinstance(value, int | float) or (integer and not isinstance(value, int)):
            raise SettingError(f"Must be {'a whole number' if integer else 'a number'}.")
        if not low <= value <= high:
            raise SettingError(f"Must be between {low:g} and {high:g}.")
        return value
    return check


def _one_of(*choices: str) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:  # noqa: ANN401
        if value not in choices:
            raise SettingError("Must be one of: " + ", ".join(choices) + ".")
        return value
    return check


def _weights(value: Any) -> dict[str, float]:  # noqa: ANN401
    if not isinstance(value, dict) or set(value) != set(WEIGHTS):
        raise SettingError("Give exactly these parts: " + ", ".join(WEIGHTS) + ".")
    if any(isinstance(v, bool) or not isinstance(v, int | float) or v < 0 or v > 1 for v in value.values()):
        raise SettingError("Each weight must be a number from 0 to 1.")
    if not math.isclose(sum(value.values()), 1.0, abs_tol=0.001):
        raise SettingError(f"The weights must add up to 1 (they add up to {sum(value.values()):.3f}).")
    return {k: float(value[k]) for k in WEIGHTS}


@dataclass(frozen=True)
class Setting:
    default: Any
    check: Callable[[Any], Any]
    description: str


SETTINGS: dict[str, Setting] = {
    "matching_weights": Setting(
        dict(WEIGHTS), _weights,
        "How much each part counts when ranking receivers for a listing (plan 7.4). Must add up to 1."),
    "max_travel_km_with_transport": Setting(
        PLATFORM_MAX_RADIUS_KM, _number(5, 200),
        "Receivers with their own transport can be matched with food up to this many km away."),
    "default_allocation_policy": Setting(
        "priority_weighted", _one_of("priority_weighted", "proportional_fair", "first_come"),
        "The allocation policy used when the provider or admin doesn't choose one."),
    "match_notify_top_k": Setting(
        5, _number(0, 50, integer=True),
        "How many best-matching receivers get a 'Food you may need' notification for a new listing (0 = none)."),
    "listing_max_days_ahead": Setting(
        30, _number(1, 90, integer=True),
        "How many days ahead a listing's collection deadline may be."),
}


def get(db: Session, key: str) -> Any:  # noqa: ANN401
    row = db.get(PlatformSetting, key)
    return row.value if row is not None else SETTINGS[key].default


def _out(key: str, row: PlatformSetting | None, emails: dict) -> dict[str, Any]:
    s = SETTINGS[key]
    return {"key": key, "value": row.value if row else s.default, "default": s.default, "is_default": row is None,
            "description": s.description, "updated_at": row.updated_at if row else None,
            "updated_by_email": emails.get(row.updated_by) if row else None}


def list_all(db: Session) -> list[dict[str, Any]]:
    rows = {r.key: r for r in db.scalars(select(PlatformSetting))}
    emails = dict(db.execute(select(User.id, User.email).where(
        User.id.in_({r.updated_by for r in rows.values() if r.updated_by}))).all())
    return [_out(key, rows.get(key), emails) for key in SETTINGS]


def _known(key: str) -> Setting:
    if key not in SETTINGS:
        raise AppError(404, "NOT_FOUND", "Unknown setting.", {"known": list(SETTINGS)})
    return SETTINGS[key]


def update(db: Session, admin: User, key: str, value: Any, client: ClientInfo) -> dict[str, Any]:  # noqa: ANN401
    setting = _known(key)
    try:
        value = setting.check(value)
    except SettingError as exc:
        raise AppError(422, "INVALID_SETTING", str(exc), {"key": key}) from exc
    before = get(db, key)
    row = db.get(PlatformSetting, key, with_for_update=True) or PlatformSetting(key=key)
    row.value, row.updated_by = value, admin.id
    row.updated_at = func.now()
    db.add(row)
    record(db, actor=admin, action="setting.update", entity_type="setting", entity_id=None, client=client,
           reason=key, before={"value": before}, after={"value": value})
    db.commit()
    return next(s for s in list_all(db) if s["key"] == key)


def reset(db: Session, admin: User, key: str, client: ClientInfo) -> dict[str, Any]:
    setting = _known(key)
    row = db.get(PlatformSetting, key, with_for_update=True)
    if row is not None:
        record(db, actor=admin, action="setting.reset", entity_type="setting", entity_id=None, client=client,
               reason=key, before={"value": row.value}, after={"value": setting.default})
        db.delete(row)
        db.commit()
    return next(s for s in list_all(db) if s["key"] == key)

