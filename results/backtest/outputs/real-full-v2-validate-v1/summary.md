# Backtest real-full-v2-validate-v1 (real)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **pass**
- Protected paths unchanged: True
- Created (UTC): 2026-10-24T14:37:01.299966+00:00

## Gates

| gate | status | value | threshold |
| --- | --- | --- | --- |
| jackknife_median_shift[cops/enmo_hourly_median_mean_g] | pass | 0.04872 | 0.5 |
| jackknife_median_shift[cops/walking_cadence_hourly_median_mean_hz] | pass | 0.03527 | 0.5 |
| jackknife_median_shift[cops/rest_tremor_window_fraction] | pass | 0.04512 | 0.5 |
| jackknife_median_shift[nhanes/enmo_hourly_median_mean_g] | pass | 0.02858 | 0.5 |
| jackknife_median_shift[nhanes/walking_cadence_hourly_median_mean_hz] | pass | 0.0272 | 0.5 |
| jackknife_median_shift[nhanes/rest_tremor_window_fraction] | pass | 0.03205 | 0.5 |
| bootstrap_median_ci_width[cops/enmo_hourly_median_mean_g] | pass | 0.4474 | 2 |
| bootstrap_median_ci_width[cops/walking_cadence_hourly_median_mean_hz] | pass | 0.8962 | 2 |
| bootstrap_median_ci_width[cops/rest_tremor_window_fraction] | pass | 1.043 | 2 |
| bootstrap_median_ci_width[nhanes/enmo_hourly_median_mean_g] | pass | 0.5062 | 2 |
| bootstrap_median_ci_width[nhanes/walking_cadence_hourly_median_mean_hz] | pass | 0.5057 | 2 |
| bootstrap_median_ci_width[nhanes/rest_tremor_window_fraction] | pass | 0.3614 | 2 |
| no_lookahead[train] | pass | 0 | 0 |
| no_lookahead[valid] | pass | 0 | 0 |
| schema_and_semantic_validation | pass | 1539 | build_analysis/validate_output |
| schema_and_semantic_validation | pass | 243 | build_analysis/validate_output |
| baseline_unchanged | pass | True | True |
| audit[baseline_lock] | pass | 0 failed / 3 checked | 0 failed |
| audit[hash_chain] | pass | 0 failed / 9 checked | 0 failed |
| audit[reference_formulas] | pass | 0 failed / 54 checked | 0 failed |
| audit[distances] | pass | 0 failed / 4860 checked | 0 failed |
| audit[null_handling] | pass | 0 failed / 486 checked | 0 failed |
| audit[previous_day] | pass | 0 failed / 972 checked | 0 failed |
| audit[coverage] | pass | 0 failed / 2742 checked | 0 failed |
| audit[official_reports] | pass | 0 failed / 269 checked | 0 failed |
| audit[test_lock] | pass | 0 failed / 3 checked | 0 failed |
| protected_paths_unchanged | pass | [] | ["src", "tools", "config", "metadata", "tests"] |

## Evaluability

| dataset | metric | ref people | full | LOO/jackknife | bootstrap | valid | test |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| cops | enmo_hourly_median_mean_g | 31 | True | True | True | distribution | blocked_no_test_features |
| cops | walking_cadence_hourly_median_mean_hz | 24 | True | True | True | distribution | blocked_no_test_features |
| cops | rest_tremor_window_fraction | 31 | True | True | True | distribution | blocked_no_test_features |
| nhanes | enmo_hourly_median_mean_g | 58 | True | True | True | distribution | blocked_no_test_features |
| nhanes | walking_cadence_hourly_median_mean_hz | 50 | True | True | True | distribution | blocked_no_test_features |
| nhanes | rest_tremor_window_fraction | 58 | True | True | True | distribution | blocked_no_test_features |

## Validation audit

- baseline SHA256: `e2a35a02ff067505c6594a3abb944d063fe4d3a31a6c7cad586af55631510bc9` (หลัง audit: `e2a35a02ff067505c6594a3abb944d063fe4d3a31a6c7cad586af55631510bc9`)
- fit ระหว่าง validation: False

| หมวด | status | checked | failed |
| --- | --- | ---: | ---: |
| baseline_lock | pass | 3 | 0 |
| hash_chain | pass | 9 | 0 |
| reference_formulas | pass | 54 | 0 |
| distances | pass | 4860 | 0 |
| null_handling | pass | 486 | 0 |
| previous_day | pass | 972 | 0 |
| coverage | pass | 2742 | 0 |
| official_reports | pass | 269 | 0 |
| test_lock | pass | 3 | 0 |

| กรณี null | exercised | violations | status |
| --- | ---: | ---: | --- |
| daily_insufficient_all_null | 19 | 0 | pass |
| reference_insufficient_null_distance | 0 | 0 | not_exercised |
| zero_scale_distance_without_z | 0 | 0 | not_exercised |
| ok_comparison_complete | 448 | 0 | pass |
| csv_null_not_zero | 19 | 0 | pass |
| tremor_zero_requires_windows | 0 | 0 | not_exercised |

| previous_day | n | status |
| --- | ---: | --- |
| no_previous_day | 3 | exercised |
| previous_day_missing | 60 | exercised |
| current_day_insufficient_data | 14 | exercised |
| previous_day_insufficient_data | 7 | exercised |
| ok | 159 | exercised |

- เทียบ analysis ทางการ: {"exact_rows": 243, "roundoff_rows": 0, "mismatch_rows": 0, "max_relative_roundoff": 0.0, "n_valid_excluded": 14}
- float parse (pandas default vs round_trip): {"n_values": 224, "n_not_round_trip": 169, "max_relative_difference": 2.985372365982286e-14}
- ข้อจำกัด: no hourly features: daily metric values cannot be recomputed from hourly rows

## Device-confound flags

- reference_gap_in_pooled_scale: amplitude metric separates cohorts >= 2.0x more than non-amplitude metrics -> possible device (brand/gain) effect; do not attribute the gap to disease
- valid_gap_in_pooled_scale: amplitude metric separates cohorts >= 2.0x more than non-amplitude metrics -> possible device (brand/gain) effect; do not attribute the gap to disease

## ความพร้อมเปิด test (ต่อ cohort × ตัวชี้วัดที่ reference ok)

| dataset | metric | ready | เหตุผล |
| --- | --- | --- | --- |
| cops | enmo_hourly_median_mean_g | True | — |
| cops | walking_cadence_hourly_median_mean_hz | True | — |
| cops | rest_tremor_window_fraction | True | — |
| nhanes | enmo_hourly_median_mean_g | True | — |
| nhanes | walking_cadence_hourly_median_mean_hz | True | — |
| nhanes | rest_tremor_window_fraction | True | — |

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
