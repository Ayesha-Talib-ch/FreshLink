"""Reproduce the surplus-estimation evaluation for the report (plan 7.7 "every evaluation figure is
reproduced by one command").

    python ml/evaluate_surplus.py      (run from the repository folder, with the backend venv)

It regenerates the synthetic data from its fixed seed, trains exactly as the API does
(backend/app/ml/surplus_training.py) and writes ml/reports/surplus_evaluation.md.
It does not touch the database or the active model."""

import importlib.util
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))

from app.ml.surplus_features import FEATURE_NAMES, GAP  # noqa: E402
from app.ml.surplus_training import HOLDOUT_DAYS, train  # noqa: E402
from app.services.ml_service import series_from_csv  # noqa: E402

REPORT = ROOT / "reports" / "surplus_evaluation.md"


def _generate() -> Path:
    spec = importlib.util.spec_from_file_location("generate", ROOT / "data" / "generate.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    import csv

    rows = gen.generate()
    with gen.OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return gen.OUT


def main() -> int:
    path = _generate()
    series, source = series_from_csv(path)
    started = time.time()
    result = train(series)
    seconds = time.time() - started
    m = result.metrics
    lines = [
        "# Surplus estimation: evaluation",
        "",
        f"*Generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC by `python ml/evaluate_surplus.py` "
        f"(training took {seconds:.0f} s).*",
        "",
        f"> **Data: {source}.** These figures come from SIMULATED provider logs (`ml/data/generate.py`, fixed seed). "
        "They show that the method works and beats simple rules on data with realistic patterns; they are NOT "
        "evidence of accuracy for real Pakistani providers. Real accuracy is measured from logged estimates "
        "vs actual surplus once providers use the system.",
        "",
        "## Set-up",
        "",
        f"- {len(series)} provider/food-category histories; {result.training_rows} training rows; "
        f"{m['holdout_rows']} hold-out rows.",
        f"- Hold-out = the last {HOLDOUT_DAYS} days ({m['holdout_period'][0]} to {m['holdout_period'][1]}), never "
        "used for training or model choice.",
        f"- {len(FEATURE_NAMES)} features, all computed from days at least {GAP} days before the target "
        "(estimates up to a week ahead, no future leakage).",
        "- Model choice: TimeSeriesSplit (4 folds, by whole days, never shuffled) on the training period.",
        "",
        "## Model choice (cross-validation MAE, lower is better)",
        "",
        "| Model | CV MAE |",
        "|---|---|",
        *(f"| {name}{' (chosen)' if name == m['chosen'] else ''} | {mae} |"
          for name, mae in sorted(m["cross_validation_mae"].items(), key=lambda kv: kv[1])),
        "",
        "## Hold-out results",
        "",
        "| | MAE | RMSE | R² |",
        "|---|---|---|---|",
        f"| **Model ({m['chosen']})** | **{m['model']['mae']}** | {m['model']['rmse']} | {m['model']['r2']} |",
        f"| Baseline B2: same-weekday mean of 4 weeks | {m['baseline_b2']['mae']} | {m['baseline_b2']['rmse']} | "
        f"{m['baseline_b2']['r2']} |",
        f"| Baseline B1: same weekday last week | {m['baseline_b1']['mae']} | {m['baseline_b1']['rmse']} | "
        f"{m['baseline_b1']['r2']} |",
        "",
        f"- Improvement over B2: **{m['improvement_over_b2_pct']} %** lower MAE.",
        f"- Uncertainty range (10 %-90 % quantile models): the actual surplus fell inside the range on "
        f"**{m['range_coverage_pct']} %** of hold-out days (target about 80 %).",
        "",
        "## By food type (hold-out MAE)",
        "",
        "| Food type | Rows | Model | B2 |",
        "|---|---|---|---|",
        *(f"| {kind} | {v['rows']} | {v['model_mae']} | {v['b2_mae']} |" for kind, v in m["per_food_type"].items()),
        "",
        "## Limitations",
        "",
        "- Synthetic data: the patterns (weekday, season, Ramadan / Eid, over-production) were designed, so a model "
        "can learn them; real data will be noisier.",
        "- Islamic dates in `backend/app/ml/calendar_pk.py` are expected dates and need a yearly update.",
        "- The hold-out period does not contain Ramadan; its effect is learned from the training period only.",
        "- MAE is in each listing's own unit (packages, kg, litres ...), averaged over all of them.",
    ]
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[OK] {m['chosen']}: hold-out MAE {m['model']['mae']} vs B2 {m['baseline_b2']['mae']} "
          f"({m['improvement_over_b2_pct']} % better); report written to {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
