# Model card: taxi-trip-duration

## Summary

Predicts how many minutes a New York City yellow-taxi trip will take, using only what is known when the passenger is picked up. It is served behind the public web page and the `/predict` API. The live version is whichever version holds the `@champion` alias in the MLflow registry.

| | |
|---|---|
| Model | LightGBM gradient-boosted trees, tuned with Optuna |
| Registry | MLflow, `models:/taxi-trip-duration@champion` (v2) |
| Trained on | January 2025 gold snapshot, 3,287,442 trips, SHA-256 pinned |
| Output | Trip duration in minutes, clipped at 0 |
| Owner | ML Platform |

## Intended use

- Give a rough ride-time estimate before a trip starts.
- Demonstrate an end-to-end, reproducible ML platform for a portfolio.
- Not for pricing, dispatch, driver evaluation or any decision about a person.

## Inputs

These come from `taxi_contracts.TripFeatures` and are validated at the API:

- pickup zone and dropoff zone (TLC zone ids 1 to 265), and the zone pair as a route;
- trip distance in miles, plus its log;
- pickup hour (also encoded as a cycle), ISO weekday and a weekend flag.

No personal data is used. The fare, tip and payment type are never used, because they are only known after the trip ends (leakage).

## Data

- Source: NYC TLC Yellow Taxi trip records (public).
- Cleaning: silver-layer rules keep 1 to 120 minute trips, 0 to 100 miles, at most 80 mph and known zones; duplicates are removed.
- Split by time, never at random:
  - train on days 1 to 17;
  - validate on days 18 to 24;
  - test on the last 7 days (25 to 31).

## Performance

These are measured on the held-out last week of January 2025.

| Model | Test RMSE (min) | MAE | R² |
|---|---|---|---|
| Mean baseline | 11.06 | | |
| RandomForest (v1, `@previous`) | 4.52 | | |
| **LightGBM (v2, `@champion`)** | **4.29** | 2.58 | 0.85 |

Test RMSE (minutes) by slice for the champion:

| Slice | RMSE |
|---|---|
| Under 2 miles | 2.64 |
| 2 to 5 miles | 4.09 |
| 5 to 10 miles | 6.86 |
| 10 miles and over | 9.81 |
| Weekday 7 to 10 am | 5.25 |
| Weekday 4 to 8 pm | 4.15 |
| Night, 0 to 6 am | 3.52 |
| Weekend | 3.67 |
| Airport pickup or dropoff | 7.00 |

## Monitoring

Monitoring runs monthly, after each new gold month. It checks two things:

- PSI drift per feature and on the target;
- the champion's RMSE on the new month.

February 2025 compared with January 2025:

- every feature is stable (all PSI values are below 0.02);
- the champion's RMSE is 4.52, which is 5.3% worse than on its own test week;
- retraining is not yet recommended, because the threshold is a 10% degradation or significant drift.

## Promotion rules

A challenger replaces the champion only if all of these hold:

- it beats the mean baseline;
- it is at least 0.5% better in RMSE on the same test week;
- no slice gets more than 5% worse.

When a challenger is promoted, the old champion is kept as `@previous` for one-step rollback.

## Limitations

- Errors grow with distance and are largest for airport trips: the model does not know about traffic incidents, weather or the route taken.
- It is trained on a single month, so seasonal effects such as holidays and summer are not learned until more months are added.
- The distance is the actual trip distance from the record. At booking time, a planned-route distance would be used instead, which adds error.
- Yellow taxis only; green taxis and for-hire vehicles behave differently.

## Ethical considerations

Zone-level aggregates only and no rider or driver identifiers. Predictions are estimates and are shown as such on the page.
