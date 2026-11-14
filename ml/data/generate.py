"""Synthetic provider daily logs for training and testing surplus estimation (plan 7.1).

    python ml/data/generate.py                       -> ml/data/synthetic_daily_logs.csv
    python ml/data/generate.py --provider P07 --import-csv demo.csv
                                                     -> one provider's last 120 days in the API's CSV import format,
                                                        moved so they end yesterday (for demos)

HONESTY RULE: this is simulated data. Every row says source=synthetic, and any accuracy figure based
on it must be reported as "on synthetic data", not as proof of real-world accuracy.

How a day is simulated (plan 7.1):
  demand   = base x weekday pattern x season x Ramadan / Eid / holiday effect x random shock
  produced = what the provider PLANS: the same pattern, but without knowing today's shock and only
             partly anticipating Ramadan / Eid, plus the provider's habit of over-producing (5-25 %)
  sold     = min(produced, demand)        surplus = produced - sold
The seed and the end date are fixed, so every run produces exactly the same file."""

import argparse
import csv
import math
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))  # shared calendar
from app.ml import calendar_pk  # noqa: E402

SEED = 42
END = date(2026, 11, 13)  # fixed, so the data never changes between runs
DAYS = 550  # about 18 months
OUT = Path(__file__).resolve().parent / "synthetic_daily_logs.csv"
DEMO_DAYS = 120  # --import-csv: the API accepts logs up to a year old; 120 days is plenty for the model

# provider type -> (number of providers, [(food category code, perishability, unit, typical daily demand)])
TYPES = {
    "bakery": (12, [("bakery_items", "bakery", "package", 80)]),
    "restaurant": (10, [("cooked_meals", "cooked", "kg", 40)]),
    "fast_food": (8, [("fast_food", "cooked", "portion", 120)]),
    "grocery": (10, [("fresh_produce", "fresh_produce", "kg", 60), ("dairy", "dairy", "litre", 50),
                     ("packaged_food", "packaged", "package", 100)]),
}
# weekday pattern (Mon..Sun) per provider type
WEEKDAY = {
    "bakery": [0.95, 0.92, 0.95, 0.97, 1.02, 1.08, 1.15],
    "restaurant": [0.85, 0.85, 0.9, 0.95, 1.1, 1.2, 1.2],
    "fast_food": [0.85, 0.85, 0.9, 0.95, 1.15, 1.25, 1.15],
    "grocery": [0.95, 0.95, 0.95, 1.0, 1.05, 1.1, 1.05],
}
# Ramadan and Eid change sales a lot in Pakistan (iftar, Eid sweets, closed shops)
RAMADAN = {"bakery": 1.25, "restaurant": 0.8, "fast_food": 1.15, "fresh_produce": 1.2, "dairy": 1.1, "packaged": 1.05}
EID = {"bakery": 1.6, "restaurant": 1.4, "fast_food": 1.3, "fresh_produce": 0.6, "dairy": 0.7, "packaged": 0.8}


@dataclass
class Series:
    provider: str
    provider_type: str
    category: str
    perishability: str
    unit: str
    base: float
    over_production: float  # this provider's habit: plans this much more than expected demand
    anticipation: float  # how much of the Ramadan / Eid effect the provider plans for (0..1)


def _effect_key(s: Series) -> str:
    return s.provider_type if s.provider_type != "grocery" else s.perishability


def _season(s: Series, day: date) -> float:
    if s.provider_type == "restaurant" and day.month in (11, 12, 1, 2):
        return 1.15  # wedding season
    if s.perishability == "dairy":
        return 1.0 + 0.08 * math.cos(2 * math.pi * (day.month - 6) / 12)  # more in summer
    return 1.0 + 0.05 * math.sin(2 * math.pi * day.month / 12)


def _calendar(s: Series, day: date) -> float:
    key = _effect_key(s)
    if calendar_pk.is_eid(day):
        return EID[key]
    effect = RAMADAN[key] if calendar_pk.is_ramadan(day) else 1.0
    if calendar_pk.days_until_eid(day) in (1, 2) and s.provider_type == "grocery":
        effect *= 1.3  # stocking up before Eid
    if calendar_pk.is_holiday(day):
        effect *= 0.9
    return effect


def make_series(rng: np.random.Generator) -> list[Series]:
    out, n = [], 0
    for provider_type, (count, categories) in TYPES.items():
        for _ in range(count):
            n += 1
            size = float(rng.lognormal(0, 0.45))  # small shops and big ones
            over = float(rng.uniform(0.05, 0.25))
            anticipation = float(rng.uniform(0.3, 0.8))
            for code, perishability, unit, typical in categories:
                out.append(Series(f"P{n:02d}", provider_type, code, perishability, unit,
                                  typical * size * float(rng.uniform(0.85, 1.15)), over, anticipation))
    return out


def simulate(s: Series, rng: np.random.Generator) -> list[dict]:
    rows = []
    start = END - timedelta(days=DAYS - 1)
    trend = float(rng.normal(0, 0.15))  # some shops grow, some shrink over the period
    countable = s.unit in ("package", "piece", "portion", "tray")
    for i in range(DAYS):
        day = start + timedelta(days=i)
        if rng.random() < 0.03:
            continue  # closed, or the day wasn't logged
        pattern = s.base * WEEKDAY[s.provider_type][day.weekday()] * _season(s, day) * (1 + trend * i / DAYS)
        calendar_effect = _calendar(s, day)
        shock = float(rng.lognormal(0, 0.12))
        if rng.random() < 0.02:
            shock *= float(rng.choice([0.5, 1.6]))  # rain, a power cut, a big order ...
        demand = pattern * calendar_effect * shock
        planned_effect = 1 + (calendar_effect - 1) * s.anticipation
        produced = pattern * planned_effect * (1 + s.over_production) * float(rng.normal(1, 0.03))
        produced = max(produced, 0.0)
        if countable:
            produced, demand = float(round(produced)), float(round(demand))
        else:
            produced, demand = round(produced, 1), round(demand, 1)
        sold = min(produced, demand)
        rows.append({"provider_key": s.provider, "provider_type": s.provider_type, "food_category_code": s.category,
                     "perishability": s.perishability, "unit": s.unit, "date": day.isoformat(),
                     "produced": produced, "sold": sold, "surplus": round(produced - sold, 2), "source": "synthetic"})
    return rows


def generate() -> list[dict]:
    rng = np.random.default_rng(SEED)
    rows: list[dict] = []
    for s in make_series(rng):
        rows.extend(simulate(s, rng))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", help="with --import-csv: which synthetic provider, e.g. P07")
    parser.add_argument("--import-csv", type=Path, help="write that provider's history in the API's CSV import format")
    args = parser.parse_args()
    rows = generate()
    if args.import_csv:
        mine = [r for r in rows if r["provider_key"] == args.provider]
        shift = (date.today() - timedelta(days=1)) - END  # the demo history ends yesterday
        mine = [{**r, "date": (date.fromisoformat(r["date"]) + shift).isoformat()} for r in mine
                if date.fromisoformat(r["date"]) > END - timedelta(days=DEMO_DAYS)]
        if not mine:
            print(f"[x] No synthetic provider called {args.provider!r} (P01 ... P40).")
            return 1
        with args.import_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "food_category_code", "unit", "produced", "sold"])
            w.writerows([r["date"], r["food_category_code"], r["unit"], r["produced"], r["sold"]] for r in mine)
        print(f"[OK] {len(mine)} days of {args.provider} written to {args.import_csv}")
        return 0
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    series = len({(r["provider_key"], r["food_category_code"]) for r in rows})
    print(f"[OK] {len(rows)} synthetic daily logs ({series} provider/category series, "
          f"{rows[0]['date']} to {END}) written to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
