# ml/ - AI / ML training data and evaluation

Planned stack (Technical Plan 7): Python, NumPy, scikit-learn. Inference code that the API uses lives in
`backend/app/ml/` (shared feature builder, calendar, training); this folder holds the data generator and
the evaluation scripts. Trained model files (`artifacts/`) and generated CSVs are not committed.

## Surplus estimation (Stage 10a)

Run from the repository folder with the backend venv active:

| Command | What it does |
|---|---|
| `python ml/data/generate.py` | Writes `ml/data/synthetic_daily_logs.csv` (fixed seed: always the same 32 043 rows) |
| `python ml/data/generate.py --provider P07 --import-csv demo.csv` | One synthetic provider in the API's CSV import format (for demos) |
| `python ml/evaluate_surplus.py` | Regenerates the data, trains like the API does, writes `ml/reports/surplus_evaluation.md` |
| `cd backend; python -m app.cli train-surplus --csv ../ml/data/synthetic_daily_logs.csv` | Trains, saves the model in `ml/artifacts/` and registers it (activated only if it beats the baselines and the current model) |

Honesty rule: results from synthetic data are labelled "synthetic". They are not proof of real-world
accuracy, and no module ever claims that food is safe to eat.
