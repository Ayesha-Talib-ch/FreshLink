# Surplus estimation: evaluation

*Generated 2026-11-14 19:12 UTC by `python ml/evaluate_surplus.py` (training took 13 s).*

> **Data: synthetic.** These figures come from SIMULATED provider logs (`ml/data/generate.py`, fixed seed). They show that the method works and beats simple rules on data with realistic patterns; they are NOT evidence of accuracy for real Pakistani providers. Real accuracy is measured from logged estimates vs actual surplus once providers use the system.

## Set-up

- 60 provider/food-category histories; 27347 training rows; 3506 hold-out rows.
- Hold-out = the last 60 days (2026-09-15 to 2026-11-13), never used for training or model choice.
- 30 features, all computed from days at least 7 days before the target (estimates up to a week ahead, no future leakage).
- Model choice: TimeSeriesSplit (4 folds, by whole days, never shuffled) on the training period.

## Model choice (cross-validation MAE, lower is better)

| Model | CV MAE |
|---|---|
| Ridge (chosen) | 7.741 |
| HistGradientBoostingRegressor | 7.791 |
| RandomForestRegressor | 7.797 |

## Hold-out results

| | MAE | RMSE | R² |
|---|---|---|---|
| **Model (Ridge)** | **7.286** | 10.448 | 0.375 |
| Baseline B2: same-weekday mean of 4 weeks | 7.922 | 11.46 | 0.248 |
| Baseline B1: same weekday last week | 9.785 | 14.378 | -0.183 |

- Improvement over B2: **8.0 %** lower MAE.
- Uncertainty range (10 %-90 % quantile models): the actual surplus fell inside the range on **87.7 %** of hold-out days (target about 80 %).

## By food type (hold-out MAE)

| Food type | Rows | Model | B2 |
|---|---|---|---|
| bakery | 692 | 7.56 | 8.132 |
| cooked | 1060 | 7.232 | 7.899 |
| dairy | 586 | 5.292 | 5.784 |
| fresh_produce | 588 | 6.036 | 6.308 |
| packaged | 580 | 10.343 | 11.507 |

## Limitations

- Synthetic data: the patterns (weekday, season, Ramadan / Eid, over-production) were designed, so a model can learn them; real data will be noisier.
- Islamic dates in `backend/app/ml/calendar_pk.py` are expected dates and need a yearly update.
- The hold-out period does not contain Ramadan; its effect is learned from the training period only.
- MAE is in each listing's own unit (packages, kg, litres ...), averaged over all of them.
