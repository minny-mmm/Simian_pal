# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Data pipeline that prepares wrist-accelerometer data to train a classifier separating Parkinson's (PD) patients from
healthy controls:
- **PD**: COPS (GENEActiv, 100 Hz), 49 subjects — split 35/7/7 in `metadata/cops_splits.csv`
- **Healthy**: NHANES 2013–2014 PAX80_H (ActiGraph, 80 Hz), 150 age/sex-matched — `metadata/nhanes_splits.csv`

**The central constraint:** label and watch brand overlap 100%, so a model can learn the device instead of the disease.
Every stage therefore uses the *same code and thresholds for both datasets*. The only dataset-specific parts are the
raw readers (`src/ingestion/`) and `clean.harmonize` in `config/config.yaml`. Don't introduce per-dataset branches
elsewhere; any new feature/step should be checked for device leakage.

Docs are in Thai: `README.md` (overview, clean stage, harmonize), `README_Download.md` (team S3 workflow),
`README_feature.md` (feature columns, training pitfalls). README.md's statement that feature code doesn't exist is
outdated — see `src/features/run.py`.

## Commands

Run everything from the repo root as modules (imports are `from src...`). Local venv is `venv/`
(Windows: `venv\Scripts\python`).

```bash
pip install -r requirements.txt
python -m pytest tests                                   # all tests
python -m pytest tests/test_clean.py -k <name>           # single test
python -m src.clean.run                                  # clean everyone with local raw data (skips done)
python -m src.clean.run --ids COPS-8 NHANES-74132 --force   # --summary-only, --upload also available
python -m src.features.run --dataset cops                # features; --ids ... --force
python tools/noise_target.py                             # check NHANES noise target vs COPS train (exit 1 = mismatch)
python tools/s3_pull.py list                             # what's on S3
python tools/s3_batch.py clean --dry-run                 # pull raw from S3 3 at a time -> clean -> upload
python -m src.pipeline --datasets cops                   # optional ingestion path (COPS only)
```

`tests/test_storage.py` uses moto's in-process S3 server; it never touches real AWS. On Windows its 3 tests currently
error at setup (`ThreadedMotoServer` reports host `0.0.0.0`, which Windows can't connect to: WinError 10049);
the other tests pass. Real S3 access needs
`aws sso login --profile minny` (profile/bucket in `config/config.yaml` → `storage`).

## Architecture

Flow: raw (OSF/CDC → S3 `data/raw/`) → subject selection & splits (`tools/`, outputs committed to git) →
**clean** (`src/clean`) → `data/clean/<ds>/<id>.parquet` + `.qc.json` → **features** (`src/features/run.py`) →
`data/features/<ds>/<id>.csv` (1 row per hour) → model (not started).

- **Readers** (`src/ingestion/`): subclasses of `DatasetReader` (`base.py`), registered in `READERS` in
  `src/ingestion/__init__.py`. The clean stage uses only `has_raw`, `iter_hours` (yields `(t, xyz)` per hourly file,
  t = seconds since 00:00 of day 1), `flag_intervals`, `hour_labels`. COPS is read directly from zips, NHANES streamed
  from `<SEQN>.tar.bz2` — never extract. `src/pipeline.py` (common schema + `metadata/manifest.csv`) is a separate
  side path that clean does **not** use and that doesn't support NHANES.
- **Storage** (`src/storage.py`): `storage.path()` returns a `Path` for local or `UPath` for `s3://`, so readers work
  unchanged against S3. S3 layout mirrors the local repo layout 1:1 under `storage.s3_root`.
  Call `storage.configure(config["storage"])` once at startup.
- **Clean** (`src/clean`): `steps.py` holds pure, I/O-free step functions (time fix, van Hees calibration, resample to
  50 Hz, clip, abnormal, ISM, noise harmonize, nonwear, HDCZA sleep, valid days); `run.py` orders them per subject,
  hour by hour. Samples are **flagged, never deleted**: output has `valid` + `reason` (`|`-joined flags). All thresholds
  live in `config.yaml` → `clean`; change config, not code. New/changed steps go in `steps.py` + order in `run.py` +
  tests in `tests/test_clean.py`.
- **Harmonize**: NHANES gets per-axis Gaussian noise up to `clean.harmonize.add_noise.target_mg` (median COPS-train
  noise floor), then both datasets are quantized to 3.9 mg. Seeded by `seed + crc32(subject_id)`. Changing
  `target_mg` means re-cleaning all NHANES; re-run `tools/noise_target.py` after any COPS re-clean. Clean COPS before
  NHANES. `s3_batch.py clean` treats NHANES results with a stale noise target as needing re-clean.
- **Features** (`src/features/run.py`): valid rows only → complete 10 s windows (500 samples, no stitching across gaps)
  → 09:00–21:00 only → classify windows tremor/walk/rest → aggregate per (day, hour). Thresholds are in its `CFG`
  dict (mostly untuned). Amplitude-dependent columns use `AMPLITUDE_PREFIXES` and are the most brand-sensitive.
  Inter-axis correlation features were removed deliberately (device fingerprint).

## Gotchas

- Clean parquet row groups ≠ days (pyarrow cuts every 1,048,576 rows). Split by the `day` / `time_s` columns or read
  with `filters=[("day", "=", d)]`.
- NHANES dates are fake (year 2000); only day order is meaningful.
- Training rules (README_feature.md §5): split by subject via the `split` column (GroupKFold on `subject_id`); compute
  any statistic (scaling, imputation, thresholds, noise target) from train only; never fill feature NaN with 0;
  never use `subject_id`, `dataset`, `label`, `split`, `day`, `hour`, `hour_idx`, or window-count columns as inputs.
- `data/`, most of `metadata/` and `reports/`, `*.parquet`, `*.zip` are gitignored. Only `metadata/cops_splits.csv`,
  `metadata/nhanes_splits.csv`, `reports/cops_visit_summary.csv` are committed; data lives on S3.
- Comments and CLI help in `src/clean`, `src/storage.py`, `tools/` are Thai; `src/features/run.py` is English —
  match the file you're editing.
