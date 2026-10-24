# Backtest real-checkpoint01-v1 (real)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **fail**
- Protected paths unchanged: True
- Created (UTC): 2026-10-23T17:33:17.099441+00:00

## Gates

| gate | status | value | threshold |
| --- | --- | --- | --- |
| jackknife_median_shift[cops/enmo_hourly_median_mean_g] | pass | 0.3426 | 0.5 |
| jackknife_median_shift[cops/walking_cadence_hourly_median_mean_hz] | not_evaluable | — | 0.5 |
| jackknife_median_shift[cops/rest_tremor_window_fraction] | fail | 0.539 | 0.5 |
| jackknife_median_shift[nhanes/enmo_hourly_median_mean_g] | not_evaluable | — | 0.5 |
| jackknife_median_shift[nhanes/walking_cadence_hourly_median_mean_hz] | not_evaluable | — | 0.5 |
| jackknife_median_shift[nhanes/rest_tremor_window_fraction] | not_evaluable | — | 0.5 |
| bootstrap_median_ci_width[cops/enmo_hourly_median_mean_g] | pass | 1.086 | 2 |
| bootstrap_median_ci_width[cops/walking_cadence_hourly_median_mean_hz] | pass | 1.761 | 2 |
| bootstrap_median_ci_width[cops/rest_tremor_window_fraction] | pass | 1.545 | 2 |
| bootstrap_median_ci_width[nhanes/enmo_hourly_median_mean_g] | fail | 2.692 | 2 |
| bootstrap_median_ci_width[nhanes/walking_cadence_hourly_median_mean_hz] | not_evaluable | — | 2 |
| bootstrap_median_ci_width[nhanes/rest_tremor_window_fraction] | pass | 1.513 | 2 |
| no_lookahead[train] | pass | 0 | 0 |
| no_lookahead[valid] | pass | 0 | 0 |
| schema_and_semantic_validation | pass | 180 | build_analysis/validate_output |
| schema_and_semantic_validation | pass | 33 | build_analysis/validate_output |
| baseline_unchanged | pass | True | True |
| protected_paths_unchanged | pass | [] | ["src", "tools", "config", "metadata", "tests"] |

## Evaluability

| dataset | metric | ref people | full | LOO/jackknife | bootstrap | valid | test |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| cops | enmo_hourly_median_mean_g | 6 | True | True | True | descriptive_only | blocked_no_test_features |
| cops | walking_cadence_hourly_median_mean_hz | 5 | True | False | True | unavailable | blocked_no_test_features |
| cops | rest_tremor_window_fraction | 6 | True | True | True | descriptive_only | blocked_no_test_features |
| nhanes | enmo_hourly_median_mean_g | 5 | True | False | True | descriptive_only | blocked_no_test_features |
| nhanes | walking_cadence_hourly_median_mean_hz | 4 | False | False | False | descriptive_only | blocked_no_test_features |
| nhanes | rest_tremor_window_fraction | 5 | True | False | True | descriptive_only | blocked_no_test_features |

## Device-confound flags

- reference_gap_in_pooled_scale: amplitude metric separates cohorts >= 2.0x more than non-amplitude metrics -> possible device (brand/gain) effect; do not attribute the gap to disease

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
