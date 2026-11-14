"""Suggest volunteers for a delivery task (Technical Plan section 7.6). A supporting
HEURISTIC, not ML: rank approved volunteers and explain why.

Hard filter: the pickup point must be within the volunteer's service radius.
Score (0-1), weighted:
  distance        0.35  exp(-km / 5) to the pickup point
  availability    0.25  1 if a weekly slot covers the pickup time (or "available now" with
                        no time set), 0.5 if "available now" only, else 0
  vehicle         0.15  1 with a vehicle; without one, 1 for small loads (<= 20) else 0.3
  workload        0.15  1 / (1 + active tasks): spreads work out
  reliability     0.10  completed / (completed + failed); 0.8 for newcomers (benefit of the doubt)
Times are compared in Pakistan time (UTC+5, no daylight saving).
"""

import math
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

PKT = timezone(timedelta(hours=5), "PKT")
SMALL_LOAD = Decimal(20)
WEIGHTS = {"distance": 0.35, "availability": 0.25, "vehicle": 0.15, "workload": 0.15, "reliability": 0.10}


@dataclass(frozen=True)
class VolunteerFacts:
    volunteer_id: Any
    name: str
    distance_km: float  # to the pickup point
    service_radius_km: float
    has_vehicle: bool
    vehicle_type: str | None
    is_available_now: bool
    weekly_slots: list[tuple[int, time, time]]  # (day 0=Mon, start, end) in Pakistan time
    active_tasks: int
    completed_tasks: int
    failed_tasks: int


@dataclass(frozen=True)
class Suggestion:
    volunteer_id: Any
    score: float
    components: dict[str, float]
    reasons: list[str]


def _covered(slots: list[tuple[int, time, time]], when: datetime) -> bool:
    local = when.astimezone(PKT)
    return any(day == local.weekday() and start <= local.time() <= end for day, start, end in slots)


def suggest(volunteers: list[VolunteerFacts], pickup_at: datetime | None,
            quantity: Decimal) -> tuple[list[Suggestion], list[tuple[Any, str]]]:
    suggestions, excluded = [], []
    for v in volunteers:
        if v.distance_km > v.service_radius_km:
            excluded.append((v.volunteer_id, f"{v.distance_km:.1f} km from the pickup, outside their "
                                             f"{v.service_radius_km:g} km service area."))
            continue
        slot = pickup_at is not None and _covered(v.weekly_slots, pickup_at)
        if slot or (pickup_at is None and v.is_available_now):
            availability = 1.0
        elif v.is_available_now:
            availability = 0.5
        else:
            availability = 0.0
        finished = v.completed_tasks + v.failed_tasks
        c = {
            "distance": math.exp(-v.distance_km / 5),
            "availability": availability,
            "vehicle": 1.0 if v.has_vehicle or quantity <= SMALL_LOAD else 0.3,
            "workload": 1 / (1 + v.active_tasks),
            "reliability": v.completed_tasks / finished if finished else 0.8,
        }
        score = round(sum(WEIGHTS[k] * value for k, value in c.items()), 4)
        reasons = [f"{v.distance_km:.1f} km from the pickup"]
        if slot:
            reasons.append("available at the pickup time")
        elif v.is_available_now:
            reasons.append("available now")
        if v.has_vehicle:
            reasons.append(f"has a {v.vehicle_type or 'vehicle'}")
        reasons.append("no other tasks" if v.active_tasks == 0 else f"{v.active_tasks} other active task(s)")
        if finished:
            reasons.append(f"completed {v.completed_tasks} of {finished} deliveries")
        suggestions.append(Suggestion(v.volunteer_id, score, {k: round(x, 4) for k, x in c.items()}, reasons))
    suggestions.sort(key=lambda s: (-s.score, str(s.volunteer_id)))
    return suggestions, excluded
