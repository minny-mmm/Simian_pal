# Backtest real-symptom-v1 (real)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **pass**
- Protected paths unchanged: True
- Created (UTC): 2026-10-24T08:57:00.880703+00:00

## Gates

| gate | status | value | threshold |
| --- | --- | --- | --- |
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

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
