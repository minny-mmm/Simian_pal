"""Build the locked TEST daily bundle for a full-train reference run. The only backtest code that opens test signal.

  python -X utf8 -m backtest.prepare_test --config backtest/config/backtest_config.test-unlock.yaml \
      --unlock-test --approved-baseline-sha256 <sha of the full-fit baseline.json> --dry-run
  python -X utf8 -m backtest.prepare_test --config ... --unlock-test --approved-baseline-sha256 <sha> [--ids ...] [--pull]

Every test lock must pass before any test file is touched: test.allow_test_split, --unlock-test, approved SHA ==
baseline file == test_plan locked_baseline_sha256, a recorded validation run of this baseline that passed every
pre-registered gate (reference_backtest.readiness_for_test), and test not yet compared. The run's locked
pipeline/reference config, NHANES noise lock and clean/extractor source must reproduce the baseline's
preprocessing_version exactly. Never fits, never compares; tools/run_real_reference.py stays untouched (it refuses
test by design). Writes only backtest/workspace/real/test/<run_id>/<sha12>/ and backtest/outputs/_test_prepared/.
"""
import argparse
import copy
import gc
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from src.reference.common import ROOT, fingerprint, load_roster, sha256, write_json

from backtest.runners import reference_backtest as ref
from backtest.runners.data_loader import DEFAULT_CONFIG, load_backtest_config, load_real, materialize

RUN_FILES = ['pipeline_config.yaml', 'reference_config.yaml', 'run_manifest.json', 'noise_lock.json', 'inventory.json']


def _now():
    return datetime.now(timezone.utc).isoformat()


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def expected_preprocessing_version(clean_config, code_version):
    """Mirrors the signature written by tools/extract_reference_features.extract (fails closed if it drifts)."""
    from src.features import run as extractor
    return fingerprint(dict(extractor_sha256=sha256(extractor.__file__), cfg=extractor.CFG, fs=extractor.FS,
                            window_s=extractor.WIN_S, clean_config=clean_config, clean_code_version=code_version))


def verify_run_files(files, plan, artifact, current_source):
    """Locked run inputs must be exactly those the baseline was fitted with; returns (pipeline, lock, manifest)."""
    missing = [k for k, v in files.items() if v is None]
    if missing:
        raise FileNotFoundError(f'Locked run files missing: {missing} (use --pull after `aws sso login --profile minny`)')
    if sha256(files['pipeline_config.yaml']) != plan.get('locked_pipeline_config_sha256'):
        raise ValueError('pipeline_config.yaml differs from locked_pipeline_config_sha256 in the test plan')
    if sha256(files['reference_config.yaml']) != plan.get('locked_reference_config_sha256'):
        raise ValueError('reference_config.yaml differs from locked_reference_config_sha256 in the test plan')
    pipeline = yaml.safe_load(Path(files['pipeline_config.yaml']).read_text(encoding='utf-8'))
    lock, manifest = _read(files['noise_lock.json']), _read(files['run_manifest.json'])
    if lock.get('pipeline_config_sha256') != sha256(files['pipeline_config.yaml']):
        raise ValueError('Noise lock was not made for this pipeline config')
    locked_source = manifest['source_version']
    if current_source['files'] != locked_source['files']:
        changed = sorted(k for k in locked_source['files'] if current_source['files'].get(k) != locked_source['files'][k])
        raise ValueError(f'Clean source differs from the locked run: {changed}; check out the run source, never mix preprocessing')
    if expected_preprocessing_version(pipeline['clean'], locked_source) != artifact['preprocessing_version']:
        raise ValueError('Current extractor/clean config would not reproduce the baseline preprocessing_version')
    return pipeline, lock, manifest


def locked_context(bcfg, unlock, approved_sha, pull=False, outputs_root=None, current_source=None):
    """All locks + locked run files. Nothing from the test split is read here (the plan holds names only)."""
    if bcfg['real'].get('checkpoint'):
        raise ref.TestLocked('Test is prepared only for a full-train fit baseline: set real.checkpoint: null')
    outputs_root = Path(outputs_root or ROOT / bcfg['paths']['outputs'])
    inputs = load_real(bcfg, pull)
    sha, plan, readiness = ref.check_test_locks(inputs, bcfg, outputs_root, unlock, approved_sha)
    report = f"reports/reference_baseline/{bcfg['real']['run_id']}"
    files = {name: materialize(f'{report}/{name}', bcfg['paths']['workspace'], pull) for name in RUN_FILES}
    if current_source is None:
        from tools.run_real_reference import source_version
        current_source = source_version()
    pipeline, lock, manifest = verify_run_files(files, plan, inputs['artifact'], current_source)
    for s in plan['subjects']:
        if s.get('split') != 'test':
            raise ValueError(f"Test plan lists a non-test subject: {s.get('subject_id')}")
    ws = Path(ROOT / bcfg['paths']['workspace']) / 'real' / 'test' / bcfg['real']['run_id'] / sha[:12]
    return dict(inputs=inputs, sha=sha, plan=plan, readiness=readiness, files=files, pipeline=pipeline, lock=lock,
                manifest=manifest, current_source=current_source, ws=ws, outputs_root=outputs_root,
                cfg=inputs['artifact']['config'])


def _download(fs, inventory, key, local):
    from src import storage
    proof = local.with_name(local.name + '.download.json')
    if not (local.exists() and proof.exists() and _read(proof).get('sha256') == sha256(local)):
        if local.exists():
            raise ValueError(f'Unverified existing cache: {local}')
        storage.download('s3://' + key, local)
        info = fs.info(key)
        if info.get('ETag') != inventory[key]['etag']:
            raise ValueError('Raw object changed since the run inventory; do not mix inputs')
        write_json(proof, dict(uri='s3://' + key, sha256=sha256(local), size=local.stat().st_size,
                               etag=info.get('ETag'), version_id=info.get('VersionId'), downloaded_utc=_now()))
    return dict(path=local.relative_to(ROOT).as_posix(), **_read(proof))


def clean_and_extract(ctx, subject, pipeline, roster):
    """Same steps and evidence record as tools/run_real_reference.clean_one, then extract; returns a status dict."""
    from src.clean import run as clean
    from tools.extract_reference_features import extract
    from tools.run_real_reference import dependency_versions, remote_files, resource_check, select_raw, source_version
    sid, ds, ws = subject['subject_id'], subject['dataset'], ctx['ws']
    dest, hourly = ws / 'clean' / ds, ws / 'hourly' / ds / f'{sid}.csv'
    pq, qcpath, manifest = dest / f'{sid}.parquet', dest / f'{sid}.qc.json', dest / f'{sid}.clean.manifest.json'
    if not manifest.exists():
        inventory = _read(ctx['files']['inventory.json'])['files']
        fs, root = remote_files(pipeline)
        keys = select_raw(inventory, root, subject)
        if not keys:
            return dict(status='excluded', reason='raw_or_required_diary_missing')
        inputs = []
        for key in keys:
            rel = key.split('/data/raw/cops/', 1)[1] if ds == 'cops' else 'pax80_raw/' + Path(key).name
            inputs.append(_download(fs, inventory, key, ws / 'raw' / ds / rel))
        inputs += [dict(path=r, sha256=sha256(ROOT / r), kind='split_or_cohort_metadata') for r in pipeline['clean']['subjects'].values()]
        resources = resource_check(pipeline, subject, inputs)
        target = np.asarray(ctx['lock']['target_axes_mg']) if ds == 'nhanes' else None
        start = _now()
        dest.mkdir(parents=True, exist_ok=True)
        write_json(dest / f'{sid}.started.json', dict(schema_version=1, kind='clean_run_started', run_id=ctx['manifest']['run_id'],
                                                      **subject, started_utc=start, resources=resources, inputs=inputs))
        result, qc = clean.clean_subject(pipeline, subject, clean.make_reader(pipeline, ds), target, log=lambda s: print(s, flush=True))
        if source_version() != ctx['current_source']:
            raise ValueError('Clean source changed during execution; outputs must not be certified')
        qc['noise_target_source'] = 'locked_agreed_config' if target is not None else None
        if result is not None:
            clean.write_parquet(pq, result)
        del result
        gc.collect()
        write_json(qcpath, qc)
        write_json(manifest, dict(
            schema_version=1, kind='clean_run_evidence', run_id=ctx['manifest']['run_id'], recorded_at_utc=_now(),
            evidence_uri=(dest / f'{sid}.started.json').as_uri(), **subject, status='completed', started_utc=start,
            resolved_clean_config=pipeline['clean'], resolved_config=pipeline,
            # Locked run source (file hashes verified identical); the commit actually executed is recorded beside it.
            code_version=ctx['manifest']['source_version'], executed_code_version=ctx['current_source'],
            dependencies=dependency_versions(), inputs=inputs, pipeline_config_sha256=sha256(ctx['files']['pipeline_config.yaml']),
            runtime_noise_target_axes_mg=None if target is None else target.tolist(),
            parquet_sha256=sha256(pq) if pq.exists() else None, qc_sha256=sha256(qcpath),
            included=qc.get('included', False), qc_status=qc['status'], test_prepare=True))
    record, qc = _read(manifest), _read(qcpath)
    if sha256(qcpath) != record['qc_sha256'] or (record.get('parquet_sha256') and sha256(pq) != record['parquet_sha256']):
        raise ValueError(f'{sid}: completed clean output changed')
    if qc.get('status') != 'ok' or not qc.get('included'):
        return dict(status='excluded', reason='clean_qc_excluded', valid_days=qc.get('valid_days'))
    if not hourly.exists():
        extract(ds, sid, pq, qcpath, ws / 'hourly', roster, ctx['cfg'], pipeline, clean_manifest=manifest)
    return dict(status='included')


def prepare(bcfg, unlock, approved_sha, ids=None, pull=False, dry_run=False, outputs_root=None, process=clean_and_extract,
            context=locked_context):
    ctx = context(bcfg, unlock, approved_sha, pull, outputs_root)
    ws, sha, subjects = ctx['ws'], ctx['sha'], ctx['plan']['subjects']
    bundle = ws / 'daily' / 'test'
    summary = dict(baseline_sha256=sha, validation_run=ctx['readiness']['validation_run'], n_plan_subjects=len(subjects),
                   workspace=str(ws), bundle=str(bundle))
    if dry_run:
        return dict(summary, status='dry_run_locks_passed')
    if bundle.exists():
        raise ValueError(f'Test bundle already prepared: {bundle}; never rebuild test inputs for the same baseline')
    if ids and not set(ids) <= {s['subject_id'] for s in subjects}:
        raise ValueError('IDs are not in the test plan')
    marker = Path(ctx['outputs_root']) / '_test_prepared' / f'{sha}.json'
    progress_path = ws / 'progress.json'
    progress = _read(progress_path)['rows'] if progress_path.exists() else {}
    write_json(marker, dict(summary, status='preparing', updated_utc=_now()))
    pipeline = copy.deepcopy(ctx['pipeline'])
    for ds in ['cops', 'nhanes']:  # raw is cached in the backtest workspace only; clean config itself is unchanged
        raw = ws / 'raw' / ds
        pipeline['datasets'][ds]['raw_dir'] = (raw.relative_to(ROOT) if raw.is_relative_to(ROOT) else raw).as_posix()
    roster = load_roster(pipeline=pipeline)
    for s in subjects:
        sid = s['subject_id']
        if (ids and sid not in ids) or progress.get(sid, {}).get('status') in ('included', 'excluded'):
            continue
        try:
            progress[sid] = dict(dataset=s['dataset'], **process(ctx, s, pipeline, roster))
        except MemoryError as exc:
            progress[sid] = dict(dataset=s['dataset'], status='deferred_memory', error=str(exc))
        except Exception as exc:  # recorded per subject; no bundle while any subject is unresolved
            progress[sid] = dict(dataset=s['dataset'], status='failed', error=f'{type(exc).__name__}: {exc}')
        write_json(progress_path, dict(updated_utc=_now(), rows=progress))
        print(_now(), sid, progress[sid]['status'], flush=True)
    pending = [s['subject_id'] for s in subjects if progress.get(s['subject_id'], {}).get('status') not in ('included', 'excluded')]
    excluded = [dict(subject_id=k, **v) for k, v in progress.items() if v['status'] == 'excluded']
    write_json(ws / 'test_exclusions.json', dict(data_kind='real', baseline_sha256=sha, excluded=excluded))
    if pending:
        write_json(marker, dict(summary, status='incomplete', pending=pending, updated_utc=_now()))
        return dict(summary, status='incomplete', pending=pending)
    if not any(v['status'] == 'included' for v in progress.values()):
        raise ValueError('No QC-included test subjects; no test bundle created')
    from src.reference.io import create_bundle
    create_bundle(ws / 'hourly', bundle, roster, ctx['cfg'], pipeline,
                  {name: sha256(ROOT / name) for name in pipeline['clean']['subjects'].values()})
    done = dict(summary, status='prepared', n_included=sum(v['status'] == 'included' for v in progress.values()),
                n_excluded=len(excluded), bundle_manifest_sha256=sha256(bundle / 'bundle.manifest.json'), updated_utc=_now())
    write_json(marker, done)
    return done


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--unlock-test', action='store_true')
    p.add_argument('--approved-baseline-sha256', required=True)
    p.add_argument('--ids', nargs='+', help='process only these planned test subjects (resume in batches)')
    p.add_argument('--pull', action='store_true', help='pull locked run files/baseline from S3 into the workspace')
    p.add_argument('--dry-run', action='store_true', help='check every lock and the locked run files only')
    a = p.parse_args(argv)
    bcfg = load_backtest_config(a.config)
    bcfg['real']['approved_baseline_sha256'] = a.approved_baseline_sha256
    try:
        result = prepare(bcfg, a.unlock_test, a.approved_baseline_sha256, a.ids, a.pull, a.dry_run)
    except ref.TestLocked as exc:
        print(f'LOCKED: {exc}')
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result['status'] == 'prepared':
        print(f"Next: backtest.run --mode real --config {a.config} --tracks test --unlock-test "
              f"--approved-baseline-sha256 {result['baseline_sha256']} --test-bundle {result['bundle']} --run-id <new>")
    return 0 if result['status'] in ('prepared', 'dry_run_locks_passed') else 1


if __name__ == '__main__':
    sys.exit(main())
