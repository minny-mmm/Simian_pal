# Backtest synthetic-flow-B (synthetic)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **pass**
- Protected paths unchanged: True
- Created (UTC): 2026-10-24T08:28:07.888344+00:00

## Gates

| gate | status | value | threshold |
| --- | --- | --- | --- |
| baseline_unchanged | pass | True | True |
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

## Test hold-out

- status: **locked_not_ready**
- {"run_id": "synthetic-flow-A", "reasons": ["overall=fail", "cops/walking_cadence_hourly_median_mean_hz: bootstrap_median_ci_width=fail", "cops/rest_tremor_window_fraction: bootstrap_median_ci_width=fail"]}
- Baseline not ready for test: [{'run_id': 'synthetic-flow-A', 'reasons': ['overall=fail', 'cops/walking_cadence_hourly_median_mean_hz: bootstrap_median_ci_width=fail', 'cops/rest_tremor_window_fraction: bootstrap_median_ci_width=fail']}]

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
