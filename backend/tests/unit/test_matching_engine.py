"""Matching engine v1 (spec 6.3): hard filters, score parts, ranking and reasons.

Includes the spec's own example: "NGO A: suitable category, nearby, capacity available.
Community Kitchen B: suitable category, slightly farther away. Receiver C: not eligible."
"""

import math
from decimal import Decimal as D

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.enums import QuantityUnit, UrgencyLevel
from app.engines.matching import PLATFORM_MAX_RADIUS_KM, WEIGHTS, ListingFacts, ReceiverFacts, rank

BREAD = ListingFacts(food_category_name="Bakery items", unit=QuantityUnit.PACKAGE, remaining=D(30),
                     urgency=UrgencyLevel.NORMAL)


def receiver(rid: str, **overrides) -> ReceiverFacts:  # noqa: ANN003
    base = {"receiver_id": rid, "name": rid, "category_name": "Welfare organization / NGO",
            "priority_weight": D("1.0"), "distance_km": 2.0, "pickup_radius_km": 10.0, "has_own_transport": False,
            "can_receive": True, "accepts_category": True, "capacity": D(20), "capacity_unit_matches": True,
            "recent_allocations": 0, "already_allocated": False}
    return ReceiverFacts(**{**base, **overrides})


def test_spec_example() -> None:
    result = rank(BREAD, [
        receiver("Community Kitchen B", category_name="Community kitchen", distance_km=6.0),
        receiver("NGO A", distance_km=1.8),
        receiver("Receiver C", can_receive=False),
    ], max_weight=D("1.0"))
    assert [m.receiver_id for m in result.matches] == ["NGO A", "Community Kitchen B"]  # nearer ranks higher
    assert [(e.receiver_id, e.reason) for e in result.excluded] == [
        ("Receiver C", "Account not verified, not active, or not eligible.")]
    assert result.matches[0].reasons[:3] == ["1.8 km away", "accepts Bakery items", "can take up to 20 packages"]
    assert result.matches[0].rank == 1 and result.model_version == "heuristic-v1"


@pytest.mark.parametrize(
    ("overrides", "reason_part"),
    [
        ({"accepts_category": False}, "Doesn't accept Bakery items"),
        ({"distance_km": 12.0}, "outside their 10 km pickup radius"),
        ({"already_allocated": True}, "Already allocated"),
        ({"distance_km": PLATFORM_MAX_RADIUS_KM + 1, "has_own_transport": True}, "outside their"),
    ],
)
def test_hard_filters_explain_exclusions(overrides: dict, reason_part: str) -> None:
    result = rank(BREAD, [receiver("x", **overrides)], max_weight=D(1))
    assert result.matches == [] and reason_part in result.excluded[0].reason


def test_own_transport_extends_the_radius() -> None:
    result = rank(BREAD, [receiver("far driver", distance_km=25.0, has_own_transport=True)], max_weight=D(1))
    assert result.matches and "has own transport" in result.matches[0].reasons


def test_score_is_the_weighted_sum_of_its_parts() -> None:
    match = rank(BREAD, [receiver("x", distance_km=5.0, capacity=D(15))], max_weight=D(1)).matches[0]
    c = match.components
    assert c["distance"] == pytest.approx(math.exp(-1), abs=1e-4)  # 5 km
    assert c["capacity_fit"] == pytest.approx(0.5)  # 15 vs 30
    assert c["collection_fit"] == 0.5 and c["urgency_feasibility"] == 1.0
    assert c["category_priority"] == 1.0 and c["fairness"] == 1.0
    assert match.score == pytest.approx(sum(WEIGHTS[k] * v for k, v in c.items()), abs=1e-3)


def test_capacity_in_a_different_unit_is_neutral() -> None:
    match = rank(BREAD, [receiver("x", capacity=D(5), capacity_unit_matches=False)], max_weight=D(1)).matches[0]
    assert match.components["capacity_fit"] == 0.5
    assert not any("can take up to" in r for r in match.reasons)


def test_urgent_food_prefers_receivers_who_can_collect_quickly() -> None:
    urgent = ListingFacts("Cooked meals", QuantityUnit.KG, D(12), UrgencyLevel.CRITICAL)
    result = rank(urgent, [receiver("slow", distance_km=6.0), receiver("near", distance_km=2.5),
                           receiver("driver", distance_km=6.0, has_own_transport=True)], max_weight=D(1))
    parts = {m.receiver_id: m.components["urgency_feasibility"] for m in result.matches}
    assert parts == {"slow": 0.2, "near": 1.0, "driver": 1.0}
    assert result.matches[-1].receiver_id == "slow"
    assert "can collect urgent food quickly" in next(m for m in result.matches if m.receiver_id == "near").reasons


def test_fairness_favours_receivers_who_got_less_recently() -> None:
    result = rank(BREAD, [receiver("often fed", recent_allocations=4), receiver("rarely fed", recent_allocations=0)],
                  max_weight=D(1))
    assert [m.receiver_id for m in result.matches] == ["rarely fed", "often fed"]
    assert "received less food recently" in result.matches[0].reasons
    assert result.matches[1].components["fairness"] == 0.0


def test_priority_weight_is_normalised() -> None:
    result = rank(BREAD, [receiver("ngo", priority_weight=D("1.0")),
                          receiver("shop", priority_weight=D("0.5"), category_name="Small food business")],
                  max_weight=D("1.0"))
    shop = next(m for m in result.matches if m.receiver_id == "shop")
    assert shop.components["category_priority"] == 0.5
    assert result.matches[0].receiver_id == "ngo"


def test_ties_go_to_the_nearer_receiver() -> None:
    result = rank(BREAD, [receiver("b"), receiver("a")], max_weight=D(1))
    assert [m.rank for m in result.matches] == [1, 2]
    assert [m.receiver_id for m in result.matches] == ["a", "b"]  # same distance -> stable id order


@settings(max_examples=1500, deadline=None)
@given(st.lists(st.builds(
    lambda i, d, t, w, n, cap, ok, acc: receiver(f"r{i}", distance_km=d, has_own_transport=t, priority_weight=w,
                                                  recent_allocations=n, capacity=cap, can_receive=ok,
                                                  accepts_category=acc),
    st.integers(0, 10_000), st.floats(0, 80), st.booleans(), st.sampled_from([D("0.5"), D("0.7"), D("1.0")]),
    st.integers(0, 10), st.none() | st.integers(1, 100).map(D), st.booleans(), st.booleans()),
    max_size=15, unique_by=lambda r: r.receiver_id))
def test_properties_hold_for_any_receivers(receivers: list) -> None:
    result = rank(BREAD, receivers, max_weight=D("1.0"))
    assert len(result.matches) + len(result.excluded) == len(receivers)  # everyone accounted for
    scores = [m.score for m in result.matches]
    assert scores == sorted(scores, reverse=True)  # ranked by score
    assert [m.rank for m in result.matches] == list(range(1, len(scores) + 1))
    for m in result.matches:
        assert 0 <= m.score <= 1
        assert all(0 <= v <= 1 for v in m.components.values())
        assert m.reasons
    excluded_ids = {e.receiver_id for e in result.excluded}
    for r in receivers:
        if not r.can_receive or not r.accepts_category:
            assert r.receiver_id in excluded_ids
