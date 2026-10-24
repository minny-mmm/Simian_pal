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

**Current modelling target (since 21 Oct 2026):** not a classifier. `src/reference` builds a *daily cohort-reference
baseline* — per-day distance of 3 indicators (ENMO, walking cadence, rest-tremor fraction) from separate COPS and
NHANES train references. Outputs must say "distance from the COPS/NHANES reference", never diagnosis/severity/
probability, and never a combined score. The hourly-tremor plan in `README_model_plan.md` is exploratory only.

Docs are in Thai: `README.md` (overview, clean stage, harmonize), `README_Download.md` (team S3 workflow),
`README_feature.md` (feature columns, training pitfalls), `README_reference_baseline.md` (reference baseline: formulas,
null rules, real runs, SageMaker prep — the most current status doc), `backtest/README.md`. README.md's statement that
feature code doesn't exist is outdated — see `src/features/run.py`.

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

# reference baseline (all write new run dirs; never overwrite)
python tools/extract_reference_features.py --dataset cops --ids COPS-8 [--config ...] [--clean-manifest-root ...]
python tools/reference_baseline.py daily|fit|compare ... # hourly->daily bundle, train-only fit, compare
python tools/run_real_reference.py --run-id <new> --stage inspect|train|validation|checkpoint|status
python tools/reference_demo.py --out reports/reference_baseline/demo-NEW   # synthetic only

# backtest (separate folder; reads project code/config read-only) — full sequence in backtest/RUNBOOK.md
python -X utf8 -m backtest.run --mode synthetic|real [--pull] [--tracks ...] --run-id <new>   # exit 1 gate fail, 2 test locked
python -X utf8 -m backtest.run --mode synthetic --synthetic-from <run> --config <unlock cfg> --tracks test --unlock-test ...
python -X utf8 -m backtest.aws_compare --aws-artifact <s3 uri|file> --aws-sha256 <sha>
python -X utf8 -m backtest.prepare_symptom                # Track B inputs, COPS train/valid only -> --symptom-snapshot
python -X utf8 -m backtest.prepare_test --config <unlock cfg> --unlock-test --approved-baseline-sha256 <sha> --dry-run
python -m pytest backtest/tests -q
```

On Windows use `python -X utf8` for anything that prints/writes Thai. The AWS CLI needs `--region ap-southeast-1`
explicitly (the profile's region is malformed); Python storage reads region from config.

`tests/test_storage.py` uses moto's in-process S3 server; it never touches real AWS. On Windows its 3 tests currently
error at setup (`ThreadedMotoServer` reports host `0.0.0.0`, which Windows can't connect to: WinError 10049 or
botocore `EndpointConnectionError`); the other tests pass. Real S3 access needs
`aws sso login --profile minny` (profile/bucket in `config/config.yaml` → `storage`).

## Architecture

Flow: raw (OSF/CDC → S3 `data/raw/`) → subject selection & splits (`tools/`, outputs committed to git) →
**clean** (`src/clean`) → `data/clean/<ds>/<id>.parquet` + `.qc.json` → **features** (`src/features/run.py`) →
`data/features/<ds>/<id>.csv` (1 row per hour) → **reference baseline** (`src/reference`, below).

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
- **Reference baseline** (`src/reference`, thresholds in `config/reference_baseline.yaml`): versioned hourly features
  (each CSV needs a `.manifest.json` from `tools/extract_reference_features.py`, backed by clean-run evidence;
  `provenance.py`) → `daily.py` aggregates 09–21 h hourly rows to 3 daily indicators with coverage/`insufficient_data`
  → `io.py` writes hashed bundles (`daily.csv`, `hour_qc.csv`, `bundle.manifest.json`, ...) → `model.py` fits
  subject-balanced weighted median/quantiles/MAD on **train only** (`fit_reference` → `baseline.json`, tamper-checked
  by `artifact_id`) and `compare_daily` (train rows are scored leave-one-subject-out) → `output.py` `build_analysis`
  adds QC + `previous_day` and validates against public JSON Schema `1.0.0`. Null is never 0; MAD=0 → `zero_scale`
  (distance kept, standardized null). `tools/run_real_reference.py` drives real runs into per-run namespaces
  (`data/{clean/reference-runs,features/reference-v1,daily/reference-v1,models/reference-v1}/<run>/`,
  `reports/reference_baseline/<run>/`). SageMaker files (`aws/reference/`, `tools/prepare_reference_aws.py`,
  `tools/sagemaker_fit_reference.py`) only prepare requests; nothing in the repo submits jobs.
- **Backtest** (`backtest/`): never edits `src/ tools/ config/ metadata/ tests/` (hash-checked each run); writes only
  `backtest/workspace|outputs` (gitignored). Real mode reads only the baseline whose SHA256 equals
  `real.approved_baseline_sha256` in `backtest/config/backtest_config.yaml`, never fits, keeps test locked; gates are
  pre-registered there. `aws_compare.py` compares an AWS artifact with the local one by content (file SHA and
  `artifact_id` always differ) and treats more train subjects as a new version that must be re-validated.
  Every validation run with `audit` appends to `outputs/baseline_registry.json` (synthetic: `synthetic_registry.json`)
  keyed by baseline SHA. **Test opens only if** `test.allow_test_split` + `--unlock-test` + approved SHA == file ==
  `locked_baseline_sha256` of `test_plan_NOT_EXECUTED.json` (written only by `run_real_reference --stage fit`, so
  checkpoint baselines can never open test) **and** the readiness gate passes: a registry run of that SHA with no failed
  gate, audit passed, same gates fingerprint, and for every (cohort, metric) whose reference is `ok` the jackknife and
  bootstrap gates `pass` (not `not_evaluable`) and valid has ≥ `min_valid_subjects_for_distribution` people
  (`ref.ready_cells` / `readiness_for_test`). Once per SHA: `outputs/_test_opened/<sha>.json` is written before any test
  row is read. The test bundle is built only by `backtest/prepare_test.py` (same locks; locked pipeline/reference
  config, noise lock and clean source must reproduce the baseline `preprocessing_version`) and is never pulled by
  `--pull`. Keep `allow_test_split: false` in the main config; unlock via a separate config copy with identical gates.
  The lock is per baseline SHA, but the test *people* are single-use: once baseline v1's test result (`e2a35a02…`,
  run `real-20261024-full-v2`) has been seen, never use it to tune/select later models, configs or thresholds (use
  train/valid/LOO/walk-forward); re-running that test on a new baseline must be reported as reused test, not held-out.
  `real.data_root` points real mode at another checkout (e.g. a run's worktree) before the repo/workspace.

## Gotchas

- Clean parquet row groups ≠ days (pyarrow cuts every 1,048,576 rows). Split by the `day` / `time_s` columns or read
  with `filters=[("day", "=", d)]`.
- NHANES dates are fake (year 2000); only day order is meaningful.
- Training rules (README_feature.md §5): split by subject via the `split` column (GroupKFold on `subject_id`); compute
  any statistic (scaling, imputation, thresholds, noise target) from train only; never fill feature NaN with 0;
  never use `subject_id`, `dataset`, `label`, `split`, `day`, `hour`, `hour_idx`, or window-count columns as inputs.
- `data/`, most of `metadata/` and `reports/`, `*.parquet`, `*.zip` are gitignored. Only `metadata/cops_splits.csv`,
  `metadata/nhanes_splits.csv`, `reports/cops_visit_summary.csv` are committed; data lives on S3. Exception: `results/`
  holds a checksummed snapshot of non-raw results (baseline v1, bundles, backtest outputs incl. the opened test; no
  parquet/raw). The repo is public; COPS is CC BY 4.0, so derived COPS files need attribution (see `results/README.md`).
- Comments and CLI help in `src/clean`, `src/storage.py`, `tools/` are Thai; `src/features/run.py`, `src/reference`,
  `tools/*reference*` and `backtest/` are English — match the file you're editing.
- Reference config/thresholds may only be tuned on train. `config_hash` includes `baseline_version`, so bundles from
  runs with different names don't mix even when the policy is identical; never mix preprocessing versions.
- `tools/run_real_reference.py` `initialize()` refuses to continue a run once git HEAD or clean source changes. Run long
  real runs from a dedicated `git worktree` so commits elsewhere don't break them; a run's clean evidence lives only on
  the machine that ran it (S3 has bundles/reports, not `data/clean/reference-runs/`). A `--stage checkpoint` taken
  before the full fit leaves `data/models/reference-v1/<run>/checkpoints/`, and `fit_bundle` then refuses the non-empty
  model dir: move `checkpoints/` aside (hash it), fit, move it back (see backtest/RUNBOOK.md).
- Legacy CSVs in S3 `data/features/` have no manifests: don't use them for the reference baseline (Track B only).
  `tools/audit_model_readiness.py` fetches QC/diaries of every split incl. test; use `backtest.prepare_symptom` instead.
  `data/features/nhanes/tests/` holds test-split files — don't pull or open test data until the test gate is unlocked.
- `load_bundle` reads CSVs with pandas' default float parser, which is not bit-exact round-trip (~1e-15 relative);
  fits and analyses are consistent because all read the same way, but a different pandas version can shift values.
