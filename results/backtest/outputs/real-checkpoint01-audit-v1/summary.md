# Backtest real-checkpoint01-audit-v1 (real)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **fail**
- Protected paths unchanged: True
- Created (UTC): 2026-10-23T17:56:54.641172+00:00

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
| audit[baseline_lock] | pass | 0 failed / 3 checked | 0 failed |
| audit[hash_chain] | pass | 0 failed / 9 checked | 0 failed |
| audit[reference_formulas] | pass | 0 failed / 54 checked | 0 failed |
| audit[distances] | pass | 0 failed / 660 checked | 0 failed |
| audit[null_handling] | pass | 0 failed / 66 checked | 0 failed |
| audit[previous_day] | pass | 0 failed / 132 checked | 0 failed |
| audit[coverage] | pass | 0 failed / 381 checked | 0 failed |
| audit[official_reports] | pass | 0 failed / 59 checked | 0 failed |
| audit[test_lock] | pass | 0 failed / 3 checked | 0 failed |
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

## Validation audit

- baseline SHA256: `cb844f813f2adadb4fcb84dee4beaeaf60dd7ec3b79fe2df02ffc7226da760de` (หลัง audit: `cb844f813f2adadb4fcb84dee4beaeaf60dd7ec3b79fe2df02ffc7226da760de`)
- fit ระหว่าง validation: False

| หมวด | status | checked | failed |
| --- | --- | ---: | ---: |
| baseline_lock | pass | 3 | 0 |
| hash_chain | pass | 9 | 0 |
| reference_formulas | pass | 54 | 0 |
| distances | pass | 660 | 0 |
| null_handling | pass | 66 | 0 |
| previous_day | pass | 132 | 0 |
| coverage | pass | 381 | 0 |
| official_reports | pass | 59 | 0 |
| test_lock | pass | 3 | 0 |

| กรณี null | exercised | violations | status |
| --- | ---: | ---: | --- |
| daily_insufficient_all_null | 7 | 0 | pass |
| reference_insufficient_null_distance | 4 | 0 | pass |
| zero_scale_distance_without_z | 0 | 0 | not_exercised |
| ok_comparison_complete | 48 | 0 | pass |
| csv_null_not_zero | 7 | 0 | pass |
| tremor_zero_requires_windows | 0 | 0 | not_exercised |

| previous_day | n | status |
| --- | ---: | --- |
| no_previous_day | 0 | not_exercised |
| previous_day_missing | 6 | exercised |
| current_day_insufficient_data | 5 | exercised |
| previous_day_insufficient_data | 2 | exercised |
| ok | 20 | exercised |

- เทียบ analysis ทางการ: {"exact_rows": 33, "roundoff_rows": 0, "mismatch_rows": 0, "max_relative_roundoff": 0.0, "n_valid_excluded": 27}
- float parse (pandas default vs round_trip): {"n_values": 26, "n_not_round_trip": 20, "max_relative_difference": 4.6324077381355536e-15}
- ข้อจำกัด: no hourly features: daily metric values cannot be recomputed from hourly rows
- ข้อจำกัด: valid people per cohort {'cops': 1, 'nhanes': 1}: verifies pipeline correctness, not performance

## Device-confound flags

- reference_gap_in_pooled_scale: amplitude metric separates cohorts >= 2.0x more than non-amplitude metrics -> possible device (brand/gain) effect; do not attribute the gap to disease

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
