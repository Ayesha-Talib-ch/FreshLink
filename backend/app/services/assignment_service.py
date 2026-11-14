"""Volunteer assignments (spec 4.3, 5.6; Technical Plan section 4.9).

Lifecycle of a delivery task:
  open --claim--> accepted                       (a volunteer takes an open task)
  open --assign (admin)--> assigned --accept--> accepted
                                    --decline (reason)--> declined  (+ a NEW open task)
  accepted -> en_route_pickup -> collected (confirms the handover quantity)
           -> en_route_dropoff -> delivered -> completed (when the receiver confirms receipt)
  failed (reason) from any working step; cancelled by an admin or when the collection ends

Privacy (plan 5.3): a volunteer sees the receiver's address and both sides' phone numbers
only once they have ACCEPTED the task, and no longer 7 days after it is completed.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import (
    ACTIVE_ASSIGNMENT_STATUSES,
    AccountStatus,
    AssignmentStatus,
    CollectionMethod,
    UserRole,
    VerificationStatus,
)
from app.domain.state_machines import VOLUNTEER_STEPS, check_assignment_transition
from app.engines.geo import haversine_km, haversine_km_sql
from app.engines.volunteer_ranking import VolunteerFacts, suggest
from app.models.allocation import Allocation
from app.models.assignment import AssignmentStatusHistory, VolunteerAssignment
from app.models.collection import Collection
from app.models.listing import FoodListing
from app.models.profiles import ProviderProfile, ReceiverProfile, VolunteerProfile
from app.models.user import User
from app.services.audit_service import ClientInfo, record

CONTACTS_VISIBLE = {AssignmentStatus.ACCEPTED, AssignmentStatus.EN_ROUTE_PICKUP, AssignmentStatus.COLLECTED,
                    AssignmentStatus.EN_ROUTE_DROPOFF, AssignmentStatus.DELIVERED}
CONTACTS_AFTER_COMPLETION = timedelta(days=7)
BEFORE_PICKUP = {AssignmentStatus.OPEN, AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED,
                 AssignmentStatus.EN_ROUTE_PICKUP}


def _now() -> datetime:
    return datetime.now(UTC)


def _history(db: Session, a: VolunteerAssignment, target: AssignmentStatus, user_id: uuid.UUID | None,
             note: str | None = None) -> None:
    check_assignment_transition(a.status, target)
    db.add(AssignmentStatusHistory(assignment_id=a.id, from_status=a.status.value, to_status=target.value,
                                   changed_by=user_id, note=note))
    a.status = target
    a.version += 1


# ------------------------------------------------- hooks used by collection_service


def create_open(db: Session, collection: Collection, created_by: uuid.UUID | None = None) -> VolunteerAssignment:
    """A collection needs a volunteer: open a task. The caller commits."""
    listing, receiver = collection.listing, collection.receiver
    task = VolunteerAssignment(
        collection_id=collection.id, pickup_address=listing.pickup_address,
        pickup_latitude=listing.pickup_latitude, pickup_longitude=listing.pickup_longitude,
        dropoff_address=receiver.address, dropoff_latitude=receiver.latitude, dropoff_longitude=receiver.longitude,
        scheduled_pickup_at=collection.scheduled_pickup_at,
    )
    db.add(task)
    db.flush()
    db.add(AssignmentStatusHistory(assignment_id=task.id, from_status=None, to_status=AssignmentStatus.OPEN.value,
                                   changed_by=created_by, note="delivery task opened"))
    return task


def active_for(db: Session, collection_id: uuid.UUID) -> VolunteerAssignment | None:
    return db.scalar(select(VolunteerAssignment).where(
        VolunteerAssignment.collection_id == collection_id,
        VolunteerAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES)).with_for_update(of=VolunteerAssignment))


def cancel_active(db: Session, collection_id: uuid.UUID, user_id: uuid.UUID | None, note: str) -> None:
    task = active_for(db, collection_id)
    if task is not None:
        if task.status not in BEFORE_PICKUP:
            raise AppError(409, "VOLUNTEER_HAS_THE_FOOD",
                           "A volunteer already has the food; report a failed delivery instead.")
        _history(db, task, AssignmentStatus.CANCELLED, user_id, note)


def on_provider_handover(db: Session, collection_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """The provider confirmed the handover: the volunteer has collected the food."""
    task = active_for(db, collection_id)
    if task is not None and task.status in (AssignmentStatus.ACCEPTED, AssignmentStatus.EN_ROUTE_PICKUP):
        _history(db, task, AssignmentStatus.COLLECTED, user_id, "handover confirmed by the provider")


def on_receipt(db: Session, collection_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """The receiver confirmed receipt: the delivery task is complete."""
    task = active_for(db, collection_id)
    if task is not None:
        if task.status in (AssignmentStatus.COLLECTED, AssignmentStatus.EN_ROUTE_DROPOFF):
            _history(db, task, AssignmentStatus.DELIVERED, user_id, "receiver confirmed receipt")
        if task.status == AssignmentStatus.DELIVERED:
            _history(db, task, AssignmentStatus.COMPLETED, user_id, "receiver confirmed receipt")
            task.completed_at = _now()


def on_collection_failed(db: Session, collection_id: uuid.UUID, user_id: uuid.UUID, reason: str) -> None:
    task = active_for(db, collection_id)
    if task is not None:
        target = AssignmentStatus.CANCELLED if task.status in (AssignmentStatus.OPEN, AssignmentStatus.ASSIGNED) \
            else AssignmentStatus.FAILED
        task.failure_reason = reason if target == AssignmentStatus.FAILED else None
        _history(db, task, target, user_id, f"collection failed: {reason}")


# -------------------------------------------------------------------- volunteers


def _volunteer_profile(db: Session, user: User) -> VolunteerProfile:
    profile = db.scalar(select(VolunteerProfile).where(VolunteerProfile.user_id == user.id))
    if profile is None:
        raise AppError(409, "PROFILE_REQUIRED", "Please complete your volunteer profile first.")
    return profile


def _task_for_update(db: Session, task_id: uuid.UUID) -> VolunteerAssignment:
    task = db.scalar(select(VolunteerAssignment).where(VolunteerAssignment.id == task_id)
                     .with_for_update(of=VolunteerAssignment))
    if task is None:
        raise AppError(404, "NOT_FOUND", "Task not found.")
    return task


def _mine(task: VolunteerAssignment, profile: VolunteerProfile) -> None:
    if task.volunteer_id != profile.id:
        raise AppError(404, "NOT_FOUND", "Task not found.")


def open_tasks(db: Session, user: User) -> list[dict[str, Any]]:
    """Open tasks whose pickup is inside this volunteer's service area, nearest first."""
    profile = _volunteer_profile(db, user)
    distance = haversine_km_sql(VolunteerAssignment.pickup_latitude, VolunteerAssignment.pickup_longitude,
                                profile.latitude, profile.longitude)
    rows = db.execute(select(VolunteerAssignment, distance).where(
        VolunteerAssignment.status == AssignmentStatus.OPEN, distance <= float(profile.service_radius_km))
        .order_by(distance, VolunteerAssignment.created_at)).unique().all()
    return [to_out(task, user, distance_km=round(float(d), 2)) for task, d in rows]


def claim(db: Session, user: User, task_id: uuid.UUID) -> dict[str, Any]:
    profile = _volunteer_profile(db, user)
    task = _task_for_update(db, task_id)  # lock: two volunteers can't both claim it
    if task.status != AssignmentStatus.OPEN:
        raise AppError(409, "TASK_NOT_OPEN", "Another volunteer has already taken this task.")
    if haversine_km(task.pickup_latitude, task.pickup_longitude, profile.latitude, profile.longitude) \
            > float(profile.service_radius_km):
        raise AppError(422, "OUTSIDE_SERVICE_AREA", "This pickup is outside your service area.")
    task.volunteer_id = profile.id
    task.accepted_at = _now()
    _history(db, task, AssignmentStatus.ACCEPTED, user.id, "claimed by the volunteer")
    db.commit()
    db.refresh(task)
    return to_out(task, user)


def accept(db: Session, user: User, task_id: uuid.UUID) -> dict[str, Any]:
    profile = _volunteer_profile(db, user)
    task = _task_for_update(db, task_id)
    _mine(task, profile)
    task.accepted_at = _now()
    _history(db, task, AssignmentStatus.ACCEPTED, user.id, "accepted")
    db.commit()
    db.refresh(task)
    return to_out(task, user)


def decline(db: Session, user: User, task_id: uuid.UUID, reason: str) -> dict[str, Any]:
    profile = _volunteer_profile(db, user)
    task = _task_for_update(db, task_id)
    _mine(task, profile)
    task.decline_reason = reason
    _history(db, task, AssignmentStatus.DECLINED, user.id, reason)
    db.flush()
    create_open(db, task.collection, user.id)  # the delivery still has to happen
    db.commit()
    db.refresh(task)
    return to_out(task, user)


def step(db: Session, user: User, task_id: uuid.UUID, target: AssignmentStatus, note: str | None,
         quantity: Decimal | None, client: ClientInfo) -> dict[str, Any]:
    """The volunteer's own progress updates (spec 4.3.4)."""
    from app.services import collection_service  # here, to avoid a circular import

    profile = _volunteer_profile(db, user)
    task = _task_for_update(db, task_id)
    _mine(task, profile)
    if target not in VOLUNTEER_STEPS:
        raise AppError(422, "VALIDATION_ERROR", f"'{target.value}' is not a step a volunteer can report.")
    check_assignment_transition(task.status, target)
    note = " ".join((note or "").split()) or None

    if target == AssignmentStatus.COLLECTED:
        if quantity is None:
            raise AppError(422, "QUANTITY_REQUIRED", "Enter the quantity you collected.", {"field": "quantity"})
        collection = db.scalar(select(Collection).where(Collection.id == task.collection_id)
                               .with_for_update(of=Collection))
        collection_service.perform_handover(db, collection, user, quantity, note)
        _history(db, task, AssignmentStatus.COLLECTED, user.id, note or "food collected")
    elif target == AssignmentStatus.FAILED:
        if note is None or len(note) < 5:
            raise AppError(422, "REASON_REQUIRED", "Please explain what went wrong.", {"field": "note"})
        task.failure_reason = note
        _history(db, task, AssignmentStatus.FAILED, user.id, note)
        db.flush()  # save "failed" first, so the collection's failure handling sees it as finished
        collection_service.perform_failure(db, task.collection_id, user, f"volunteer delivery failed: {note}", client)
    else:
        _history(db, task, target, user.id, note)
    db.commit()
    db.refresh(task)
    return to_out(task, user)


# ------------------------------------------------------------------------ admins


def suggestions(db: Session, task_id: uuid.UUID) -> dict[str, Any]:
    task = db.get(VolunteerAssignment, task_id)
    if task is None:
        raise AppError(404, "NOT_FOUND", "Task not found.")
    volunteers = db.execute(select(VolunteerProfile, User).join(User, User.id == VolunteerProfile.user_id).where(
        User.deleted_at.is_(None), User.account_status == AccountStatus.ACTIVE,
        User.verification_status == VerificationStatus.APPROVED)).unique().all()
    counts = dict(db.execute(select(VolunteerAssignment.volunteer_id, func.count()).where(
        VolunteerAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES), VolunteerAssignment.volunteer_id.isnot(None))
        .group_by(VolunteerAssignment.volunteer_id)).all())
    finished = db.execute(select(VolunteerAssignment.volunteer_id, VolunteerAssignment.status, func.count()).where(
        VolunteerAssignment.status.in_([AssignmentStatus.COMPLETED, AssignmentStatus.FAILED]))
        .group_by(VolunteerAssignment.volunteer_id, VolunteerAssignment.status)).all()
    done = {(vid, status): n for vid, status, n in finished}
    facts, names = [], {}
    for profile, user in volunteers:
        names[profile.id] = (profile, user)
        facts.append(VolunteerFacts(
            volunteer_id=profile.id, name=user.full_name,
            distance_km=haversine_km(task.pickup_latitude, task.pickup_longitude, profile.latitude, profile.longitude),
            service_radius_km=float(profile.service_radius_km), has_vehicle=profile.has_vehicle,
            vehicle_type=profile.vehicle_type.value if profile.vehicle_type else None,
            is_available_now=profile.is_available_now,
            weekly_slots=[(s.day_of_week, s.start_time, s.end_time) for s in profile.availability],
            active_tasks=counts.get(profile.id, 0), completed_tasks=done.get((profile.id, "completed"), 0),
            failed_tasks=done.get((profile.id, "failed"), 0)))
    ranked, excluded = suggest(facts, task.scheduled_pickup_at, task.collection.approved_quantity)
    card = lambda vid: {"id": vid, "full_name": names[vid][1].full_name,  # noqa: E731
                        "has_vehicle": names[vid][0].has_vehicle, "is_available_now": names[vid][0].is_available_now}
    return {"task_id": task.id,
            "suggestions": [{"score": s.score, "components": s.components, "reasons": s.reasons,
                             "volunteer": card(s.volunteer_id)} for s in ranked],
            "excluded": [{"reason": reason, "volunteer": card(vid)} for vid, reason in excluded]}


def assign(db: Session, admin: User, task_id: uuid.UUID, volunteer_id: uuid.UUID, client: ClientInfo) -> dict[str, Any]:
    """Assign an open task, or REASSIGN one that hasn't started (the old one is cancelled)."""
    task = _task_for_update(db, task_id)
    volunteer = db.get(VolunteerProfile, volunteer_id)
    if volunteer is None or volunteer.user.account_status != AccountStatus.ACTIVE or \
            volunteer.user.verification_status != VerificationStatus.APPROVED:
        raise AppError(422, "VOLUNTEER_NOT_AVAILABLE", "Choose an approved, active volunteer.", {"field": "volunteer_id"})
    if task.status not in (AssignmentStatus.OPEN, AssignmentStatus.ASSIGNED, AssignmentStatus.ACCEPTED):
        raise AppError(409, "TASK_ALREADY_STARTED", f"A '{task.status.value}' task can't be reassigned.")
    before = {"status": task.status.value, "volunteer_id": str(task.volunteer_id) if task.volunteer_id else None}
    if task.status != AssignmentStatus.OPEN:  # reassign: close the old task, open a new one
        _history(db, task, AssignmentStatus.CANCELLED, admin.id, "reassigned by an administrator")
        db.flush()
        task = create_open(db, task.collection, admin.id)
    task.volunteer_id = volunteer.id
    task.assigned_by = admin.id
    _history(db, task, AssignmentStatus.ASSIGNED, admin.id, f"assigned to {volunteer.user.full_name}")
    record(db, actor=admin, action="assignment.assign", entity_type="volunteer_assignment", entity_id=task.id,
           client=client, before=before, after={"status": "assigned", "volunteer_id": str(volunteer.id)})
    try:
        db.commit()
    except IntegrityError as exc:  # another admin changed this task at the same moment
        db.rollback()
        raise AppError(409, "CONFLICT", "This task was changed at the same time. Please reload it.") from exc
    db.refresh(task)
    return to_out(task, admin)


def cancel(db: Session, admin: User, task_id: uuid.UUID, reason: str, client: ClientInfo) -> dict[str, Any]:
    """Stop using a volunteer for this collection: it becomes a self-pickup again."""
    task = _task_for_update(db, task_id)
    if task.status not in BEFORE_PICKUP:
        raise AppError(409, "VOLUNTEER_HAS_THE_FOOD", "A volunteer already has the food; report a failure instead.")
    _history(db, task, AssignmentStatus.CANCELLED, admin.id, reason)
    task.collection.method = CollectionMethod.SELF_PICKUP
    record(db, actor=admin, action="assignment.cancel", entity_type="volunteer_assignment", entity_id=task.id,
           client=client, reason=reason)
    db.commit()
    db.refresh(task)
    return to_out(task, admin)


# ----------------------------------------------------------------------- reading


def _involved(task: VolunteerAssignment, user: User) -> str | None:
    """How the user relates to the task: 'admin', 'volunteer', 'provider', 'receiver' or None."""
    if user.role == UserRole.ADMIN:
        return "admin"
    if task.volunteer is not None and task.volunteer.user_id == user.id:
        return "volunteer"
    if task.collection.provider.user_id == user.id:
        return "provider"
    if task.collection.receiver.user_id == user.id:
        return "receiver"
    return None


def _volunteer_sees_contacts(task: VolunteerAssignment) -> bool:
    if task.status in CONTACTS_VISIBLE:
        return True
    return (task.status == AssignmentStatus.COMPLETED and task.completed_at is not None
            and _now() - task.completed_at <= CONTACTS_AFTER_COMPLETION)


def to_out(task: VolunteerAssignment, user: User, distance_km: float | None = None) -> dict[str, Any]:
    role = _involved(task, user)
    c = task.collection
    listing, provider, receiver = c.listing, c.provider, c.receiver
    contacts = role in ("admin", "provider", "receiver") or (role == "volunteer" and _volunteer_sees_contacts(task))
    volunteer = task.volunteer
    return {
        "id": task.id, "collection_id": c.id, "status": task.status, "food_name": listing.food_name,
        "unit": listing.unit, "quantity": c.approved_quantity, "handed_over_quantity": c.handed_over_quantity,
        "pickup_address": task.pickup_address, "pickup_latitude": task.pickup_latitude,
        "pickup_longitude": task.pickup_longitude, "scheduled_pickup_at": task.scheduled_pickup_at,
        "collection_deadline": listing.collection_deadline,
        "delivery_distance_km": round(haversine_km(task.pickup_latitude, task.pickup_longitude,
                                                   task.dropoff_latitude, task.dropoff_longitude), 2),
        "distance_km": distance_km,
        # Hidden from volunteers until they accept (plan 5.3):
        "dropoff_address": task.dropoff_address if contacts else None,
        "dropoff_latitude": task.dropoff_latitude if contacts else None,
        "dropoff_longitude": task.dropoff_longitude if contacts else None,
        "provider_contact": ({"business_name": provider.business_name, "contact_person": provider.contact_person,
                              "contact_phone": provider.contact_phone} if contacts else None),
        "receiver_contact": ({"receiver_name": receiver.receiver_name, "contact_name": receiver.contact_name,
                              "contact_phone": receiver.contact_phone} if contacts else None),
        "volunteer": ({"full_name": volunteer.user.full_name, "phone": volunteer.user.phone,
                       "vehicle_type": volunteer.vehicle_type} if volunteer is not None else None),
        "decline_reason": task.decline_reason, "failure_reason": task.failure_reason,
        "accepted_at": task.accepted_at, "completed_at": task.completed_at, "version": task.version,
        "created_at": task.created_at,
    }


def _scoped(user: User):  # noqa: ANN202
    query = select(VolunteerAssignment)
    if user.role == UserRole.ADMIN:
        return query
    base = query.join(Collection, Collection.id == VolunteerAssignment.collection_id).join(
        Allocation, Allocation.id == Collection.allocation_id)
    if user.role == UserRole.VOLUNTEER:
        return query.join(VolunteerProfile, VolunteerProfile.id == VolunteerAssignment.volunteer_id).where(
            VolunteerProfile.user_id == user.id)
    if user.role == UserRole.PROVIDER:
        return base.join(FoodListing, FoodListing.id == Allocation.listing_id).join(
            ProviderProfile, ProviderProfile.id == FoodListing.provider_id).where(ProviderProfile.user_id == user.id)
    return base.join(ReceiverProfile, ReceiverProfile.id == Allocation.receiver_id).where(
        ReceiverProfile.user_id == user.id)


def list_tasks(db: Session, user: User, *, status: AssignmentStatus | None, page: int, page_size: int) -> dict[str, Any]:
    query = _scoped(user)
    if status is not None:
        query = query.where(VolunteerAssignment.status == status)
    total = db.scalar(select(func.count()).select_from(query.with_only_columns(VolunteerAssignment.id).subquery()))
    tasks = db.scalars(query.order_by(VolunteerAssignment.created_at.desc(), VolunteerAssignment.id)
                       .offset((page - 1) * page_size).limit(page_size)).unique().all()
    return {"items": [to_out(t, user) for t in tasks], "total": total, "page": page, "page_size": page_size}


def get_task(db: Session, user: User, task_id: uuid.UUID) -> dict[str, Any]:
    task = db.get(VolunteerAssignment, task_id)
    # Volunteers may also look at OPEN tasks (to decide whether to claim them).
    if task is None or not (_involved(task, user) or (user.role == UserRole.VOLUNTEER
                                                     and task.status == AssignmentStatus.OPEN)):
        raise AppError(404, "NOT_FOUND", "Task not found.")
    return to_out(task, user)


def history(db: Session, user: User, task_id: uuid.UUID) -> list[AssignmentStatusHistory]:
    get_task(db, user, task_id)
    return list(db.scalars(select(AssignmentStatusHistory).where(AssignmentStatusHistory.assignment_id == task_id)
                           .order_by(AssignmentStatusHistory.created_at, AssignmentStatusHistory.id)))

