# Backtest synthetic-demo-A (synthetic)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **pass_with_unevaluable**
- Protected paths unchanged: True
- Created (UTC): 2026-10-24T08:32:19.184241+00:00

## Gates

| gate | status | value | threshold |
| --- | --- | --- | --- |
| jackknife_median_shift[cops/enmo_hourly_median_mean_g] | pass | 0.196 | 0.5 |
| jackknife_median_shift[cops/walking_cadence_hourly_median_mean_hz] | pass | 0.06092 | 0.5 |
| jackknife_median_shift[cops/rest_tremor_window_fraction] | pass | 0.0937 | 0.5 |
| jackknife_median_shift[nhanes/enmo_hourly_median_mean_g] | pass | 0.1632 | 0.5 |
| jackknife_median_shift[nhanes/walking_cadence_hourly_median_mean_hz] | not_evaluable | — | 0.5 |
| jackknife_median_shift[nhanes/rest_tremor_window_fraction] | not_evaluable | — | 0.5 |
| bootstrap_median_ci_width[cops/enmo_hourly_median_mean_g] | pass | 1.753 | 10 |
| bootstrap_median_ci_width[cops/walking_cadence_hourly_median_mean_hz] | pass | 2.041 | 10 |
| bootstrap_median_ci_width[cops/rest_tremor_window_fraction] | pass | 2.597 | 10 |
| bootstrap_median_ci_width[nhanes/enmo_hourly_median_mean_g] | pass | 1.22 | 10 |
| bootstrap_median_ci_width[nhanes/walking_cadence_hourly_median_mean_hz] | not_evaluable | — | 10 |
| bootstrap_median_ci_width[nhanes/rest_tremor_window_fraction] | not_evaluable | — | 10 |
| no_lookahead[train] | pass | 0 | 0 |
| no_lookahead[train_hourly] | pass | 0 | 0 |
| no_lookahead[valid] | pass | 0 | 0 |
| no_lookahead[valid_hourly] | pass | 0 | 0 |
| schema_and_semantic_validation | pass | 480 | build_analysis/validate_output |
| schema_and_semantic_validation | pass | 240 | build_analysis/validate_output |
| baseline_unchanged | pass | True | True |
| audit[baseline_lock] | pass | 0 failed / 3 checked | 0 failed |
| audit[hash_chain] | pass | 0 failed / 9 checked | 0 failed |
| audit[reference_formulas] | pass | 0 failed / 54 checked | 0 failed |
| audit[distances] | pass | 0 failed / 4800 checked | 0 failed |
| audit[null_handling] | pass | 0 failed / 516 checked | 0 failed |
| audit[previous_day] | pass | 0 failed / 960 checked | 0 failed |
| audit[coverage] | pass | 0 failed / 2631 checked | 0 failed |
| audit[official_reports] | not_evaluable | 0 failed / 0 checked | 0 failed |
| audit[test_lock] | pass | 0 failed / 3 checked | 0 failed |
| known_answer | pass | [true, true, true, true, true, true] | 0.75 |
| protected_paths_unchanged | pass | [] | ["src", "tools", "config", "metadata", "tests"] |

## Evaluability

| dataset | metric | ref people | full | LOO/jackknife | bootstrap | valid | test |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| cops | enmo_hourly_median_mean_g | 8 | True | True | True | distribution | available_locked |
| cops | walking_cadence_hourly_median_mean_hz | 8 | True | True | True | distribution | available_locked |
| cops | rest_tremor_window_fraction | 8 | True | True | True | distribution | available_locked |
| nhanes | enmo_hourly_median_mean_g | 8 | True | True | True | distribution | available_locked |
| nhanes | walking_cadence_hourly_median_mean_hz | 0 | False | False | False | distribution | available_locked |
| nhanes | rest_tremor_window_fraction | 8 | True | True | False | distribution | available_locked |

## Validation audit

- baseline SHA256: `8e059468fe37b05e7419fca392e5a5acfd89862e29abe2e422a203bda570f630` (หลัง audit: `8e059468fe37b05e7419fca392e5a5acfd89862e29abe2e422a203bda570f630`)
- fit ระหว่าง validation: False

| หมวด | status | checked | failed |
| --- | --- | ---: | ---: |
| baseline_lock | pass | 3 | 0 |
| hash_chain | pass | 9 | 0 |
| reference_formulas | pass | 54 | 0 |
| distances | pass | 4800 | 0 |
| null_handling | pass | 516 | 0 |
| previous_day | pass | 960 | 0 |
| coverage | pass | 2631 | 0 |
| official_reports | not_evaluable | 0 | 0 |
| test_lock | pass | 3 | 0 |

| กรณี null | exercised | violations | status |
| --- | ---: | ---: | --- |
| daily_insufficient_all_null | 26 | 0 | pass |
| reference_insufficient_null_distance | 70 | 0 | pass |
| zero_scale_distance_without_z | 72 | 0 | pass |
| ok_comparison_complete | 286 | 0 | pass |
| csv_null_not_zero | 26 | 0 | pass |
| tremor_zero_requires_windows | 36 | 0 | pass |

| previous_day | n | status |
| --- | ---: | --- |
| no_previous_day | 24 | exercised |
| previous_day_missing | 24 | exercised |
| current_day_insufficient_data | 26 | exercised |
| previous_day_insufficient_data | 2 | exercised |
| ok | 164 | exercised |

- เทียบ analysis ทางการ: {"exact_rows": 0, "roundoff_rows": 0, "mismatch_rows": 0, "max_relative_roundoff": 0.0}
- float parse (pandas default vs round_trip): {"n_values": 214, "n_not_round_trip": 116, "max_relative_difference": 3.2483761651326383e-15}

## ความพร้อมเปิด test (ต่อ cohort × ตัวชี้วัดที่ reference ok)

| dataset | metric | ready | เหตุผล |
| --- | --- | --- | --- |
| cops | enmo_hourly_median_mean_g | True | — |
| cops | walking_cadence_hourly_median_mean_hz | True | — |
| cops | rest_tremor_window_fraction | True | — |
| nhanes | enmo_hourly_median_mean_g | True | — |

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
