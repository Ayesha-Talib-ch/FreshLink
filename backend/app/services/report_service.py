"""Admin reports and analytics (spec 14.2 / 14.3 and 4.4.10; plan 3.5 and 4.12).

Rules that every report follows:
- Quantities are grouped by unit (10 kg and 10 packages are never added together).
- Listed, allocated, collected (handed over) and distributed (received) are different numbers
  and are never mixed up (spec 14.3: "must not be treated as interchangeable").
- The date range applies to the date of each event: a listing counts in the period it was
  created, a distribution in the period the receiver confirmed it, and so on.
- Dates are calendar days in Pakistan time (UTC+5), the platform's operating time zone."""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.enums import (
    AllocationStatus,
    AssignmentStatus,
    CollectionStatus,
    ListingStatus,
    RequestStatus,
)
from app.engines.fairness import fill_ratio, gini
from app.models.allocation import Allocation
from app.models.assignment import VolunteerAssignment
from app.models.category import FoodCategory, ReceiverCategory
from app.models.collection import Collection
from app.models.listing import FoodListing
from app.models.profiles import ProviderProfile, ReceiverProfile, VolunteerProfile
from app.models.request import FoodRequest
from app.models.user import User

PKT = timezone(timedelta(hours=5), "PKT")
APPROVED = (RequestStatus.APPROVED, RequestStatus.PARTIALLY_APPROVED, RequestStatus.COMPLETED)
DECIDED = (*APPROVED, RequestStatus.REJECTED)


@dataclass(frozen=True)
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    food_category_id: uuid.UUID | None = None
    provider_id: uuid.UUID | None = None
    receiver_id: uuid.UUID | None = None
    listing_status: ListingStatus | None = None
    request_status: RequestStatus | None = None

    def check(self) -> "Filters":
        if self.date_from and self.date_to and self.date_to < self.date_from:
            raise AppError(422, "INVALID_DATE_RANGE", "date_to must be on or after date_from.")
        return self

    def as_dict(self) -> dict[str, Any]:
        return {k: (str(v) if v is not None else None) for k, v in self.__dict__.items()}


def between(column: ColumnElement, f: Filters) -> list[ColumnElement]:
    """Calendar days in Pakistan time; date_to is inclusive."""
    conditions = []
    if f.date_from:
        conditions.append(column >= datetime.combine(f.date_from, time.min, PKT))
    if f.date_to:
        conditions.append(column < datetime.combine(f.date_to + timedelta(days=1), time.min, PKT))
    return conditions


def _listing_filters(f: Filters) -> list[ColumnElement]:
    conditions = []
    if f.food_category_id:
        conditions.append(FoodListing.food_category_id == f.food_category_id)
    if f.provider_id:
        conditions.append(FoodListing.provider_id == f.provider_id)
    if f.listing_status:
        conditions.append(FoodListing.status == f.listing_status)
    else:
        conditions.append(FoodListing.status != ListingStatus.REMOVED)  # removed = never real food
    return conditions


# ------------------------------------------------------------- the four measures
# Each returns a SELECT with FoodListing joined, so callers can group by anything.


def _listed(f: Filters) -> Select:
    return select().select_from(FoodListing).where(*_listing_filters(f), *between(FoodListing.created_at, f))


def _requests(f: Filters) -> Select:
    q = select().select_from(FoodRequest).join(FoodListing, FoodListing.id == FoodRequest.listing_id).where(
        *_listing_filters(f), *between(FoodRequest.created_at, f))
    if f.request_status:
        q = q.where(FoodRequest.status == f.request_status)
    if f.receiver_id:
        q = q.where(FoodRequest.receiver_id == f.receiver_id)
    return q


def _allocated(f: Filters) -> Select:
    q = select().select_from(Allocation).join(FoodListing, FoodListing.id == Allocation.listing_id).where(
        *_listing_filters(f), *between(Allocation.created_at, f), Allocation.status != AllocationStatus.CANCELLED)
    return q.where(Allocation.receiver_id == f.receiver_id) if f.receiver_id else q


def _collections(f: Filters, event: Literal["handed_over", "received"]) -> Select:
    when = Collection.handed_over_at if event == "handed_over" else Collection.received_at
    q = (select().select_from(Collection).join(Allocation, Allocation.id == Collection.allocation_id)
         .join(FoodListing, FoodListing.id == Allocation.listing_id)
         .where(*_listing_filters(f), when.is_not(None), *between(when, f)))
    if event == "received":
        q = q.where(Collection.status == CollectionStatus.RECEIVED)
    return q.where(Allocation.receiver_id == f.receiver_id) if f.receiver_id else q


def _units(rows: list[tuple[Any, ...]]) -> dict[Any, list[dict[str, Any]]]:
    """(key, unit, quantity) rows -> {key: [{"unit": ..., "quantity": ...}, ...]} sorted by unit."""
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for key, unit, quantity in sorted(rows, key=lambda r: (str(r[0]), r[1].value)):
        if quantity:
            out[key].append({"unit": unit, "quantity": Decimal(quantity)})
    return out


def _sum_by(db: Session, base: Select, key: ColumnElement, quantity: ColumnElement) -> dict[Any, list[dict]]:
    rows = db.execute(base.add_columns(key, FoodListing.unit, func.sum(quantity)).group_by(key, FoodListing.unit)).all()
    return _units([tuple(r) for r in rows])


def _count_by(db: Session, base: Select, key: ColumnElement) -> dict[Any, int]:
    return dict(db.execute(base.add_columns(key, func.count()).group_by(key)).all())


def _count(db: Session, base: Select) -> int:
    return db.scalar(base.add_columns(func.count())) or 0


def _total(db: Session, base: Select, quantity: ColumnElement) -> list[dict[str, Any]]:
    """Grand total per unit (no other grouping)."""
    rows = db.execute(base.add_columns(FoodListing.unit, func.sum(quantity)).group_by(FoodListing.unit)).all()
    return _units([("all", unit, q) for unit, q in rows]).get("all", [])


# ======================================================================= summary


def summary(db: Session, f: Filters) -> dict[str, Any]:
    requests = _requests(f)
    by_status = _count_by(db, requests, FoodRequest.status)
    received = _collections(f, "received")
    return {
        "filters": f.as_dict(),
        "listings": _count(db, _listed(f)),
        "requests": sum(by_status.values()),
        "requests_by_status": {s.value: by_status.get(s, 0) for s in RequestStatus},
        "allocations": _count(db, _allocated(f)),
        "successful_redistributions": _count(db, received),
        "failed_collections": _count(db, select().select_from(Collection).join(Allocation).join(
            FoodListing, FoodListing.id == Allocation.listing_id).where(
            *_listing_filters(f), Collection.status == CollectionStatus.FAILED, *between(Collection.updated_at, f),
            *([Allocation.receiver_id == f.receiver_id] if f.receiver_id else []))),
        "quantity_listed": _total(db, _listed(f), FoodListing.total_quantity),
        "quantity_allocated": _total(db, _allocated(f), Allocation.approved_quantity),
        "quantity_collected": _total(db, _collections(f, "handed_over"), Collection.handed_over_quantity),
        "quantity_distributed": _total(db, received, Collection.received_quantity),
        # food on listings that ended (expired / closed) without being allocated: not redistributed
        "quantity_not_redistributed": _total(db, _listed(f).where(FoodListing.status.in_((ListingStatus.EXPIRED, ListingStatus.CLOSED))), FoodListing.total_quantity - FoodListing.allocated_quantity),
    }


# =================================================================== timeseries


def _periods(start: date, end: date, interval: str) -> list[date]:
    out, current = [], (start - timedelta(days=start.weekday()) if interval == "week" else start.replace(day=1))
    while current <= end:
        out.append(current)
        current = current + timedelta(days=7) if interval == "week" else \
            (current.replace(year=current.year + 1, month=1) if current.month == 12
             else current.replace(month=current.month + 1))
    return out


def timeseries(db: Session, f: Filters, interval: Literal["week", "month"]) -> dict[str, Any]:
    """Weekly or monthly trends (spec 14.2: listing, request and distribution trends)."""
    def bucket(column: ColumnElement) -> ColumnElement:
        return func.date_trunc(interval, func.timezone("Asia/Karachi", column)).label("period")

    listed_at, requested_at = bucket(FoodListing.created_at), bucket(FoodRequest.created_at)
    received_at = bucket(Collection.received_at)
    listings = {k.date(): v for k, v in _count_by(db, _listed(f), listed_at).items()}
    requests = {k.date(): v for k, v in _count_by(db, _requests(f), requested_at).items()}
    received = _collections(f, "received")
    completed = {k.date(): v for k, v in _count_by(db, received, received_at).items()}
    listed_qty = {k.date(): v for k, v in _sum_by(db, _listed(f), listed_at, FoodListing.total_quantity).items()}
    dist_qty = {k.date(): v for k, v in _sum_by(db, received, received_at, Collection.received_quantity).items()}

    seen = set(listings) | set(requests) | set(completed)
    today = datetime.now(PKT).date()
    start = f.date_from or (min(seen) if seen else today)
    end = f.date_to or (max(seen | {today}))
    rows = [{"period_start": p, "listings": listings.get(p, 0), "requests": requests.get(p, 0),
             "completed_distributions": completed.get(p, 0), "quantity_listed": listed_qty.get(p, []),
             "quantity_distributed": dist_qty.get(p, [])} for p in _periods(start, end, interval)]
    return {"filters": f.as_dict(), "interval": interval, "rows": rows}


# ============================================================ grouped reports


def categories(db: Session, f: Filters) -> dict[str, Any]:
    """Most common food categories: listings, requests, listed and distributed per category."""
    key = FoodListing.food_category_id
    listings = _count_by(db, _listed(f), key)
    requests = _count_by(db, _requests(f), key)
    listed = _sum_by(db, _listed(f), key, FoodListing.total_quantity)
    distributed = _sum_by(db, _collections(f, "received"), key, Collection.received_quantity)
    names = dict(db.execute(select(FoodCategory.id, FoodCategory.name)).all())
    rows = [{"food_category_id": cid, "name": names.get(cid, "?"), "listings": listings.get(cid, 0),
             "requests": requests.get(cid, 0), "quantity_listed": listed.get(cid, []),
             "quantity_distributed": distributed.get(cid, [])}
            for cid in set(listings) | set(requests) | set(distributed)]
    rows.sort(key=lambda r: (-r["listings"], -r["requests"], r["name"]))
    return {"filters": f.as_dict(), "rows": rows}


def providers(db: Session, f: Filters) -> dict[str, Any]:
    """Provider activity."""
    key = FoodListing.provider_id
    listings = _count_by(db, _listed(f), key)
    received = _collections(f, "received")
    completed = _count_by(db, received, key)
    listed = _sum_by(db, _listed(f), key, FoodListing.total_quantity)
    distributed = _sum_by(db, received, key, Collection.received_quantity)
    names = dict(db.execute(select(ProviderProfile.id, ProviderProfile.business_name)).all())
    rows = [{"provider_id": pid, "name": names.get(pid, "?"), "listings": listings.get(pid, 0),
             "completed_distributions": completed.get(pid, 0), "quantity_listed": listed.get(pid, []),
             "quantity_distributed": distributed.get(pid, [])} for pid in set(listings) | set(completed)]
    rows.sort(key=lambda r: (-r["listings"], -r["completed_distributions"], r["name"]))
    return {"filters": f.as_dict(), "rows": rows}


def receivers(db: Session, f: Filters) -> dict[str, Any]:
    """Receiver activity."""
    key = FoodRequest.receiver_id
    requests = _requests(f)
    total = _count_by(db, requests, key)
    approved = _count_by(db, requests.where(FoodRequest.status.in_(APPROVED)), key)
    rejected = _count_by(db, requests.where(FoodRequest.status == RequestStatus.REJECTED), key)
    received = _collections(f, "received")
    completed = _count_by(db, received, Allocation.receiver_id)
    quantity = _sum_by(db, received, Allocation.receiver_id, Collection.received_quantity)
    profiles = {p.id: p for p in db.scalars(select(ReceiverProfile).where(
        ReceiverProfile.id.in_(set(total) | set(completed))))}
    rows = [{"receiver_id": rid, "name": profiles[rid].receiver_name, "category": profiles[rid].category.name,
             "requests": total.get(rid, 0), "approved_requests": approved.get(rid, 0),
             "rejected_requests": rejected.get(rid, 0), "completed_collections": completed.get(rid, 0),
             "quantity_received": quantity.get(rid, [])} for rid in profiles]
    rows.sort(key=lambda r: (-r["completed_collections"], -r["requests"], r["name"]))
    return {"filters": f.as_dict(), "rows": rows}


def volunteers(db: Session, f: Filters) -> dict[str, Any]:
    """Volunteer activity: delivery tasks created in the period (category / provider filters apply)."""
    status = VolunteerAssignment.status
    q = (select(VolunteerAssignment.volunteer_id, func.count(),
                func.count().filter(status == AssignmentStatus.COMPLETED),
                func.count().filter(status == AssignmentStatus.FAILED),
                func.count().filter(status == AssignmentStatus.DECLINED))
         .join(Collection, Collection.id == VolunteerAssignment.collection_id).join(Allocation)
         .join(FoodListing, FoodListing.id == Allocation.listing_id)
         .where(VolunteerAssignment.volunteer_id.is_not(None), *_listing_filters(f),
                *between(VolunteerAssignment.created_at, f))
         .group_by(VolunteerAssignment.volunteer_id))
    counts = db.execute(q).all()
    people = dict(db.execute(select(VolunteerProfile.id, User.full_name).join(User, User.id == VolunteerProfile.user_id)
                             .where(VolunteerProfile.id.in_([c[0] for c in counts]))).all())
    rows = [{"volunteer_id": vid, "name": people.get(vid, "?"), "tasks": tasks, "completed_deliveries": done,
             "failed_deliveries": failed, "declined_tasks": declined,
             "completion_rate": round(done / (done + failed), 2) if done + failed else None}
            for vid, tasks, done, failed, declined in counts]
    rows.sort(key=lambda r: (-r["completed_deliveries"], -r["tasks"], r["name"]))
    return {"filters": f.as_dict(), "rows": rows}


# ======================================================================= fairness


def fairness(db: Session, f: Filters) -> dict[str, Any]:
    """Plan 4.12 / 7.5: is food shared fairly between receiver categories and receivers?

    - fill ratio: share of each decided request that was approved (0 = rejected, 1 = in full), averaged;
      unit-free, so categories can be compared even when they ask for different units
    - Gini of received quantity across receivers who requested in the period, per unit (0 = equal)
    - override rate: allocations where a person changed the suggested quantity"""
    decided = list(db.execute(_requests(f).add_columns(
        FoodRequest.receiver_id, FoodRequest.status, FoodRequest.requested_quantity, FoodListing.unit,
        Allocation.approved_quantity).outerjoin(Allocation, Allocation.request_id == FoodRequest.id)
        .where(FoodRequest.status.in_(DECIDED))).all())
    requesters = {r[0] for r in db.execute(_requests(f).add_columns(FoodRequest.receiver_id)).all()}
    profiles = {p.id: p for p in db.scalars(select(ReceiverProfile).where(ReceiverProfile.id.in_(requesters)))}
    category_names = dict(db.execute(select(ReceiverCategory.id, ReceiverCategory.name)).all())

    received = _sum_by(db, _collections(f, "received"), Allocation.receiver_id, Collection.received_quantity)
    per_category: dict[uuid.UUID, dict[str, Any]] = {}
    for rid, p in profiles.items():
        c = per_category.setdefault(p.receiver_category_id, {
            "receiver_category_id": p.receiver_category_id, "name": category_names.get(p.receiver_category_id, "?"),
            "receivers": 0, "decided_requests": 0, "rejected_requests": 0, "_ratios": [],
            "_received": defaultdict(Decimal)})
        c["receivers"] += 1
        for item in received.get(rid, []):
            c["_received"][item["unit"]] += item["quantity"]
    for rid, status, requested, _unit, approved in decided:
        c = per_category[profiles[rid].receiver_category_id]
        c["decided_requests"] += 1
        c["rejected_requests"] += status == RequestStatus.REJECTED
        c["_ratios"].append(fill_ratio(requested, approved or 0))

    totals: dict[Any, Decimal] = defaultdict(Decimal)
    for c in per_category.values():
        for unit, q in c["_received"].items():
            totals[unit] += q
    rows = []
    for c in sorted(per_category.values(), key=lambda c: c["name"]):
        ratios, got = c.pop("_ratios"), c.pop("_received")
        c["average_fill_ratio"] = round(sum(ratios) / len(ratios), 3) if ratios else None
        c["quantity_received"] = [{"unit": u, "quantity": q, "share": round(float(q / totals[u]), 3)}
                                  for u, q in sorted(got.items(), key=lambda x: x[0].value) if q]
        rows.append(c)

    # Gini per unit, across every receiver who asked for food of that unit in the period
    asked: dict[Any, set] = defaultdict(set)
    for rid, unit in db.execute(_requests(f).add_columns(FoodRequest.receiver_id, FoodListing.unit)).all():
        asked[unit].add(rid)
    by_receiver_unit = {(rid, item["unit"]): item["quantity"] for rid, items in received.items() for item in items}
    inequality = [{"unit": unit, "receivers": len(rids),
                   "gini": gini([by_receiver_unit.get((rid, unit), 0) for rid in rids])}
                  for unit, rids in sorted(asked.items(), key=lambda x: x[0].value)]

    allocations = _allocated(f)
    total_alloc = _count(db, allocations)
    overridden = _count(db, allocations.where(Allocation.override_reason.is_not(None)))
    return {"filters": f.as_dict(), "by_receiver_category": rows, "inequality_by_unit": inequality,
            "allocations": total_alloc, "overridden_allocations": overridden,
            "override_rate": round(overridden / total_alloc, 3) if total_alloc else None,
            "note": "Fairness figures describe how food was shared; they are not a judgement of any receiver."}
