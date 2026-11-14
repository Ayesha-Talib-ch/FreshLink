# ml/ — AI/ML training and evaluation (not started yet)

Planned stack: Python, pandas, NumPy, scikit-learn, OR-Tools.
Owner (Technical Plan §11): **M3 — ML & Mobile Lead**.

Will contain: the synthetic data generator, training scripts, evaluation notebooks
and the hand-labelled matching evaluation set. Inference code that the API calls
lives in `backend/app/ml/`. Trained model files (`artifacts/`) are not committed.

Honesty rule: results from synthetic data are labelled "synthetic". They are not
proof of real-world accuracy, and no module ever claims that food is safe to eat.
