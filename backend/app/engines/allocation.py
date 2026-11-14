"""Allocation: divide a limited quantity between several requests (spec 6.4, 5.5;
Technical Plan section 7.5). This is OPTIMISATION / operations research, not ML.

Hard rules (never broken, proven by property-based tests with thousands of cases):
  * total suggested <= available quantity
  * 0 <= suggested <= cap, where cap = min(requested, receiver's capacity)
  * ineligible requests get 0
  * countable units (packages, trays ...) get whole numbers

Policies:
  priority_weighted (default)
      Serve requests in order of the receiver category's priority weight (higher first),
      earlier requests first on ties, each up to its cap. Maximises the total
      "priority-weighted food delivered". With only "total <= available" and per-request
      caps, this greedy method is PROVABLY OPTIMAL (it is the fractional / unit-step
      knapsack with equal item sizes), so no solver is needed (plan issue M10).
  proportional_fair
      Every request receives the same SHARE of its cap: t = available / total caps.
      Whole units are rounded down, then the few units left over go one by one to the
      largest remainders (the "largest remainder" / Hamilton method used for seats in
      parliaments), so nothing is wasted and shares differ by at most one unit.
  first_come
      Serve in request order, each up to its cap.

Every line comes with a plain-language explanation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum
from typing import Any

from app.domain.enums import QuantityUnit
from app.domain.formatting import format_quantity


class Policy(StrEnum):
    PRIORITY_WEIGHTED = "priority_weighted"
    PROPORTIONAL_FAIR = "proportional_fair"
    FIRST_COME = "first_come"


@dataclass(frozen=True)
class Candidate:
    request_id: Any
    requested: Decimal
    weight: Decimal  # receiver category priority weight (admin-configured)
    requested_at: datetime
    capacity: Decimal | None = None  # receiver's max per pickup in this unit, if declared
    eligible: bool = True
    label: str = ""  # e.g. "Community kitchen", used in explanations


@dataclass(frozen=True)
class Line:
    request_id: Any
    requested: Decimal
    cap: Decimal
    suggested: Decimal
    explanation: str


@dataclass(frozen=True)
class Result:
    policy: Policy
    available: Decimal
    lines: list[Line] = field(default_factory=list)

    @property
    def total_suggested(self) -> Decimal:
        return sum((line.suggested for line in self.lines), Decimal(0))

    @property
    def unallocated(self) -> Decimal:
        return self.available - self.total_suggested


def _step(whole: bool) -> Decimal:
    return Decimal(1) if whole else Decimal("0.01")


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_FLOOR) * step


def _cap(c: Candidate, step: Decimal) -> Decimal:
    if not c.eligible:
        return Decimal(0)
    cap = c.requested if c.capacity is None else min(c.requested, c.capacity)
    return max(Decimal(0), _floor(cap, step))


def allocate(candidates: list[Candidate], available: Decimal, policy: Policy | str,
             unit: QuantityUnit, whole_units: bool) -> Result:
    policy = Policy(policy)
    step = _step(whole_units)
    available = max(Decimal(0), _floor(Decimal(available), step))
    caps = {c.request_id: _cap(c, step) for c in candidates}

    if policy == Policy.PROPORTIONAL_FAIR:
        amounts = _proportional(candidates, caps, available, step)
    else:
        if policy == Policy.PRIORITY_WEIGHTED:
            order = sorted(candidates, key=lambda c: (-c.weight, c.requested_at, str(c.request_id)))
        else:
            order = sorted(candidates, key=lambda c: (c.requested_at, str(c.request_id)))
        amounts, left = {}, available
        for c in order:
            amounts[c.request_id] = min(caps[c.request_id], left)
            left -= amounts[c.request_id]

    total_caps = sum(caps.values(), Decimal(0))
    lines = [Line(c.request_id, c.requested, caps[c.request_id], amounts[c.request_id],
                  _explain(c, caps[c.request_id], amounts[c.request_id], policy, unit, available, total_caps))
             for c in candidates]
    return Result(policy=policy, available=available, lines=lines)


def _proportional(candidates: list[Candidate], caps: dict, available: Decimal, step: Decimal) -> dict:
    total = sum(caps.values(), Decimal(0))
    if total <= available:
        return dict(caps)  # enough for everyone
    share = available / total
    exact = {c.request_id: caps[c.request_id] * share for c in candidates}
    amounts = {rid: _floor(value, step) for rid, value in exact.items()}
    left = available - sum(amounts.values(), Decimal(0))
    # Largest remainder first; earlier request wins a tie.
    by_remainder = sorted(candidates, key=lambda c: (-(exact[c.request_id] - amounts[c.request_id]),
                                                      c.requested_at, str(c.request_id)))
    for c in by_remainder:
        if left < step:
            break
        if amounts[c.request_id] + step <= caps[c.request_id]:
            amounts[c.request_id] += step
            left -= step
    return amounts


def _explain(c: Candidate, cap: Decimal, got: Decimal, policy: Policy, unit: QuantityUnit,
             available: Decimal, total_caps: Decimal) -> str:
    q = lambda amount: format_quantity(amount, unit)  # noqa: E731
    who = f" (priority: {c.label})" if c.label and policy == Policy.PRIORITY_WEIGHTED else ""
    if not c.eligible:
        return "Not allocated: the receiver is currently not eligible."
    limited_by_capacity = c.capacity is not None and c.capacity < c.requested
    if got == 0:
        reason = {Policy.PRIORITY_WEIGHTED: "nothing left after higher-priority or earlier requests",
                  Policy.FIRST_COME: "nothing left after earlier requests",
                  Policy.PROPORTIONAL_FAIR: "the equal share rounded down to zero"}[policy]
        return f"Not allocated: {reason}{who}."
    if got == c.requested:
        return f"{q(got)} of {q(c.requested)} requested: full request met{who}."
    if got == cap and limited_by_capacity:
        return (f"{q(got)} of {q(c.requested)} requested: limited to the receiver's declared "
                f"capacity of {q(c.capacity)} per pickup{who}.")
    if policy == Policy.PROPORTIONAL_FAIR:
        percent = round(available / total_caps * 100) if total_caps else 0
        return (f"{q(got)} of {q(c.requested)} requested: equal share, every request gets about "
                f"{percent}% of what it asked for.")
    after = "higher-priority or earlier requests" if policy == Policy.PRIORITY_WEIGHTED else "earlier requests"
    return f"{q(got)} of {q(c.requested)} requested: the remaining quantity after {after}{who}."
