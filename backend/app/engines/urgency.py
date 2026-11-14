"""Urgency assessment: rule-based decision support (spec 6.2, Technical Plan section 7.3).

This is NOT machine learning, and it does NOT decide whether food is safe to eat.
It only answers: "based on the dates the PROVIDER supplied, how soon does this
listing need attention?"

Algorithm
---------
1. Collect every deadline that applies:
     collection deadline, expiry date, best-before date,
     preparation time + the category's "max hours since preparation".
2. The EFFECTIVE deadline is the earliest of them.
3. hours_left = effective deadline - now
4. Compare hours_left with the category's thresholds:
     <= critical_hours -> critical, <= high_hours -> high,
     <= medium_hours   -> medium,   otherwise     -> normal
   If a date the category requires is missing -> "incomplete" (never "normal").
5. Always say WHICH deadline and WHICH rule produced the result.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from app.domain.enums import UrgencyLevel

DISCLAIMER = (
    "Urgency is based only on the dates supplied by the provider and on platform "
    "prioritisation rules. It is not a food-safety assessment."
)


@dataclass(frozen=True)
class UrgencyRule:
    """Thresholds in hours left before the effective deadline. Admin-configurable."""

    medium_hours: float
    high_hours: float
    critical_hours: float
    max_hours_since_preparation: float | None = None

    def __post_init__(self) -> None:
        if not (0 < self.critical_hours < self.high_hours < self.medium_hours):
            raise ValueError("Thresholds must satisfy 0 < critical < high < medium.")
        if self.max_hours_since_preparation is not None and self.max_hours_since_preparation <= 0:
            raise ValueError("max_hours_since_preparation must be positive.")


# Used only if a category has no rule configured. Placeholder values, not safety limits.
DEFAULT_RULE = UrgencyRule(medium_hours=48, high_hours=24, critical_hours=6)


@dataclass(frozen=True)
class ListingDates:
    collection_deadline: datetime
    expiry_at: datetime | None = None
    best_before_at: datetime | None = None
    preparation_at: datetime | None = None
    requires_preparation_time: bool = False
    requires_expiry_date: bool = False


@dataclass(frozen=True)
class UrgencyResult:
    level: UrgencyLevel
    effective_deadline: datetime
    deadline_source: str  # which deadline drove the level, e.g. "collection deadline"
    hours_left: float  # negative once the deadline has passed
    is_past_deadline: bool
    explanation: str
    missing_fields: list[str] = field(default_factory=list)
    rule: UrgencyRule = DEFAULT_RULE
    disclaimer: str = DISCLAIMER


def format_duration(hours: float) -> str:
    """3.33 -> '3 h 20 m'; 50 -> '2 d 2 h'; 0.75 -> '45 m'."""
    minutes = max(0, int(round(hours * 60)))
    days, rest = divmod(minutes, 24 * 60)
    hrs, mins = divmod(rest, 60)
    if days:
        return f"{days} d {hrs} h" if hrs else f"{days} d"
    if hrs:
        return f"{hrs} h {mins} m" if mins else f"{hrs} h"
    return f"{mins} m"


_FIELD_NAMES = {"preparation_at": "preparation time", "expiry_at": "expiry date"}

_LABELS = {
    UrgencyLevel.NORMAL: "Normal",
    UrgencyLevel.MEDIUM: "Medium urgency",
    UrgencyLevel.HIGH: "High urgency",
    UrgencyLevel.CRITICAL: "Critical urgency",
}


def assess(dates: ListingDates, rule: UrgencyRule | None, now: datetime) -> UrgencyResult:
    if now.tzinfo is None or dates.collection_deadline.tzinfo is None:
        raise ValueError("All times must be timezone-aware.")  # plan issue M11
    rule = rule or DEFAULT_RULE

    candidates: list[tuple[datetime, str]] = [(dates.collection_deadline, "collection deadline")]
    if dates.expiry_at is not None:
        candidates.append((dates.expiry_at, "expiry date"))
    if dates.best_before_at is not None:
        candidates.append((dates.best_before_at, "best-before date"))
    if dates.preparation_at is not None and rule.max_hours_since_preparation is not None:
        candidates.append((dates.preparation_at + timedelta(hours=rule.max_hours_since_preparation),
                           f"preparation time limit ({format_duration(rule.max_hours_since_preparation)})"))

    # min() keeps the FIRST of equal deadlines, so the collection deadline wins ties.
    effective_deadline, source = min(candidates, key=lambda c: c[0])
    hours_left = (effective_deadline - now).total_seconds() / 3600
    past = hours_left <= 0

    missing = []
    if dates.requires_preparation_time and dates.preparation_at is None:
        missing.append("preparation_at")
    if dates.requires_expiry_date and dates.expiry_at is None:
        missing.append("expiry_at")

    if missing:
        level = UrgencyLevel.INCOMPLETE
        readable = " and ".join(_FIELD_NAMES[m] for m in missing)
        explanation = f"Incomplete date info: the {readable} {'is' if len(missing) == 1 else 'are'} missing."
    elif past:
        level = UrgencyLevel.CRITICAL
        explanation = f"Deadline passed: the {source} was {format_duration(-hours_left)} ago."
    else:
        if hours_left <= rule.critical_hours:
            level = UrgencyLevel.CRITICAL
        elif hours_left <= rule.high_hours:
            level = UrgencyLevel.HIGH
        elif hours_left <= rule.medium_hours:
            level = UrgencyLevel.MEDIUM
        else:
            level = UrgencyLevel.NORMAL
        explanation = f"{_LABELS[level]}: {source} in {format_duration(hours_left)}."

    return UrgencyResult(
        level=level, effective_deadline=effective_deadline.astimezone(UTC), deadline_source=source,
        hours_left=round(hours_left, 2), is_past_deadline=past, explanation=explanation,
        missing_fields=missing, rule=rule,
    )
