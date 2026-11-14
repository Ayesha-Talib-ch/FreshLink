"""Account verification (spec 5.1, Technical Plan section 5.2).

State machine of users.verification_status:

    not_submitted --submit--> pending --approve--> approved
                                 |  \\--reject------> rejected       --resubmit--> pending
                                 \\----request info--> info_requested --resubmit--> pending

Only a PENDING request can be reviewed; anything else returns 409.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import FilePurpose, UserRole, VerificationRequestStatus, VerificationStatus
from app.models.category import ReceiverCategory
from app.models.profiles import ProviderProfile, ReceiverProfile, VolunteerProfile
from app.models.user import User
from app.models.verification import StoredFile, VerificationDocument, VerificationRequest, VerificationRequirement
from app.schemas.verification import VerificationSubmitIn
from app.services import notification_service
from app.services.audit_service import ClientInfo, record

PROFILE_MODELS = {
    UserRole.PROVIDER: ProviderProfile,
    UserRole.RECEIVER: ReceiverProfile,
    UserRole.VOLUNTEER: VolunteerProfile,
}

# Users in these states may (re)submit documents.
SUBMITTABLE = {VerificationStatus.NOT_SUBMITTED, VerificationStatus.REJECTED, VerificationStatus.INFO_REQUESTED}


def get_profile(db: Session, user: User) -> ProviderProfile | ReceiverProfile | VolunteerProfile | None:
    model = PROFILE_MODELS.get(user.role)
    return db.scalar(select(model).where(model.user_id == user.id)) if model else None


def requirements_for(
    db: Session, role: UserRole, receiver_category_id: uuid.UUID | None = None
) -> list[VerificationRequirement]:
    if role == UserRole.ADMIN:
        return []
    if role == UserRole.RECEIVER and receiver_category_id is not None:
        category = db.get(ReceiverCategory, receiver_category_id)
        if category is not None and not category.requires_documents:
            return []
    query = select(VerificationRequirement).where(
        VerificationRequirement.role == role, VerificationRequirement.is_active.is_(True)
    )
    if role == UserRole.RECEIVER:
        # General receiver rules + rules for this receiver's category.
        query = query.where(or_(VerificationRequirement.receiver_category_id.is_(None),
                                VerificationRequirement.receiver_category_id == receiver_category_id))
    return list(db.scalars(query.order_by(VerificationRequirement.is_required.desc(),
                                          VerificationRequirement.document_type)))


def requirements_for_user(db: Session, user: User) -> list[VerificationRequirement]:
    profile = get_profile(db, user)
    category_id = profile.receiver_category_id if isinstance(profile, ReceiverProfile) else None
    return requirements_for(db, user.role, category_id)


def latest_request(db: Session, user_id: uuid.UUID) -> VerificationRequest | None:
    return db.scalar(
        select(VerificationRequest).where(VerificationRequest.user_id == user_id)
        .order_by(VerificationRequest.submitted_at.desc()).limit(1)
    )


def my_verification(db: Session, user: User) -> dict[str, Any]:
    profile_completed = get_profile(db, user) is not None
    return {
        "verification_status": user.verification_status,
        "can_submit": user.role != UserRole.ADMIN and profile_completed and user.verification_status in SUBMITTABLE,
        "profile_completed": profile_completed,
        "requirements": requirements_for_user(db, user),
        "latest_request": latest_request(db, user.id),
    }


def submit(db: Session, user: User, body: VerificationSubmitIn) -> VerificationRequest:
    if user.role == UserRole.ADMIN:
        raise AppError(403, "FORBIDDEN", "Administrators don't need verification.")
    if user.verification_status == VerificationStatus.APPROVED:
        raise AppError(409, "ALREADY_VERIFIED", "Your account is already verified.")
    if user.verification_status == VerificationStatus.PENDING:
        raise AppError(409, "VERIFICATION_ALREADY_PENDING", "Your documents are already waiting for review.")
    if get_profile(db, user) is None:
        raise AppError(409, "PROFILE_REQUIRED", "Please complete your profile before submitting documents.")

    requirements = requirements_for_user(db, user)
    allowed_types = {r.document_type for r in requirements}
    for index, doc in enumerate(body.documents):
        if doc.document_type not in allowed_types:
            raise AppError(422, "UNKNOWN_DOCUMENT_TYPE", f"'{doc.document_type}' is not a document type for your account.",
                           {"field": f"documents.{index}.document_type", "allowed": sorted(allowed_types)})
        stored = db.get(StoredFile, doc.file_id)
        # Only your OWN uploaded verification files can be attached.
        if stored is None or stored.owner_user_id != user.id or stored.purpose != FilePurpose.VERIFICATION_DOC:
            raise AppError(422, "INVALID_FILE", "One of the files was not found. Please upload it again.",
                           {"field": f"documents.{index}.file_id"})

    missing = sorted({r.document_type for r in requirements if r.is_required}
                     - {d.document_type for d in body.documents})
    if missing:
        names = {r.document_type: r.name for r in requirements}
        raise AppError(422, "MISSING_REQUIRED_DOCUMENTS",
                       "Please upload: " + ", ".join(names[m] for m in missing) + ".", {"missing": missing})

    request = VerificationRequest(
        user_id=user.id,
        note=(body.note or "").strip() or None,
        documents=[VerificationDocument(file_id=d.file_id, document_type=d.document_type) for d in body.documents],
    )
    db.add(request)
    user.verification_status = VerificationStatus.PENDING
    try:
        db.flush()  # the request's id is needed for the notification link
        notification_service.notify(db, notification_service.admin_ids(db), "admin_alert", "Documents to review",
                                    f"{user.full_name} ({user.role.value}) submitted verification documents.",
                                    "verification_request", request.id)
        db.commit()
    except IntegrityError as exc:
        # Two submissions at the same moment: the partial unique index allows one.
        db.rollback()
        raise AppError(409, "VERIFICATION_ALREADY_PENDING", "Your documents are already waiting for review.") from exc
    db.refresh(request)
    return request


# --------------------------------------------------------------------- admin


def queue(
    db: Session, *, status: VerificationRequestStatus | None, role: UserRole | None, page: int, page_size: int
) -> dict[str, Any]:
    doc_count = (
        select(func.count()).where(VerificationDocument.verification_request_id == VerificationRequest.id)
        .correlate(VerificationRequest).scalar_subquery()
    )
    query = select(VerificationRequest, User, doc_count.label("document_count")).join(
        User, User.id == VerificationRequest.user_id
    )
    if status is not None:
        query = query.where(VerificationRequest.status == status)
    if role is not None:
        query = query.where(User.role == role)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    # Oldest first, so nobody waits forever at the bottom of the queue.
    rows = db.execute(
        query.order_by(VerificationRequest.submitted_at, VerificationRequest.id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        {"id": r.id, "status": r.status, "submitted_at": r.submitted_at, "reviewed_at": r.reviewed_at,
         "user_id": u.id, "full_name": u.full_name, "email": u.email, "role": u.role, "document_count": n}
        for r, u, n in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def admin_detail(db: Session, request_id: uuid.UUID) -> dict[str, Any]:
    request = db.get(VerificationRequest, request_id)
    if request is None:
        raise AppError(404, "NOT_FOUND", "Verification request not found.")
    user = db.get(User, request.user_id)
    profile = get_profile(db, user)
    history = db.scalars(
        select(VerificationRequest).where(VerificationRequest.user_id == user.id, VerificationRequest.id != request.id)
        .order_by(VerificationRequest.submitted_at.desc())
    ).all()
    return {
        **{c: getattr(request, c) for c in ("id", "status", "note", "submitted_at", "reviewed_at",
                                             "review_notes", "documents")},
        "user": user,
        f"{user.role.value}_profile": profile,
        "requirements": requirements_for_user(db, user),
        "history": history,
    }


_DECISIONS = {
    "approve": (VerificationRequestStatus.APPROVED, VerificationStatus.APPROVED),
    "reject": (VerificationRequestStatus.REJECTED, VerificationStatus.REJECTED),
    "request_info": (VerificationRequestStatus.INFO_REQUESTED, VerificationStatus.INFO_REQUESTED),
}


def decide(
    db: Session, admin: User, request_id: uuid.UUID, decision: str, notes: str | None, client: ClientInfo
) -> VerificationRequest:
    notes_text = notes or ""
    # Lock the request row: if two admins click at the same moment, the second
    # one waits, then sees it's no longer pending and gets a 409.
    request = db.scalar(select(VerificationRequest).where(VerificationRequest.id == request_id).with_for_update(of=VerificationRequest))
    if request is None:
        raise AppError(404, "NOT_FOUND", "Verification request not found.")
    if request.status != VerificationRequestStatus.PENDING:
        raise AppError(409, "ALREADY_REVIEWED", f"This request was already reviewed ({request.status.value}).",
                       {"status": request.status.value})

    user = db.get(User, request.user_id)
    request_status, user_status = _DECISIONS[decision]
    before = {"verification_status": user.verification_status.value}
    request.status = request_status
    request.reviewed_by = admin.id
    request.reviewed_at = datetime.now(UTC)
    request.review_notes = notes
    user.verification_status = user_status
    titles = {"approve": "Account approved", "reject": "Account not approved",
              "request_info": "More information needed"}
    messages = {"approve": "Your FreshLink account is approved. You can now use all features.",
                "reject": f"Your verification was not approved: {notes_text}",
                "request_info": f"An administrator needs more information: {notes_text}"}
    notification_service.notify(db, [user.id], "verification_result", titles[decision], messages[decision], "verification", None)
    record(db, actor=admin, action=f"verification.{decision}", entity_type="user", entity_id=user.id,
           client=client, reason=notes, before=before,
           after={"verification_status": user_status.value, "verification_request_id": str(request.id)})
    db.commit()
    db.refresh(request)
    return request


def set_eligibility(
    db: Session, admin: User, receiver_user_id: uuid.UUID, is_eligible: bool, notes: str | None, client: ClientInfo
) -> ReceiverProfile:
    # Lock ONLY the profile row (of=...): the profile auto-joins its category, and
    # PostgreSQL can't lock rows on the optional side of such a join.
    profile = db.scalar(
        select(ReceiverProfile).where(ReceiverProfile.user_id == receiver_user_id)
        .with_for_update(of=ReceiverProfile)
    )
    if profile is None:
        raise AppError(404, "NOT_FOUND", "Receiver profile not found.")
    notes = " ".join((notes or "").split()) or None
    if not is_eligible and (notes is None or len(notes) < 5):
        raise AppError(422, "REASON_REQUIRED", "Please give a reason when marking a receiver as not eligible.",
                       {"field": "notes"})
    before = {"is_eligible": profile.is_eligible, "eligibility_notes": profile.eligibility_notes}
    profile.is_eligible = is_eligible
    profile.eligibility_notes = notes
    record(db, actor=admin, action="receiver.eligibility", entity_type="user", entity_id=receiver_user_id,
           client=client, reason=notes, before=before, after={"is_eligible": is_eligible, "eligibility_notes": notes})
    db.commit()
    db.refresh(profile)
    return profile
