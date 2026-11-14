"""Admin user management: search, view, activate / deactivate / suspend, account history and
creating administrators (spec 4.4 "User Management"; plan 4.12 and 5.2). Every change is audited."""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import hash_password
from app.domain.enums import AccountStatus, AssignmentStatus, CollectionStatus, UserRole, VerificationStatus
from app.models.allocation import Allocation
from app.models.assignment import VolunteerAssignment
from app.models.audit import AuditLog
from app.models.collection import Collection
from app.models.listing import FoodListing
from app.models.profiles import ProviderProfile, ReceiverProfile, VolunteerProfile
from app.models.request import FoodRequest
from app.models.user import User
from app.models.verification import VerificationRequest
from app.schemas.admin_users import AdminCreateIn
from app.services import notification_service
from app.services.audit_service import ClientInfo, record
from app.services.auth_service import _ensure_unique, end_all_sessions

PROFILE_MODELS = {UserRole.PROVIDER: ProviderProfile, UserRole.RECEIVER: ReceiverProfile,
                  UserRole.VOLUNTEER: VolunteerProfile}


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _profile_names(db: Session, users: list[User]) -> dict[uuid.UUID, str]:
    ids = [u.id for u in users]
    names: dict[uuid.UUID, str] = {}
    names.update(db.execute(select(ProviderProfile.user_id, ProviderProfile.business_name)
                            .where(ProviderProfile.user_id.in_(ids))).all())
    names.update(db.execute(select(ReceiverProfile.user_id, ReceiverProfile.receiver_name)
                            .where(ReceiverProfile.user_id.in_(ids))).all())
    return names


def list_users(db: Session, *, q: str | None, role: UserRole | None, account_status: AccountStatus | None,
               verification_status: VerificationStatus | None, page: int, page_size: int) -> dict[str, Any]:
    query = select(User).where(User.deleted_at.is_(None))
    search = (q or "").strip()
    if search:
        like = f"%{_escape_like(search)}%"
        digits = "".join(ch for ch in search if ch.isdigit())
        conditions = [User.full_name.ilike(like, escape="\\"), User.email.ilike(like, escape="\\"),
                      User.id.in_(select(ProviderProfile.user_id).where(
                          ProviderProfile.business_name.ilike(like, escape="\\"))),
                      User.id.in_(select(ReceiverProfile.user_id).where(
                          ReceiverProfile.receiver_name.ilike(like, escape="\\")))]
        if len(digits) >= 4:  # phones are stored as +923001234567; "0300 123" should still match
            conditions.append(User.phone.contains(digits.lstrip("0")))
        query = query.where(or_(*conditions))
    if role is not None:
        query = query.where(User.role == role)
    if account_status is not None:
        query = query.where(User.account_status == account_status)
    if verification_status is not None:
        query = query.where(User.verification_status == verification_status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    users = list(db.scalars(query.order_by(User.created_at.desc(), User.id)
                            .offset((page - 1) * page_size).limit(page_size)))
    names = _profile_names(db, users)
    for u in users:
        u.profile_name = names.get(u.id)
    return {"items": users, "total": total, "page": page, "page_size": page_size}


def _get(db: Session, user_id: uuid.UUID, *, lock: bool = False) -> User:
    query = select(User).where(User.id == user_id, User.deleted_at.is_(None))
    user = db.scalar(query.with_for_update(of=User) if lock else query)
    if user is None:
        raise AppError(404, "NOT_FOUND", "User not found.")
    return user


def _profile(db: Session, user: User) -> dict[str, Any] | None:
    model = PROFILE_MODELS.get(user.role)
    p = db.scalar(select(model).where(model.user_id == user.id)) if model else None
    if p is None:
        return None
    if isinstance(p, ProviderProfile):
        return {"id": p.id, "business_name": p.business_name, "category": p.category.name, "address": p.address,
                "contact_person": p.contact_person, "contact_phone": p.contact_phone, "opening_hours": p.opening_hours}
    if isinstance(p, ReceiverProfile):
        return {"id": p.id, "receiver_name": p.receiver_name, "category": p.category.name, "address": p.address,
                "max_pickup_radius_km": float(p.max_pickup_radius_km), "has_own_transport": p.has_own_transport,
                "household_size": p.household_size, "is_eligible": p.is_eligible,
                "eligibility_notes": p.eligibility_notes}
    return {"id": p.id, "address": p.address, "has_vehicle": p.has_vehicle,
            "vehicle_type": p.vehicle_type.value if p.vehicle_type else None,
            "service_radius_km": float(p.service_radius_km), "is_available_now": p.is_available_now}


def _counts(db: Session, user: User, profile: dict[str, Any] | None) -> dict[str, int]:
    def n(query) -> int:  # noqa: ANN001
        return db.scalar(select(func.count()).select_from(query.subquery())) or 0

    if profile is None:
        return {}
    pid = profile["id"]
    if user.role == UserRole.PROVIDER:
        return {"listings": n(select(FoodListing.id).where(FoodListing.provider_id == pid)),
                "completed_distributions": n(select(Collection.id).join(Allocation).join(
                    FoodListing, FoodListing.id == Allocation.listing_id).where(
                    FoodListing.provider_id == pid, Collection.status == CollectionStatus.RECEIVED))}
    if user.role == UserRole.RECEIVER:
        return {"requests": n(select(FoodRequest.id).where(FoodRequest.receiver_id == pid)),
                "completed_collections": n(select(Collection.id).join(Allocation).where(
                    Allocation.receiver_id == pid, Collection.status == CollectionStatus.RECEIVED))}
    tasks = select(VolunteerAssignment.id).where(VolunteerAssignment.volunteer_id == pid)
    return {"tasks": n(tasks),
            "completed_deliveries": n(tasks.where(VolunteerAssignment.status == AssignmentStatus.COMPLETED)),
            "failed_deliveries": n(tasks.where(VolunteerAssignment.status == AssignmentStatus.FAILED))}


def detail(db: Session, user_id: uuid.UUID) -> User:
    user = _get(db, user_id)
    user.profile = _profile(db, user)
    user.counts = _counts(db, user, user.profile)
    user.profile_name = (user.profile or {}).get("business_name") or (user.profile or {}).get("receiver_name")
    return user


# ------------------------------------------------------------ status changes

_ACTIONS = {"activate": AccountStatus.ACTIVE, "deactivate": AccountStatus.DEACTIVATED,
            "suspend": AccountStatus.SUSPENDED}


def change_status(db: Session, admin: User, user_id: uuid.UUID, action: str, reason: str | None,
                  client: ClientInfo) -> User:
    """Plan 5.2: suspending or deactivating ends every session at once (token_version + refresh tokens)."""
    target_status = _ACTIONS[action]
    user = _get(db, user_id, lock=True)  # two admins clicking at once: one waits for the other
    if user.id == admin.id:
        raise AppError(409, "CANNOT_CHANGE_OWN_ACCOUNT", "You can't change the status of your own account.")
    if user.role == UserRole.ADMIN and not admin.is_super_admin:
        raise AppError(403, "FORBIDDEN", "Only a super administrator can change another administrator's account.")
    if user.account_status == target_status:
        raise AppError(409, "STATUS_UNCHANGED", f"This account is already {target_status.value}.",
                       {"account_status": target_status.value})
    before = user.account_status
    user.account_status = target_status
    if target_status != AccountStatus.ACTIVE:
        end_all_sessions(db, user)  # logged out on every device, immediately
    else:
        notification_service.notify(db, [user.id], "admin_alert", "Account reactivated",
                                    "Your FreshLink account is active again." + (f" Note: {reason}" if reason else ""),
                                    "user", user.id)
    record(db, actor=admin, action=f"user.{action}", entity_type="user", entity_id=user.id, client=client,
           reason=reason, before={"account_status": before.value}, after={"account_status": target_status.value})
    db.commit()
    return detail(db, user.id)


# ------------------------------------------------------------------- history


def history(db: Session, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """Account history (plan 4.12): registration, last login, verification requests, and every audit
    entry about this account or made by it. Newest first."""
    user = _get(db, user_id)
    items: list[dict[str, Any]] = [{"at": user.created_at, "kind": "account.registered",
                                    "summary": f"Registered as a {user.role.value}"}]
    if user.last_login_at:
        items.append({"at": user.last_login_at, "kind": "account.last_login", "summary": "Last login"})
    requests = list(db.scalars(select(VerificationRequest).where(VerificationRequest.user_id == user.id)))
    for v in requests:
        items.append({"at": v.submitted_at, "kind": "verification.submitted",
                      "summary": f"Submitted {len(v.documents)} verification document(s)", "reason": v.note})
        if v.reviewed_at:
            items.append({"at": v.reviewed_at, "kind": f"verification.{v.status.value}",
                          "summary": f"Verification {v.status.value.replace('_', ' ')}", "reason": v.review_notes})
    about = [user.id, *(v.id for v in requests)]
    for a in db.scalars(select(AuditLog).where(or_(AuditLog.entity_id.in_(about), AuditLog.actor_id == user.id))
                        .order_by(AuditLog.created_at.desc()).limit(200)):
        mine = a.actor_id == user.id and a.entity_id not in about
        items.append({"at": a.created_at, "kind": a.action,
                      "summary": f"{'Did' if mine else 'Admin action'}: {a.action.replace('.', ' ').replace('_', ' ')}",
                      "actor_email": a.actor_email, "reason": a.reason})
    items.sort(key=lambda i: i["at"], reverse=True)
    return items


# ------------------------------------------------------------ create an admin


def create_admin(db: Session, super_admin: User, body: AdminCreateIn, client: ClientInfo) -> User:
    """Plan 4.12: only a super admin can create administrator accounts (public sign-up never can)."""
    _ensure_unique(db, body.email, body.phone)
    user = User(email=body.email, phone=body.phone, full_name=body.full_name,
                password_hash=hash_password(body.password), role=UserRole.ADMIN,
                is_super_admin=body.is_super_admin, verification_status=VerificationStatus.APPROVED)
    db.add(user)
    try:
        db.flush()
    except IntegrityError as exc:  # same email/phone created at the same moment
        db.rollback()
        raise AppError(409, "EMAIL_OR_PHONE_ALREADY_REGISTERED",
                       "An account with this email or phone number already exists.") from exc
    record(db, actor=super_admin, action="admin.create", entity_type="user", entity_id=user.id, client=client,
           after={"email": user.email, "is_super_admin": user.is_super_admin})  # never the password
    db.commit()
    return detail(db, user.id)
