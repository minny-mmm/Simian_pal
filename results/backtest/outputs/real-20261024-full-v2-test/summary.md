# Backtest real-20261024-full-v2-test (real)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **pass**
- Protected paths unchanged: True
- Created (UTC): 2026-10-24T16:16:19.878449+00:00

## Gates

| gate | status | value | threshold |
| --- | --- | --- | --- |
| schema_and_semantic_validation | pass | 303 | build_analysis/validate_output |
| baseline_unchanged | pass | True | True |
| protected_paths_unchanged | pass | [] | ["src", "tools", "config", "metadata", "tests"] |

## Evaluability

| dataset | metric | ref people | full | LOO/jackknife | bootstrap | valid | test |
| --- | --- | ---: | --- | --- | --- | --- | --- |
| cops | enmo_hourly_median_mean_g | 31 | True | True | True | distribution | available_locked |
| cops | walking_cadence_hourly_median_mean_hz | 24 | True | True | True | distribution | available_locked |
| cops | rest_tremor_window_fraction | 31 | True | True | True | distribution | available_locked |
| nhanes | enmo_hourly_median_mean_g | 58 | True | True | True | distribution | available_locked |
| nhanes | walking_cadence_hourly_median_mean_hz | 50 | True | True | True | distribution | available_locked |
| nhanes | rest_tremor_window_fraction | 58 | True | True | True | distribution | available_locked |

## Test hold-out

- status: **compared_once**

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
