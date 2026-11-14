"""Volunteer suggestion heuristic (plan section 7.6)."""

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal as D

from app.engines.volunteer_ranking import PKT, VolunteerFacts, suggest

# Friday 15 Nov 2026, 18:00 Pakistan time
PICKUP = datetime(2026, 11, 13, 18, 0, tzinfo=PKT)


def v(vid: str, **overrides) -> VolunteerFacts:  # noqa: ANN003
    base = {"volunteer_id": vid, "name": vid, "distance_km": 2.0, "service_radius_km": 10.0, "has_vehicle": False,
            "vehicle_type": None, "is_available_now": False, "weekly_slots": [], "active_tasks": 0,
            "completed_tasks": 0, "failed_tasks": 0}
    return VolunteerFacts(**{**base, **overrides})


def test_availability_slot_in_pakistan_time() -> None:
    friday_evening = [(4, time(17, 0), time(21, 0))]  # day 4 = Friday
    ranked, _ = suggest([v("slot", weekly_slots=friday_evening), v("none")], PICKUP, D(10))
    assert ranked[0].volunteer_id == "slot" and "available at the pickup time" in ranked[0].reasons
    # The same instant written in UTC (13:00Z) must give the same answer.
    ranked_utc, _ = suggest([v("slot", weekly_slots=friday_evening)], PICKUP.astimezone(timezone.utc), D(10))
    assert ranked_utc[0].components["availability"] == 1.0


def test_outside_service_area_is_excluded() -> None:
    ranked, excluded = suggest([v("far", distance_km=12.0)], PICKUP, D(10))
    assert ranked == [] and "outside their 10 km service area" in excluded[0][1]


def test_large_loads_favour_volunteers_with_vehicles() -> None:
    ranked, _ = suggest([v("walker"), v("van", has_vehicle=True, vehicle_type="van")], PICKUP, D(50))
    assert ranked[0].volunteer_id == "van" and "has a van" in ranked[0].reasons
    small, _ = suggest([v("walker")], PICKUP, D(10))
    assert small[0].components["vehicle"] == 1.0


def test_workload_and_reliability() -> None:
    ranked, _ = suggest([v("busy", active_tasks=3, completed_tasks=9, failed_tasks=1),
                         v("free", completed_tasks=10)], PICKUP, D(10))
    assert ranked[0].volunteer_id == "free"
    busy = ranked[1]
    assert busy.components["workload"] == 0.25 and busy.components["reliability"] == 0.9
    assert "3 other active task(s)" in busy.reasons and "completed 9 of 10 deliveries" in busy.reasons


def test_newcomers_get_benefit_of_the_doubt_and_scores_are_bounded() -> None:
    ranked, _ = suggest([v("new"), v("now", is_available_now=True, distance_km=0)], None, D(5))
    assert ranked[0].volunteer_id == "now" and "available now" in ranked[0].reasons
    assert all(0 <= s.score <= 1 for s in ranked)
    assert ranked[1].components["reliability"] == 0.8
    assert (PICKUP - timedelta(days=0)).tzinfo is not None
