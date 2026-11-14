"""Urgency engine: table-driven rule tests (spec 6.5: "rule validation against test
cases" and "correctness of date calculations"). No database needed."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.enums import UrgencyLevel as U
from app.engines.urgency import DEFAULT_RULE, DISCLAIMER, ListingDates, UrgencyRule, assess, format_duration

PKT = timezone(timedelta(hours=5))
NOW = datetime(2026, 11, 15, 14, 0, tzinfo=PKT)
COOKED = UrgencyRule(medium_hours=12, high_hours=6, critical_hours=2, max_hours_since_preparation=12)


def h(hours: float) -> datetime:
    return NOW + timedelta(hours=hours)


@pytest.mark.parametrize(
    ("hours_left", "level"),
    [
        (100, U.NORMAL), (12.01, U.NORMAL),
        (12, U.MEDIUM), (6.01, U.MEDIUM),  # exactly at a threshold counts as the more urgent level
        (6, U.HIGH), (2.01, U.HIGH),
        (2, U.CRITICAL), (0.01, U.CRITICAL),
    ],
)
def test_levels_and_exact_boundaries(hours_left: float, level: U) -> None:
    result = assess(ListingDates(collection_deadline=h(hours_left)), COOKED, NOW)
    assert result.level == level and not result.is_past_deadline
    assert result.deadline_source == "collection deadline"


@pytest.mark.parametrize("hours_left", [0, -0.01, -30])
def test_passed_deadline(hours_left: float) -> None:
    result = assess(ListingDates(collection_deadline=h(hours_left)), COOKED, NOW)
    assert result.is_past_deadline and result.level == U.CRITICAL
    assert result.explanation.startswith("Deadline passed")


@pytest.mark.parametrize(
    ("dates", "source", "hours"),
    [
        (ListingDates(collection_deadline=h(10), expiry_at=h(5)), "expiry date", 5),
        (ListingDates(collection_deadline=h(10), best_before_at=h(3)), "best-before date", 3),
        (ListingDates(collection_deadline=h(10), preparation_at=h(-11)), "preparation time limit (12 h)", 1),
        (ListingDates(collection_deadline=h(10), expiry_at=h(20), best_before_at=h(15)), "collection deadline", 10),
        (ListingDates(collection_deadline=h(4), expiry_at=h(4)), "collection deadline", 4),  # tie
    ],
)
def test_the_earliest_deadline_wins(dates: ListingDates, source: str, hours: float) -> None:
    result = assess(dates, COOKED, NOW)
    assert result.deadline_source == source
    assert result.hours_left == pytest.approx(hours)
    assert result.effective_deadline == h(hours).astimezone(UTC)


def test_preparation_limit_only_when_the_rule_has_one() -> None:
    no_limit = UrgencyRule(medium_hours=48, high_hours=24, critical_hours=6)
    result = assess(ListingDates(collection_deadline=h(10), preparation_at=h(-100)), no_limit, NOW)
    assert result.deadline_source == "collection deadline"


@pytest.mark.parametrize(
    ("dates", "missing", "text"),
    [
        (ListingDates(collection_deadline=h(30), requires_preparation_time=True), ["preparation_at"],
         "the preparation time is missing"),
        (ListingDates(collection_deadline=h(30), requires_expiry_date=True), ["expiry_at"],
         "the expiry date is missing"),
        (ListingDates(collection_deadline=h(30), requires_preparation_time=True, requires_expiry_date=True),
         ["preparation_at", "expiry_at"], "the preparation time and expiry date are missing"),
    ],
)
def test_missing_required_dates_give_incomplete_never_normal(dates: ListingDates, missing: list, text: str) -> None:
    result = assess(dates, COOKED, NOW)
    assert result.level == U.INCOMPLETE and result.missing_fields == missing
    assert text in result.explanation


def test_same_moment_in_any_time_zone_gives_the_same_result() -> None:
    deadline_pkt = datetime(2026, 11, 15, 18, 0, tzinfo=PKT)
    deadline_utc = datetime(2026, 11, 15, 13, 0, tzinfo=UTC)  # the same instant
    a = assess(ListingDates(collection_deadline=deadline_pkt), COOKED, NOW)
    b = assess(ListingDates(collection_deadline=deadline_utc), COOKED, NOW.astimezone(UTC))
    assert (a.level, a.hours_left, a.effective_deadline) == (b.level, b.hours_left, b.effective_deadline)
    assert a.hours_left == 4


def test_times_without_a_zone_are_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        assess(ListingDates(collection_deadline=datetime(2026, 11, 15, 18, 0)), COOKED, NOW)


def test_default_rule_when_category_has_none() -> None:
    result = assess(ListingDates(collection_deadline=h(30)), None, NOW)
    assert result.rule == DEFAULT_RULE and result.level == U.MEDIUM  # 30 h <= 48 h


@pytest.mark.parametrize(
    "thresholds",
    [(12, 6, 6), (12, 12, 2), (2, 6, 12), (12, 6, 0), (12, 6, -1)],
)
def test_rules_must_be_ordered(thresholds: tuple) -> None:
    medium, high, critical = thresholds
    with pytest.raises(ValueError):
        UrgencyRule(medium_hours=medium, high_hours=high, critical_hours=critical)


@pytest.mark.parametrize(
    ("hours", "text"),
    [(3 + 20 / 60, "3 h 20 m"), (0.75, "45 m"), (50, "2 d 2 h"), (48, "2 d"), (5, "5 h"), (0, "0 m"), (-2, "0 m")],
)
def test_format_duration(hours: float, text: str) -> None:
    assert format_duration(hours) == text


def test_explanations_name_the_level_and_the_deadline() -> None:
    result = assess(ListingDates(collection_deadline=h(3 + 20 / 60)), COOKED, NOW)
    assert result.explanation == "High urgency: collection deadline in 3 h 20 m."
    assert result.disclaimer == DISCLAIMER and "not a food-safety assessment" in DISCLAIMER


def test_wording_never_claims_food_safety() -> None:
    """Spec 6.2 / plan 1.3: never show 'safe', 'fresh', 'edible' or a quality judgement."""
    forbidden = ("safe to", " safe", "fresh", "edible", "quality", "spoil")
    cases = [ListingDates(collection_deadline=h(x)) for x in (-5, 0.5, 3, 8, 20, 100)] + [
        ListingDates(collection_deadline=h(30), requires_preparation_time=True),
        ListingDates(collection_deadline=h(10), preparation_at=h(-11), expiry_at=h(20), best_before_at=h(15)),
    ]
    for dates in cases:
        text = assess(dates, COOKED, NOW).explanation.lower()
        assert not any(word in text for word in forbidden), text
