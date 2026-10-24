# Backtest synthetic-demo-C (synthetic)

> Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.

- Overall: **pass**
- Protected paths unchanged: True
- Created (UTC): 2026-10-24T08:32:41.720723+00:00

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

- status: **locked**
- Test already opened for this baseline: D:\AWS\Simianpal\backtest\outputs\_test_opened\8e059468fe37b05e7419fca392e5a5acfd89862e29abe2e422a203bda570f630.json

ตารางเต็มอยู่ในไฟล์ CSV และ results.json ในโฟลเดอร์เดียวกัน; dashboard.html เปิดได้แบบ offline
