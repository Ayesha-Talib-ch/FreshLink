"""Machine-learning code used by the API at prediction time (plan 7.7).

Shared between training and serving, so the model always sees features built the same way:
- calendar_pk.py: Pakistani calendar effects (Ramadan, Eid, public holidays)
- surplus_features.py: turns a provider's daily history into model features
- surplus_training.py: trains, compares and evaluates the surplus models
Training scripts and the synthetic data generator live in the repository's ml/ folder."""
