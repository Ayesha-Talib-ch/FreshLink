"""Fairness measures for the admin fairness report (plan 4.12 and 7.5 "Gini across receivers").

Pure functions with no database access, so they can be tested exhaustively.

Gini coefficient: 0 means every receiver got the same amount; values near 1 mean one receiver
got almost everything. With n receivers the largest possible value is 1 - 1/n."""

from collections.abc import Sequence
from decimal import Decimal


def gini(values: Sequence[float | Decimal]) -> float | None:
    """Gini coefficient of non-negative amounts. None when there is nothing to compare
    (no receivers, or nobody received anything)."""
    xs = sorted(float(v) for v in values)
    if any(x < 0 for x in xs):
        raise ValueError("Amounts can't be negative.")
    n, total = len(xs), sum(xs)
    if n == 0 or total == 0:
        return None
    # G = (2 * sum(i * x_i) / (n * total)) - (n + 1) / n, with i = 1..n over the sorted values
    weighted = sum(i * x for i, x in enumerate(xs, start=1))
    return round(max(0.0, 2 * weighted / (n * total) - (n + 1) / n), 4)


def fill_ratio(requested: float | Decimal, approved: float | Decimal) -> float:
    """Share of a request that was approved: 0 (rejected) .. 1 (fully approved)."""
    requested = float(requested)
    if requested <= 0:
        raise ValueError("A request is always for a positive quantity.")
    return max(0.0, min(1.0, float(approved) / requested))
