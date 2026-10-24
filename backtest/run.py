"""Backtest CLI for the daily cohort-reference baseline (+ exploratory symptom CV).

Run from the repo root:
  python -X utf8 -m backtest.run --mode synthetic
  python -X utf8 -m backtest.run --mode real --pull                 # needs `aws sso login --profile minny`
  python -X utf8 -m backtest.run --mode real --config <unlock config> --tracks test --unlock-test --approved-baseline-sha256 <sha>
  python -X utf8 -m backtest.run --mode synthetic --synthetic-from <earlier run> --config <unlock config> --tracks test --unlock-test ...
  python -X utf8 -m backtest.run --mode real --baseline <other baseline.json> --approved-baseline-sha256 <its sha>

Reads project code/config/splits read-only; writes only backtest/workspace and backtest/outputs/<run_id>.
"""
import argparse
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.reference.common import ROOT, code_version, fingerprint, load_roster, sha256, write_json

from backtest.evaluators.metrics import robust_summary, snapshot_diff, tree_snapshot
from backtest.evaluators.report_builder import update_registry, write_report
from backtest.runners import reference_backtest as ref
from backtest.runners import symptom_cv_backtest as symptom
from backtest.runners import temporal_walkforward as walk
from backtest.runners import validation_audit
from backtest.runners.data_loader import BACKTEST_ROOT, DEFAULT_CONFIG, load_backtest_config, load_inputs

TRACKS = ['stability', 'loo', 'walkforward', 'valid', 'audit', 'symptom', 'test']
DEFAULT_TRACKS = [t for t in TRACKS if t != 'test']  # test only when asked for explicitly


def _now():
    return datetime.now(timezone.utc)


def _gate(name, value, threshold, ok):
    return dict(gate=name, value=value, threshold=threshold,
                status='not_evaluable' if ok is None else 'pass' if ok else 'fail')


def _backtest_code():
    return {p.relative_to(ROOT).as_posix(): sha256(p) for p in sorted(BACKTEST_ROOT.rglob('*'))
            if p.suffix in {'.py', '.yaml'} and not {'workspace', 'outputs', '__pycache__'} & set(p.parts)}


def _stability(inputs, bcfg, evaluable):
    s = bcfg['stability']
    jack = ref.jackknife(inputs['artifact'])
    jf = pd.DataFrame(jack)
    summary = []
    for e in evaluable:
        g = jf[jf.dataset.eq(e['dataset']) & jf.metric.eq(e['metric'])] if len(jf) else jf
        shifts = g.median_shift_in_full_scale.dropna() if len(g) else []
        summary.append(dict(dataset=e['dataset'], metric=e['metric'], n_left_out=len(g),
                            n_insufficient=int(g.status.eq('insufficient_data').sum()) if len(g) else 0,
                            max_median_shift_in_full_scale=float(max(shifts)) if len(shifts) else None,
                            evaluable=e['loo_jackknife_evaluable'] and e['reference_status'] == 'ok'))
    spread = []
    for split in ['train', 'valid']:
        spread += ref.deviation_spread(inputs[f'{split}_daily'], inputs['artifact'])
    sf = pd.DataFrame(spread)
    spread_summary = ([dict(split=k[0], metric=k[1], reference=k[2], jackknife_range=robust_summary(g.jackknife_range.dropna().tolist()))
                       for k, g in sf.groupby(['split', 'metric', 'reference'])] if len(sf) else [])
    return dict(jackknife=jack, jackknife_summary=summary,
                bootstrap=ref.bootstrap(inputs['artifact'], s['n_bootstrap'], s['seed']),
                sensitivity=ref.sensitivity(inputs['artifact'], s['sensitivity_min_participants']),
                deviation_spread=spread, deviation_spread_summary=spread_summary)


def _analysis(inputs, split):
    daily = inputs[f'{split}_daily']
    if daily is None or daily.empty:
        return dict(status='unavailable')
    document, check = ref.run_analysis(daily, inputs['artifact'], inputs['baseline_path'])
    rows = ref.analysis_rows(document)
    return dict(status='ok', check=check, rows=rows, distribution=ref.distribution(rows), document=document)


def _walkforward(inputs, bcfg, analyses):
    out = dict(no_lookahead={}, first_usable=[], within_subject=[])
    for split in bcfg['walkforward']['splits']:
        daily = inputs[f'{split}_daily']
        if daily is None:
            out['no_lookahead'][split] = dict(n_checked=0, n_mismatches=None, status='unavailable')
            continue
        out['no_lookahead'][split] = walk.no_lookahead(daily, inputs['artifact'])
        if split in inputs['hourly']:
            out['no_lookahead'][f'{split}_hourly'] = walk.no_lookahead_hourly(
                inputs['hourly'][split], inputs['roster'], inputs['cfg'], inputs['provenance'], inputs['days'])
        out['first_usable'] += walk.time_to_first_usable(daily)
        out['within_subject'] += walk.within_subject_stability(analyses[split].get('rows', []))
    out['stability_summary'] = walk.summarize_stability(out['within_subject'])
    return out


def _symptom(inputs, bcfg):
    scfg = bcfg['symptom']
    if inputs['mode'] == 'synthetic':
        return symptom.run_symptom(symptom.synthetic_symptom_table(scfg['seed']), scfg, 'synthetic fixture')
    table, note = symptom.load_real_symptom_table(scfg, load_roster())
    return symptom.run_symptom(table, scfg, note)


def _gates(results, inputs, bcfg, baseline_start):
    g, t, thr = [], results['tracks'], bcfg['gates']
    for e in t.get('stability', {}).get('jackknife_summary', []):
        ok = e['max_median_shift_in_full_scale'] <= thr['jackknife_median_shift_max_scale'] if e['evaluable'] else None
        g.append(_gate(f"jackknife_median_shift[{e['dataset']}/{e['metric']}]", e['max_median_shift_in_full_scale'],
                       thr['jackknife_median_shift_max_scale'], ok))
    for b in t.get('stability', {}).get('bootstrap', []):
        w = b['ci_width_in_full_scale']
        g.append(_gate(f"bootstrap_median_ci_width[{b['dataset']}/{b['metric']}]", w,
                       thr['bootstrap_median_ci_width_max_scale'], None if w is None else w <= thr['bootstrap_median_ci_width_max_scale']))
    for name, v in t.get('walkforward', {}).get('no_lookahead', {}).items():
        n = v.get('n_mismatches')
        g.append(_gate(f'no_lookahead[{name}]', n, thr['no_lookahead_mismatches_max'],
                       None if n is None or not v['n_checked'] else n <= thr['no_lookahead_mismatches_max']))
    checks = [t[k]['check'] for k in ['loo', 'valid'] if t.get(k, {}).get('status') == 'ok']
    if isinstance(t.get('test'), dict) and 'unchanged' in t['test']:
        checks.append(t['test'])
    for c in checks:
        g.append(_gate('schema_and_semantic_validation', c['n_rows'], 'build_analysis/validate_output', True))
    unchanged = all(c['unchanged'] for c in checks) and sha256(inputs['baseline_path']) == baseline_start
    g.append(_gate('baseline_unchanged', unchanged, thr['baseline_unchanged'], unchanged))
    for name, c in t.get('audit', {}).get('categories', {}).items():
        g.append(dict(gate=f'audit[{name}]', value=f"{c['n_failed']} failed / {c['n_checked']} checked",
                      threshold='0 failed', status=c['status']))
    known = t.get('valid', {}).get('known_answer')
    if inputs['mode'] == 'synthetic' and known is not None:
        g.append(_gate('known_answer', [k['passed'] for k in known], thr['known_answer_tolerance_sd'],
                       bool(known) and all(k['passed'] for k in known)))
    return g


def run(mode, run_id=None, tracks=None, config_path=DEFAULT_CONFIG, overrides=None, pull=False,
        unlock_test=False, approved_sha=None, outputs_root=None, workspace_root=None, baseline=None,
        train_bundle=None, valid_bundle=None, synthetic_from=None, test_bundle=None):
    bcfg = load_backtest_config(config_path, overrides)
    if synthetic_from and mode != 'synthetic':
        raise ValueError('--synthetic-from is only valid with --mode synthetic')
    if baseline is not None:
        # Validating another artifact (e.g. AWS): checkpoint reports/train bundle do not describe it.
        bcfg['real'].update(baseline=str(Path(baseline).resolve()), report_dir=None, fit_report=None,
                            train_bundle=str(Path(train_bundle).resolve()) if train_bundle else None)
    if valid_bundle is not None:
        bcfg['real']['valid_bundle'] = str(Path(valid_bundle).resolve())
    if test_bundle is not None:
        bcfg['real']['test_bundle'] = str(Path(test_bundle).resolve())
    if approved_sha:
        bcfg['real']['approved_baseline_sha256'] = approved_sha
    tracks = list(tracks or DEFAULT_TRACKS)
    if set(tracks) - set(TRACKS):
        raise ValueError(f'Unknown tracks: {sorted(set(tracks) - set(TRACKS))}')
    run_id = run_id or f"{mode}-{_now():%Y%m%dT%H%M%SZ}"
    if any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
        raise ValueError('Run ID must be a simple new namespace')
    outputs_root = Path(outputs_root or ROOT / bcfg['paths']['outputs'])
    workspace_root = Path(workspace_root or ROOT / bcfg['paths']['workspace'])
    bcfg['paths']['workspace'] = str(workspace_root)
    out = outputs_root / run_id
    if out.exists():
        raise ValueError(f'Output exists: {out}; never overwrite a backtest run')
    protected_before = tree_snapshot(ROOT, bcfg['paths']['protected'])
    manifest = dict(run_id=run_id, mode=mode, tracks=tracks, status='started', created_utc=_now().isoformat(),
                    config_path=str(config_path), backtest_config_fingerprint=fingerprint(bcfg),
                    gates=bcfg['gates'], gates_fingerprint=fingerprint(bcfg['gates']), synthetic_from=synthetic_from,
                    code_version=code_version(), backtest_code=_backtest_code(),
                    runtime=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__,
                                 pyyaml=yaml.__version__))
    # Gates are registered on disk before any valid/test data is opened.
    write_json(out / 'run_manifest.json', manifest)
    try:
        inputs = load_inputs(mode, bcfg, workspace_root / 'synthetic' / run_id, pull,
                             synthetic_from=workspace_root / 'synthetic' / synthetic_from if synthetic_from else None)
        baseline_start = sha256(inputs['baseline_path'])
        manifest.update(inputs=inputs['sources'], unavailable=inputs['unavailable'])
        write_json(out / 'run_manifest.json', manifest)
        evaluable = ref.evaluability(inputs, bcfg)
        results = dict(run_id=run_id, mode=mode, created_utc=manifest['created_utc'], caveat=ref.CAVEAT,
                       data_kind=inputs['artifact']['data_kind'], baseline_sha256=baseline_start,
                       unavailable=inputs['unavailable'], evaluability=evaluable, tracks={})
        t = results['tracks']
        analyses = {s: _analysis(inputs, s) for s in ['train', 'valid'] if {'loo', 'valid', 'walkforward', 'audit'} & set(tracks)}
        if 'stability' in tracks:
            t['stability'] = _stability(inputs, bcfg, evaluable)
        if 'loo' in tracks:
            t['loo'] = {k: v for k, v in analyses['train'].items() if k != 'document'}
        if 'valid' in tracks:
            v = analyses['valid']
            t['valid'] = {k: x for k, x in v.items() if k != 'document'}
            t['valid']['device_confound'] = ref.device_confound(inputs['artifact'], v.get('rows', []),
                                                                bcfg['metric_signal_type'], bcfg['gates']['device_flag_ratio'])
            if inputs['truth']:
                t['valid']['known_answer'] = ref.known_answer(v.get('rows', []), inputs['truth'],
                                                              bcfg['gates']['known_answer_tolerance_sd'])
            if v.get('document'):
                write_json(out / f"analysis_VALID_{mode.upper()}.json", v['document'])
        if 'audit' in tracks:
            t['audit'] = validation_audit.run_audit(inputs, analyses['valid'].get('document'), bcfg, tracks,
                                                    bcfg['real'].get('approved_baseline_sha256') if mode == 'real' else inputs['approved_sha'])
            t['audit']['category_table'] = [dict(category=k, **{f: v[f] for f in ['status', 'n_checked', 'n_failed']})
                                             for k, v in t['audit']['categories'].items()]
            write_json(out / 'validation_audit.json', t['audit'])
        if 'walkforward' in tracks:
            t['walkforward'] = _walkforward(inputs, bcfg, analyses)
        if 'symptom' in tracks:
            t['symptom'] = _symptom(inputs, bcfg)
        if 'test' in tracks:
            try:
                status, document = ref.run_test(inputs, bcfg, outputs_root, unlock_test, approved_sha)
                t['test'] = status
                if document:
                    rows = ref.analysis_rows(document)
                    t['test'].update(distribution=ref.distribution(rows))
                    write_json(out / f"analysis_TEST_{mode.upper()}.json", document)
            except ref.TestNotReady as exc:
                t['test'] = dict(status='locked_not_ready', reason=str(exc), readiness=exc.readiness)
            except ref.TestLocked as exc:
                t['test'] = dict(status='locked', reason=str(exc))
        results['gates'] = _gates(results, inputs, bcfg, baseline_start)
        changed = snapshot_diff(protected_before, tree_snapshot(ROOT, bcfg['paths']['protected']))
        results['protected_paths_unchanged'] = not changed
        results['gates'].append(_gate('protected_paths_unchanged', changed[:20], bcfg['paths']['protected'], not changed))
        statuses = {g['status'] for g in results['gates']}
        results['overall'] = 'fail' if 'fail' in statuses else 'pass_with_unevaluable' if 'not_evaluable' in statuses else 'pass'
        if {'stability', 'valid'} <= set(tracks):
            results['test_ready_cells'] = ref.ready_cells(evaluable, results['gates'])
        write_report(out, results)
        if 'audit' in t:
            # Validation evidence per baseline SHA; the test track reads it back (ref.readiness_for_test).
            audit_pass = all(c['status'] == 'pass' for c in t['audit']['categories'].values())
            update_registry(outputs_root, baseline_start, name=ref.registry_name(mode),
                            source=bcfg['real'].get('source', 'local') if mode == 'real' else 'synthetic',
                            artifact_id=inputs['artifact']['artifact_id'],
                            baseline_version=inputs['artifact'].get('baseline_version'),
                            validation_run=dict(run_id=run_id, overall=results['overall'], audit_pass=audit_pass,
                                                valid_rows=t.get('valid', {}).get('check', {}).get('n_rows'),
                                                audit_statuses={k: v['status'] for k, v in t['audit']['categories'].items()},
                                                gates_fingerprint=manifest['gates_fingerprint'], tracks=tracks,
                                                ready_cells=results.get('test_ready_cells'),
                                                finished_utc=_now().isoformat()))
        manifest.update(status='completed', overall=results['overall'], finished_utc=_now().isoformat())
        write_json(out / 'run_manifest.json', manifest)
        return results, out
    except Exception as exc:
        manifest.update(status='failed', error=f'{type(exc).__name__}: {exc}', finished_utc=_now().isoformat())
        write_json(out / 'run_manifest.json', manifest)
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--mode', choices=['synthetic', 'real'], required=True)
    p.add_argument('--run-id', help='new output namespace (default: <mode>-<UTC timestamp>)')
    p.add_argument('--tracks', nargs='+', choices=TRACKS, default=DEFAULT_TRACKS)
    p.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    p.add_argument('--pull', action='store_true', help='real mode: pull missing inputs from S3 into backtest/workspace')
    p.add_argument('--unlock-test', action='store_true')
    p.add_argument('--approved-baseline-sha256', help='SHA256 of the baseline file to validate/unlock against')
    p.add_argument('--baseline', type=Path, help='real mode: validate this baseline.json instead of the configured one')
    p.add_argument('--train-bundle', type=Path, help='with --baseline: the train bundle it was fitted on (hash chain)')
    p.add_argument('--valid-bundle', type=Path, help='real mode: valid bundle to compare (default from config)')
    p.add_argument('--test-bundle', type=Path, help='real mode: test bundle built by backtest.prepare_test (opened only by the test track)')
    p.add_argument('--synthetic-from', help='synthetic mode: reuse the fixture (same baseline file) of an earlier run ID')
    a = p.parse_args(argv)
    if a.baseline and not a.approved_baseline_sha256:
        p.error('--baseline requires --approved-baseline-sha256')
    results, out = run(a.mode, a.run_id, a.tracks, a.config, pull=a.pull, unlock_test=a.unlock_test,
                       approved_sha=a.approved_baseline_sha256, baseline=a.baseline, train_bundle=a.train_bundle,
                       valid_bundle=a.valid_bundle, synthetic_from=a.synthetic_from,
                       test_bundle=a.test_bundle)
    failed = [g['gate'] for g in results['gates'] if g['status'] == 'fail']
    print(f"[{results['data_kind'].upper()}] overall={results['overall']} -> {out}")
    for g in failed:
        print(f'  FAIL {g}')
    test = results['tracks'].get('test')
    if isinstance(test, dict):
        print(f"  test: {test['status']}" + (f" ({test['reason']})" if test.get('reason') else ''))
    if failed:
        return 1
    return 2 if isinstance(test, dict) and test['status'].startswith('locked') else 0


if __name__ == '__main__':
    sys.exit(main())
