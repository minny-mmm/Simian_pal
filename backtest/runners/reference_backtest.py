"""Track A: daily cohort-reference baseline backtests built only on src.reference functions.

Reference statistics are recomputed with model.summarize() on the fitted artifact's own train entries, so
real mode never refits (fit_reference needs clean-run evidence) and the config on disk never changes.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from src.reference.common import fingerprint, sha256, write_json
from src.reference.daily import METRICS
from src.reference.io import load_bundle
from src.reference.model import summarize
from src.reference.output import build_analysis

from backtest.evaluators.metrics import robust_summary

COHORTS = ['cops', 'nhanes']
CAVEAT = ('Distances from the COPS/NHANES cohort references only, not diagnosis, severity or probability. '
          'Cohort = device brand for 100% of subjects, so any COPS-vs-NHANES difference may be the device.')


def _entries(artifact, ds, metric):
    return artifact['references'][ds][metric]['entries']


def _policy(artifact):
    return artifact['config']['reference_policy']


def _eligible_ids(entries, policy):
    counts = pd.Series([e['subject_id'] for e in entries], dtype=object).value_counts()
    return sorted(counts[counts >= policy['min_days_per_participant']].index)


# ---------------------------------------------------------------------------
# Evaluability (first output of every run)
# ---------------------------------------------------------------------------
def evaluability(inputs, bcfg):
    artifact, policy = inputs['artifact'], _policy(inputs['artifact'])
    min_valid = bcfg['evaluability']['min_valid_subjects_for_distribution']
    rows = []
    for ds in COHORTS:
        for metric in METRICS:
            entries = _entries(artifact, ds, metric)
            ids = _eligible_ids(entries, policy)
            full = summarize(entries, policy)
            # Leave-one-out is evaluable only if dropping ANY single subject keeps the reference valid.
            loo_status = [summarize([e for e in entries if e['subject_id'] != s], policy)['status'] for s in ids]
            loo_ok = bool(ids) and all(s != 'insufficient_data' for s in loo_status)
            valid = inputs['valid_daily']
            n_valid = (int(valid[valid.dataset.eq(ds) & valid.metric.eq(metric) & valid.status.eq('ok')].subject_id.nunique())
                       if valid is not None else 0)
            rows.append(dict(dataset=ds, metric=metric, n_reference_participants=len(ids),
                             n_reference_days=full['n_days'], reference_status=full['status'],
                             full_reference_evaluable=full['status'] != 'insufficient_data',
                             loo_jackknife_evaluable=loo_ok,
                             n_loo_insufficient=sum(s == 'insufficient_data' for s in loo_status),
                             bootstrap_evaluable=full['status'] == 'ok',
                             n_valid_subjects_ok=n_valid,
                             valid_level='distribution' if n_valid >= min_valid else 'descriptive_only' if n_valid else 'unavailable',
                             test_level='available_locked' if inputs['test_bundle'] else 'blocked_no_test_features'))
    return rows


# ---------------------------------------------------------------------------
# Level 1: reference stability on train (jackknife, bootstrap, sensitivity)
# ---------------------------------------------------------------------------
def jackknife(artifact):
    policy, rows = _policy(artifact), []
    for ds in COHORTS:
        for metric in METRICS:
            entries = _entries(artifact, ds, metric)
            base = summarize(entries, policy)
            for left_out in _eligible_ids(entries, policy):
                s = summarize([e for e in entries if e['subject_id'] != left_out], policy)
                shift = None
                if base['status'] == 'ok' and s['median'] is not None:
                    shift = abs(s['median'] - base['median']) / base['scale']
                rows.append(dict(dataset=ds, metric=metric, left_out=left_out, status=s['status'],
                                 n_participants=s['n_participants'], median=s['median'], scale=s['scale'],
                                 full_median=base['median'], full_scale=base['scale'],
                                 median_shift_in_full_scale=shift))
    return rows


def bootstrap(artifact, n, seed):
    """Resample whole subjects with replacement; duplicates get new IDs so each copy keeps weight 1/N."""
    policy, rng, rows = _policy(artifact), np.random.default_rng(seed), []
    for ds in COHORTS:
        for metric in METRICS:
            entries = _entries(artifact, ds, metric)
            base = summarize(entries, policy)
            ids = _eligible_ids(entries, policy)
            by_id = {i: [e for e in entries if e['subject_id'] == i] for i in ids}
            medians, insufficient = [], 0
            for k in range(n if ids else 0):
                sample = []
                for j, sid in enumerate(rng.choice(ids, len(ids), replace=True)):
                    sample += [dict(e, subject_id=f'{sid}#b{k}.{j}') for e in by_id[sid]]
                s = summarize(sample, policy)
                if s['median'] is None:
                    insufficient += 1
                else:
                    medians.append(s['median'])
            low = high = width = None
            if medians and base['status'] == 'ok':
                low, high = float(np.quantile(medians, .025)), float(np.quantile(medians, .975))
                width = (high - low) / base['scale']
            rows.append(dict(dataset=ds, metric=metric, n_bootstrap=n, n_insufficient=insufficient,
                             full_median=base['median'], full_scale=base['scale'], median_ci_low=low,
                             median_ci_high=high, ci_width_in_full_scale=width, status=base['status']))
    return rows


def sensitivity(artifact, min_participants_values):
    """Policy override in memory only, passed straight to summarize(); never written to config."""
    rows = []
    for value in min_participants_values:
        policy = dict(copy.deepcopy(_policy(artifact)), min_participants=int(value))
        for ds in COHORTS:
            for metric in METRICS:
                entries = _entries(artifact, ds, metric)
                ids = _eligible_ids(entries, policy)
                full = summarize(entries, policy)
                loo = [summarize([e for e in entries if e['subject_id'] != s], policy)['status'] for s in ids]
                rows.append(dict(label='sensitivity_only_not_production', min_participants=int(value),
                                 dataset=ds, metric=metric, status=full['status'], median=full['median'],
                                 scale=full['scale'], n_participants=full['n_participants'],
                                 loo_insufficient=sum(s == 'insufficient_data' for s in loo), loo_total=len(loo)))
    return rows


def deviation_spread(daily, artifact):
    """How much each query day's standardized deviation moves when one OTHER reference subject is dropped."""
    if daily is None or daily.empty:
        return []
    policy, memo, rows = _policy(artifact), {}, []

    def summary(ds, metric, excluded):
        key = (ds, metric, excluded)
        if key not in memo:
            memo[key] = summarize([e for e in _entries(artifact, ds, metric) if e['subject_id'] not in excluded], policy)
        return memo[key]

    for r in daily[daily.status.eq('ok')].to_dict('records'):
        own = frozenset([r['subject_id']]) if r['split'] == 'train' else frozenset()
        for ds in COHORTS:
            base = summary(ds, r['metric'], own)
            if base['status'] != 'ok':
                continue
            zs = []
            for other in _eligible_ids(_entries(artifact, ds, r['metric']), policy):
                if other in own:
                    continue
                s = summary(ds, r['metric'], own | {other})
                if s['status'] == 'ok':
                    zs.append((r['value'] - s['median']) / s['scale'])
            z = (r['value'] - base['median']) / base['scale']
            rows.append(dict(subject_id=r['subject_id'], dataset=r['dataset'], split=r['split'], day=r['day'],
                             metric=r['metric'], reference=ds, standardized_deviation=z,
                             jackknife_min=min(zs) if zs else None, jackknife_max=max(zs) if zs else None,
                             jackknife_range=(max(zs) - min(zs)) if zs else None, n_jackknife=len(zs)))
    return rows


# ---------------------------------------------------------------------------
# Analysis documents (train LOO, valid) through the project's own build_analysis/validate_output
# ---------------------------------------------------------------------------
def analysis_rows(document):
    rows = []
    for r in document['analysis']:
        for ref, c in r['reference_comparisons'].items():
            rows.append(dict(subject_id=r['subject_id'], dataset=r['dataset'], split=r['split'], day=r['day'],
                             metric=r['metric'], value=r['value'], status=r['status'], reference=ref,
                             comparison_status=c['status'], signed_distance=c['signed_distance'],
                             standardized_deviation=c['standardized_deviation'],
                             excluded_train_subject=c['excluded_train_subject'],
                             previous_day_status=r['previous_day']['status'], previous_day_delta=r['previous_day']['delta'],
                             measurement_state=r['qc']['measurement_state']))
    return rows


def distribution(rows):
    f = pd.DataFrame(rows)
    out = []
    if f.empty:
        return out
    for (qds, ref, metric), g in f.groupby(['dataset', 'reference', 'metric']):
        z = g.standardized_deviation.dropna()
        per_subject = g.dropna(subset=['standardized_deviation']).groupby('subject_id').standardized_deviation.median()
        out.append(dict(query_dataset=qds, reference=ref, metric=metric, n_subjects=int(g.subject_id.nunique()),
                        n_metric_days=len(g), n_comparable_days=len(z),
                        comparison_status_counts=g.comparison_status.value_counts().to_dict(),
                        day_level=robust_summary(z.tolist()), subject_median_level=robust_summary(per_subject.tolist())))
    return out


def run_analysis(daily, artifact, baseline_path):
    before = sha256(baseline_path)
    document = build_analysis(daily, artifact)  # raises if the shared schema/semantic checks fail
    after = sha256(baseline_path)
    return document, dict(baseline_sha256_before=before, baseline_sha256_after=after, unchanged=before == after,
                          schema_validation='passed', n_rows=len(document['analysis']))


def device_confound(artifact, valid_rows, signal_types, flag_ratio):
    """Cohort gap per metric in pooled reference-scale units. Diagnostic only: never an accuracy/AUC."""
    gaps = []
    valid = pd.DataFrame(valid_rows)
    for metric in METRICS:
        s = {ds: artifact['references'][ds][metric]['summary'] for ds in COHORTS}
        ok = all(s[ds]['status'] == 'ok' for ds in COHORTS)
        pooled = float(np.mean([s[ds]['scale'] for ds in COHORTS])) if ok else None
        ref_gap = abs(s['cops']['median'] - s['nhanes']['median']) / pooled if ok else None
        valid_gap = None
        if ok and not valid.empty:
            v = valid[valid.metric.eq(metric) & valid.status.eq('ok')].drop_duplicates(['subject_id', 'day'])
            med = {ds: v[v.dataset.eq(ds)].groupby('subject_id').value.median() for ds in COHORTS}
            if all(len(med[ds]) for ds in COHORTS):
                valid_gap = abs(float(med['cops'].median()) - float(med['nhanes'].median())) / pooled
        gaps.append(dict(metric=metric, signal_type=signal_types.get(metric, 'unknown'),
                         reference_gap_in_pooled_scale=ref_gap, valid_gap_in_pooled_scale=valid_gap,
                         status='ok' if ok else 'not_evaluable'))
    flags = []
    for key in ['reference_gap_in_pooled_scale', 'valid_gap_in_pooled_scale']:
        amp = [g[key] for g in gaps if g['signal_type'] == 'amplitude' and g[key] is not None]
        other = [g[key] for g in gaps if g['signal_type'] != 'amplitude' and g[key] is not None]
        if amp and other and max(other) > 0 and max(amp) >= flag_ratio * max(other):
            flags.append(f'{key}: amplitude metric separates cohorts >= {flag_ratio}x more than non-amplitude '
                         'metrics -> possible device (brand/gain) effect; do not attribute the gap to disease')
    return dict(caveat=CAVEAT, gaps=gaps, flags=flags)


def known_answer(valid_rows, truth, tolerance):
    """Synthetic only: planted shifts must be recovered as standardized deviation vs own-cohort reference."""
    f = pd.DataFrame(valid_rows)
    out = []
    for t in (truth or {}).get('shifted', []):
        g = f[f.subject_id.eq(t['subject_id']) & f.metric.eq(t['metric']) & f.reference.eq(t['dataset'])]
        z = g.standardized_deviation.dropna()
        recovered = float(z.median()) if len(z) else None
        # The planted shift must appear as +sd_units on top of the same subject's unshifted deviation.
        increment = recovered - t['unshifted_median_sd'] if recovered is not None else None
        out.append(dict(**{k: t[k] for k in ['dataset', 'subject_id', 'metric', 'sd_units', 'unshifted_median_sd']},
                        recovered_median_sd=recovered, recovered_increment_sd=increment, n_days=len(z),
                        passed=bool(increment is not None and abs(increment - t['sd_units']) <= tolerance)))
    for t in (truth or {}).get('zero_scale', []):
        g = f[f.reference.eq(t['dataset']) & f.metric.eq(t['metric']) & f.status.eq('ok')]
        out.append(dict(dataset=t['dataset'], metric=t['metric'], check='zero_scale_has_null_standardization',
                        passed=bool(len(g) and g.comparison_status.eq('zero_scale').all()
                                     and g.standardized_deviation.isna().all() and g.signed_distance.notna().all())))
    for t in (truth or {}).get('no_walking', []):
        g = f[f.subject_id.eq(t['subject_id']) & f.day.eq(t['day']) & f.metric.eq('walking_cadence_hourly_median_mean_hz')]
        out.append(dict(subject_id=t['subject_id'], day=t['day'], check='no_walking_windows_is_null',
                        passed=bool(len(g) and g.measurement_state.eq('no_walking_windows').all() and g.value.isna().all())))
    if truth and truth.get('missing_day') is not None:
        g = f[f.day.eq(truth['missing_day'] + 1)]
        out.append(dict(day=truth['missing_day'] + 1, check='day_after_missing_day_is_previous_day_missing',
                        passed=bool(len(g) and g.previous_day_status.eq('previous_day_missing').all())))
    return out


# ---------------------------------------------------------------------------
# Level 4: locked test hold-out
# ---------------------------------------------------------------------------
class TestLocked(RuntimeError):
    pass


class TestNotReady(TestLocked):
    def __init__(self, readiness):
        super().__init__(f"Baseline not ready for test: {readiness['reasons']}")
        self.readiness = readiness


def registry_name(mode):
    # Synthetic fixtures never share the registry of real baselines.
    return 'baseline_registry.json' if mode == 'real' else 'synthetic_registry.json'


def ready_cells(evaluability, gates):
    """Per (cohort, metric) with an ok reference: stability gates passed (not merely not_evaluable) and
    enough valid people for a distribution. Cells whose reference is insufficient/zero_scale cannot be compared."""
    status = {g['gate']: g['status'] for g in gates}
    out = []
    for e in evaluability:
        if e['reference_status'] != 'ok':
            continue
        key = f"{e['dataset']}/{e['metric']}"
        reasons = [f"{name}={status.get(f'{name}[{key}]', 'missing')}"
                   for name in ['jackknife_median_shift', 'bootstrap_median_ci_width'] if status.get(f'{name}[{key}]') != 'pass']
        if e['valid_level'] != 'distribution':
            reasons.append(f"valid_level={e['valid_level']}")
        out.append(dict(dataset=e['dataset'], metric=e['metric'], ready=not reasons, reasons=reasons))
    return out


def readiness_for_test(outputs_root, mode, baseline_sha, gates_fingerprint):
    """Test may open only after a recorded validation run of THIS baseline file passed under the same gates."""
    path = Path(outputs_root) / registry_name(mode)
    runs = (json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}).get(baseline_sha, {}).get('validation_runs', [])
    rejected = []
    for r in runs:
        why = []
        if r.get('overall') not in ('pass', 'pass_with_unevaluable'):
            why.append(f"overall={r.get('overall')}")
        # Synthetic fixtures have no official checkpoint reports, so only that audit category may be not_evaluable there.
        audit = r.get('audit_statuses') or {}
        synthetic_ok = (mode == 'synthetic' and bool(audit) and r.get('valid_rows')
                        and all(s == 'pass' or (k == 'official_reports' and s == 'not_evaluable') for k, s in audit.items()))
        if not (r.get('audit_pass') and r.get('valid_rows')) and not synthetic_ok:
            why.append('validation_audit_not_passed')
        if r.get('gates_fingerprint') != gates_fingerprint:
            why.append('gates_fingerprint_differs_or_missing')
        cells = r.get('ready_cells') or []
        if not cells:
            why.append('no_comparable_cells_recorded')
        why += [f"{c['dataset']}/{c['metric']}: {', '.join(c['reasons'])}" for c in cells if not c['ready']]
        if not why:
            return dict(ready=True, validation_run=r['run_id'], reasons=[])
        rejected.append(dict(run_id=r.get('run_id'), reasons=why))
    return dict(ready=False, validation_run=None,
                reasons=rejected or [f'no validation run recorded for {baseline_sha} in {path.name}'])


def check_test_bundle(daily, cfg, artifact, roster, plan_subjects=None):
    """The test bundle must be built with the locked baseline config and contain only planned test subjects."""
    if fingerprint(cfg) != artifact['config_hash'] or not daily.config_hash.eq(artifact['config_hash']).all():
        raise ValueError('Test bundle config_hash differs from the baseline; rebuild it with the locked config')
    if not daily.preprocessing_version.eq(artifact['preprocessing_version']).all():
        raise ValueError('Test bundle preprocessing_version differs from the baseline')
    if not daily.split.eq('test').all():
        raise ValueError('Test bundle contains non-test rows')
    ids = set(daily.subject_id)
    if not ids <= set(roster[roster.split.eq('test')].subject_id):
        raise ValueError('Test bundle has subjects outside the roster test split')
    if plan_subjects is not None and not ids <= {s['subject_id'] for s in plan_subjects}:
        raise ValueError('Test bundle has subjects outside test_plan_NOT_EXECUTED.json')


def check_test_locks(inputs, bcfg, outputs_root, unlock, approved_sha):
    """Every lock except the bundle itself; shared by run_test and prepare_test. Returns (sha, plan or None)."""
    if not (bcfg['test']['allow_test_split'] and unlock):
        raise TestLocked('Test split locked: needs test.allow_test_split=true AND --unlock-test')
    actual = sha256(inputs['baseline_path'])
    if not approved_sha or approved_sha != actual:
        raise TestLocked('Approved baseline SHA256 missing or does not match the baseline file')
    plan = None
    if inputs['test_plan'] is not None:
        plan = json.loads(Path(inputs['test_plan']).read_text(encoding='utf-8'))
        if plan.get('locked_baseline_sha256') != actual:
            raise TestLocked('Baseline differs from locked_baseline_sha256 in test_plan_NOT_EXECUTED.json')
    elif inputs['mode'] == 'real':
        raise TestLocked('Real test requires the test_plan_NOT_EXECUTED.json written by a full-train fit '
                         '(checkpoint baselines have none and cannot open test)')
    readiness = readiness_for_test(outputs_root, inputs['mode'], actual, fingerprint(bcfg['gates']))
    if not readiness['ready']:
        raise TestNotReady(readiness)
    marker = Path(outputs_root) / '_test_opened' / f'{actual}.json'
    if marker.exists():
        raise TestLocked(f'Test already opened for this baseline: {marker}')
    return actual, plan, readiness


def run_test(inputs, bcfg, outputs_root, unlock=False, approved_sha=None):
    """Compare-only on test, once per baseline. Requires config allow_test_split, --unlock-test, the SHA and a
    recorded validation run of this baseline that passed every pre-registered gate."""
    actual, plan, readiness = check_test_locks(inputs, bcfg, outputs_root, unlock, approved_sha)
    if inputs['test_bundle'] is None:
        return dict(status='blocked_no_test_features', baseline_sha256=actual, readiness=readiness), None
    marker = Path(outputs_root) / '_test_opened' / f'{actual}.json'
    # Written BEFORE any test row is read: a failed attempt still counts as opened.
    opened = dict(baseline_sha256=actual, opened_utc=datetime.now(timezone.utc).isoformat(), fit=False,
                  status='opening', validation_run=readiness['validation_run'], test_bundle=str(inputs['test_bundle']))
    write_json(marker, opened)
    daily, _, cfg, _ = load_bundle(inputs['test_bundle'])
    check_test_bundle(daily, cfg, inputs['artifact'], inputs['roster'], plan.get('subjects') if plan else None)
    document, check = run_analysis(daily, inputs['artifact'], inputs['baseline_path'])
    write_json(marker, dict(opened, status='compared_once', n_rows=check['n_rows']))
    return dict(status='compared_once', readiness=readiness, **check), document
