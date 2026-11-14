"""Allocation engine (spec 6.5: "quantity constraint validation, allocation feasibility,
distribution and priority-rule compliance").

Two kinds of tests:
  * worked examples, including the spec's own example (30 packages, 3 receivers)
  * PROPERTY-BASED tests: Hypothesis invents thousands of random situations and checks
    that the rules hold in every single one (plan section 7.5)
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.enums import QuantityUnit
from app.engines.allocation import Candidate, Policy, allocate

T0 = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
PKG = QuantityUnit.PACKAGE
KG = QuantityUnit.KG


def spec_example() -> list[Candidate]:
    """Spec 5.5: 30 bread packages; NGO 15, community kitchen 10, eligible family 10 (in that order)."""
    return [Candidate("ngo", D(15), D("1.0"), T0, label="NGO"),
            Candidate("kitchen", D(10), D("1.0"), T0 + timedelta(minutes=1), label="Community kitchen"),
            Candidate("family", D(10), D("0.7"), T0 + timedelta(minutes=2), label="Individual / household")]


def amounts(result) -> dict:  # noqa: ANN001
    return {line.request_id: line.suggested for line in result.lines}


# ============================================================ worked examples


def test_spec_example_priority_weighted_gives_15_10_5() -> None:
    result = allocate(spec_example(), D(30), Policy.PRIORITY_WEIGHTED, PKG, whole_units=True)
    assert amounts(result) == {"ngo": 15, "kitchen": 10, "family": 5}
    assert result.unallocated == 0
    explanation = {line.request_id: line.explanation for line in result.lines}
    assert explanation["family"].startswith("5 packages of 10 packages requested: the remaining quantity")


def test_spec_example_proportional_fair_gives_13_9_8() -> None:
    # Share = 30 / 35 = 85.7 %: 12.86 / 8.57 / 8.57 -> 12 / 8 / 8, then the 2 left over go to the
    # largest remainders: NGO (0.86) and the earlier of the two tied 0.57s (the kitchen).
    result = allocate(spec_example(), D(30), Policy.PROPORTIONAL_FAIR, PKG, whole_units=True)
    assert amounts(result) == {"ngo": 13, "kitchen": 9, "family": 8}
    assert "about 86%" in result.lines[0].explanation


def test_spec_example_first_come() -> None:
    result = allocate(spec_example(), D(30), Policy.FIRST_COME, PKG, whole_units=True)
    assert amounts(result) == {"ngo": 15, "kitchen": 10, "family": 5}


def test_enough_for_everyone_means_full_requests_in_every_policy() -> None:
    for policy in Policy:
        result = allocate(spec_example(), D(40), policy, PKG, whole_units=True)
        assert amounts(result) == {"ngo": 15, "kitchen": 10, "family": 10}
        assert result.unallocated == 5


def test_capacity_and_eligibility() -> None:
    candidates = [Candidate("big", D(20), D(1), T0, capacity=D(8), label="NGO"),
                  Candidate("blocked", D(5), D(2), T0, eligible=False),
                  Candidate("small", D(5), D(1), T0 + timedelta(minutes=1))]
    result = allocate(candidates, D(30), Policy.PRIORITY_WEIGHTED, PKG, whole_units=True)
    assert amounts(result) == {"big": 8, "blocked": 0, "small": 5}
    text = {line.request_id: line.explanation for line in result.lines}
    assert "declared capacity of 8 packages" in text["big"]
    assert text["blocked"] == "Not allocated: the receiver is currently not eligible."


def test_kilograms_use_two_decimals() -> None:
    candidates = [Candidate("a", D("7.5"), D(1), T0), Candidate("b", D("7.5"), D(1), T0 + timedelta(seconds=1)),
                  Candidate("c", D("7.5"), D(1), T0 + timedelta(seconds=2))]
    result = allocate(candidates, D("10"), Policy.PROPORTIONAL_FAIR, KG, whole_units=False)
    assert sum(amounts(result).values()) == D("10.00")
    assert sorted(amounts(result).values()) == [D("3.33"), D("3.33"), D("3.34")]


def test_nothing_left_explanation() -> None:
    candidates = [Candidate("first", D(10), D(1), T0), Candidate("second", D(10), D(1), T0 + timedelta(minutes=1))]
    result = allocate(candidates, D(10), Policy.FIRST_COME, PKG, whole_units=True)
    assert result.lines[1].suggested == 0
    assert result.lines[1].explanation == "Not allocated: nothing left after earlier requests."


def test_no_candidates_and_nothing_available() -> None:
    assert allocate([], D(30), Policy.PRIORITY_WEIGHTED, PKG, True).lines == []
    result = allocate(spec_example(), D(0), Policy.PROPORTIONAL_FAIR, PKG, True)
    assert all(line.suggested == 0 for line in result.lines)


def test_unknown_policy_is_rejected() -> None:
    with pytest.raises(ValueError):
        allocate(spec_example(), D(30), "lottery", PKG, True)


# ======================================================== property-based tests

weights = st.sampled_from([D("0.5"), D("0.7"), D("0.8"), D("1.0"), D("2.0")])


@st.composite
def situations(draw):  # noqa: ANN001, ANN201
    whole = draw(st.booleans())
    if whole:
        quantity = st.integers(min_value=1, max_value=200).map(D)
        available = D(draw(st.integers(min_value=0, max_value=500)))
    else:
        quantity = st.integers(min_value=1, max_value=20000).map(lambda n: D(n) / 100)
        available = D(draw(st.integers(min_value=0, max_value=50000))) / 100
    n = draw(st.integers(min_value=0, max_value=12))
    candidates = []
    for i in range(n):
        capacity = draw(st.none() | quantity)
        candidates.append(Candidate(
            request_id=f"r{i}", requested=draw(quantity), weight=draw(weights),
            requested_at=T0 + timedelta(minutes=draw(st.integers(0, 300))),
            capacity=capacity, eligible=draw(st.booleans() | st.just(True)),
        ))
    policy = draw(st.sampled_from(list(Policy)))
    return candidates, available, policy, whole


@settings(max_examples=3000, deadline=None)
@given(situations())
def test_hard_rules_always_hold(case) -> None:  # noqa: ANN001
    candidates, available, policy, whole = case
    result = allocate(candidates, available, policy, PKG if whole else KG, whole)
    total = D(0)
    for c, line in zip(candidates, result.lines, strict=True):
        assert line.suggested >= 0
        assert line.suggested <= c.requested  # never more than asked
        if c.capacity is not None:
            assert line.suggested <= c.capacity  # never more than they can take
        if not c.eligible:
            assert line.suggested == 0  # ineligible receivers get nothing
        if whole:
            assert line.suggested == line.suggested.to_integral_value()  # whole packages only
        assert line.explanation
        total += line.suggested
    assert total <= available  # NEVER more than is available


@settings(max_examples=2000, deadline=None)
@given(situations())
def test_no_food_is_wasted(case) -> None:  # noqa: ANN001
    """Everything that can be given is given: total = min(available, sum of caps)."""
    candidates, available, policy, whole = case
    result = allocate(candidates, available, policy, PKG if whole else KG, whole)
    total_caps = sum((line.cap for line in result.lines), D(0))
    assert result.total_suggested == min(result.available, total_caps)


@settings(max_examples=2000, deadline=None)
@given(situations())
def test_priority_rule_compliance(case) -> None:  # noqa: ANN001
    """Priority policy: nobody with a LOWER weight gets food while someone with a HIGHER
    weight is still short of their cap."""
    candidates, available, _, whole = case
    result = allocate(candidates, available, Policy.PRIORITY_WEIGHTED, PKG if whole else KG, whole)
    lines = {line.request_id: line for line in result.lines}
    for high in candidates:
        if lines[high.request_id].suggested < lines[high.request_id].cap:  # short
            for low in candidates:
                if low.weight < high.weight:
                    assert lines[low.request_id].suggested == 0


@settings(max_examples=2000, deadline=None)
@given(situations())
def test_fair_shares_are_within_one_step(case) -> None:  # noqa: ANN001
    """Fair policy: each request's amount is within ONE unit (or 0.01 kg) of its exact fair share."""
    candidates, available, _, whole = case
    result = allocate(candidates, available, Policy.PROPORTIONAL_FAIR, PKG if whole else KG, whole)
    total_caps = sum((line.cap for line in result.lines), D(0))
    if total_caps <= result.available or total_caps == 0:
        return
    share = result.available / total_caps
    step = D(1) if whole else D("0.01")
    for line in result.lines:
        assert abs(line.suggested - line.cap * share) <= step
