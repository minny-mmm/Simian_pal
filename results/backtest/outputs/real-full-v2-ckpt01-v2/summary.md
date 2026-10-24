# Backtest real-full-v2-ckpt01-v2 (real)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **pass_with_unevaluable**
- Protected paths unchanged: True
- Created (UTC): 2026-10-24T10:05:05.857145+00:00

## Gates

| gate | status | value | threshold |
| --- | --- | --- | --- |
| jackknife_median_shift[cops/enmo_hourly_median_mean_g] | pass | 0.1835 | 0.5 |
| jackknife_median_shift[cops/walking_cadence_hourly_median_mean_hz] | pass | 0.141 | 0.5 |
| jackknife_median_shift[cops/rest_tremor_window_fraction] | pass | 0.1625 | 0.5 |
| jackknife_median_shift[nhanes/enmo_hourly_median_mean_g] | not_evaluable | — | 0.5 |
| jackknife_median_shift[nhanes/walking_cadence_hourly_median_mean_hz] | not_evaluable | — | 0.5 |
| jackknife_median_shift[nhanes/rest_tremor_window_fraction] | not_evaluable | — | 0.5 |
| bootstrap_median_ci_width[cops/enmo_hourly_median_mean_g] | pass | 1.221 | 2 |
| bootstrap_median_ci_width[cops/walking_cadence_hourly_median_mean_hz] | pass | 1.443 | 2 |
| bootstrap_median_ci_width[cops/rest_tremor_window_fraction] | pass | 1.858 | 2 |
| bootstrap_median_ci_width[nhanes/enmo_hourly_median_mean_g] | not_evaluable | — | 2 |
| bootstrap_median_ci_width[nhanes/walking_cadence_hourly_median_mean_hz] | not_evaluable | — | 2 |
| bootstrap_median_ci_width[nhanes/rest_tremor_window_fraction] | not_evaluable | — | 2 |
| no_lookahead[train] | pass | 0 | 0 |
| no_lookahead[valid] | not_evaluable | — | 0 |
| schema_and_semantic_validation | pass | 129 | build_analysis/validate_output |
| baseline_unchanged | pass | True | True |
| audit[baseline_lock] | pass | 0 failed / 3 checked | 0 failed |
| audit[hash_chain] | pass | 0 failed / 9 checked | 0 failed |
| audit[reference_formulas] | pass | 0 failed / 54 checked | 0 failed |
| audit[test_lock] | pass | 0 failed / 3 checked | 0 failed |
| protected_paths_unchanged | pass | [] | ["src", "tools", "config", "metadata", "tests"] |

## Evaluability

| dataset | metric | ref people | full | LOO/jackknife | bootstrap | valid | test |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| cops | enmo_hourly_median_mean_g | 9 | True | True | True | unavailable | blocked_no_test_features |
| cops | walking_cadence_hourly_median_mean_hz | 7 | True | True | True | unavailable | blocked_no_test_features |
| cops | rest_tremor_window_fraction | 9 | True | True | True | unavailable | blocked_no_test_features |
| nhanes | enmo_hourly_median_mean_g | 0 | False | False | False | unavailable | blocked_no_test_features |
| nhanes | walking_cadence_hourly_median_mean_hz | 0 | False | False | False | unavailable | blocked_no_test_features |
| nhanes | rest_tremor_window_fraction | 0 | False | False | False | unavailable | blocked_no_test_features |

## Validation audit

- baseline SHA256: `f8b422a825d6bb2c123aa6da65176f7177d87b3db69e21e74740f6355f302149` (หลัง audit: `f8b422a825d6bb2c123aa6da65176f7177d87b3db69e21e74740f6355f302149`)
- fit ระหว่าง validation: False

| หมวด | status | checked | failed |
| --- | --- | ---: | ---: |
| baseline_lock | pass | 3 | 0 |
| hash_chain | pass | 9 | 0 |
| reference_formulas | pass | 54 | 0 |
| test_lock | pass | 3 | 0 |
- ข้อจำกัด: valid bundle unavailable: distances/null/coverage not audited

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
