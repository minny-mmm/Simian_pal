"""Independent audit of a locked baseline on valid data: hash chain, reference formulas, distances,
null handling, previous-day rules, coverage, and agreement with the official reports.

The arithmetic here is re-implemented (exact fractions for weights) and does NOT call
src.reference.model.summarize/compare_daily; project code is used only to load inputs and to build
the analysis document under audit. Nothing is fitted and the test split is never read.
"""
import json
import math
from fractions import Fraction
from pathlib import Path

import numpy as np
import pandas as pd

from src.reference.common import clean_json, fingerprint, sha256
from src.reference.daily import METRICS
from src.reference.io import load_bundle

COHORTS = ['cops', 'nhanes']
MAX_EXAMPLES = 20


class Check:
    """Collects pass/fail per category with a few concrete failing rows."""

    def __init__(self):
        self.categories = {}

    def cat(self, name):
        return self.categories.setdefault(name, dict(n_checked=0, n_failed=0, examples=[], notes=[]))

    def expect(self, name, ok, **detail):
        c = self.cat(name)
        c['n_checked'] += 1
        if not ok:
            c['n_failed'] += 1
            if len(c['examples']) < MAX_EXAMPLES:
                c['examples'].append(clean_json(detail))

    def note(self, name, text):
        self.cat(name)['notes'].append(text)

    def result(self):
        out = {}
        for name, c in self.categories.items():
            status = 'fail' if c['n_failed'] else 'pass' if c['n_checked'] else 'not_evaluable'
            out[name] = dict(status=status, **c)
        return out


def _none(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def _eq(a, b, rel=0.):
    if _none(a) or _none(b):
        return _none(a) and _none(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return a == b if rel == 0 else math.isclose(a, b, rel_tol=rel, abs_tol=0)
    return a == b


# ---------------------------------------------------------------------------
# Exact re-implementation of the reference summary
# ---------------------------------------------------------------------------
def exact_quantile(pairs, q):
    """pairs = [(value, Fraction weight)] in source order; smallest value with cumulative weight >= q*total."""
    ordered = sorted(pairs, key=lambda p: p[0])  # stable: ties keep (subject_id, day) source order
    target = Fraction(str(q)) * sum(w for _, w in ordered)
    cumulative = Fraction(0)
    for value, w in ordered:
        cumulative += w
        if cumulative >= target:
            return value
    return ordered[-1][0]


def exact_summary(entries, policy):
    entries = sorted(entries, key=lambda e: (e['subject_id'], e['day']))
    counts = pd.Series([e['subject_id'] for e in entries], dtype=object).value_counts().to_dict()
    kept = [e for e in entries if counts[e['subject_id']] >= policy['min_days_per_participant']]
    ids = sorted({e['subject_id'] for e in kept})
    days = {i: sum(e['subject_id'] == i for e in kept) for i in ids}
    out = dict(n_participants=len(ids), n_days=len(kept), subject_ids=ids, days_per_subject=days,
               status='insufficient_data', median=None, mad=None, scale=None, quantiles={})
    if len(ids) < policy['min_participants'] or len(kept) < policy['min_days']:
        return out
    n = len(ids)
    pairs = [(float(e['value']), Fraction(1, n * days[e['subject_id']])) for e in kept]
    median = exact_quantile(pairs, .5)
    mad = exact_quantile([(abs(v - median), w) for v, w in pairs], .5)
    scale = float(policy['mad_scale_factor'] * mad)
    out.update(status='ok' if scale > 0 else 'zero_scale', median=median, mad=mad, scale=scale,
               quantiles={str(q): exact_quantile(pairs, q) for q in policy['quantiles']})
    return out


# ---------------------------------------------------------------------------
# Audit sections
# ---------------------------------------------------------------------------
def audit_lock(chk, inputs, approved_sha):
    actual = sha256(inputs['baseline_path'])
    chk.expect('baseline_lock', approved_sha is None or actual == approved_sha, approved=approved_sha, actual=actual)
    if approved_sha is None:
        chk.note('baseline_lock', 'no approved SHA configured (synthetic run): lock recorded, not enforced')
    chk.expect('baseline_lock', inputs.get('fit_called_during_validation') is False,
               fit_called_during_validation=inputs.get('fit_called_during_validation'))
    return actual


def audit_hash_chain(chk, artifact, train_daily):
    chk.expect('hash_chain', artifact['config_hash'] == fingerprint(artifact['config']), field='config_hash')
    chk.expect('hash_chain', artifact['roster_hash'] == fingerprint(artifact['roster']), field='roster_hash')
    if train_daily is None:
        chk.note('hash_chain', 'train bundle unavailable: training_daily_hash and entries not checked')
        return
    train = train_daily[train_daily.split.eq('train')]
    actual = fingerprint(clean_json(train.sort_values(['dataset', 'subject_id', 'day', 'metric']).to_dict('records')))
    chk.expect('hash_chain', artifact['training_daily_hash'] == actual, field='training_daily_hash',
               artifact=artifact['training_daily_hash'], recomputed=actual)
    for ds in COHORTS:
        for metric in METRICS:
            ok_rows = train[train.dataset.eq(ds) & train.metric.eq(metric) & train.status.eq('ok')]
            expected = clean_json(ok_rows.sort_values(['subject_id', 'day'])[['subject_id', 'day', 'value']].to_dict('records'))
            chk.expect('hash_chain', artifact['references'][ds][metric]['entries'] == expected,
                       field='entries', dataset=ds, metric=metric,
                       n_artifact=len(artifact['references'][ds][metric]['entries']), n_train_ok=len(expected))


def audit_formulas(chk, artifact):
    policy = artifact['config']['reference_policy']
    recomputed = {}
    for ds in COHORTS:
        for metric in METRICS:
            mine = exact_summary(artifact['references'][ds][metric]['entries'], policy)
            theirs = artifact['references'][ds][metric]['summary']
            recomputed[(ds, metric)] = mine
            for key in ['status', 'n_participants', 'n_days', 'subject_ids', 'median', 'mad']:
                chk.expect('reference_formulas', _eq(mine[key], theirs[key]), dataset=ds, metric=metric, field=key,
                           artifact=theirs[key], recomputed=mine[key])
            chk.expect('reference_formulas', _eq(mine['scale'], theirs['scale'], rel=1e-15), dataset=ds, metric=metric,
                       field='scale', artifact=theirs['scale'], recomputed=mine['scale'])
            chk.expect('reference_formulas', {str(k): v for k, v in theirs['days_per_subject'].items()} == mine['days_per_subject'],
                       dataset=ds, metric=metric, field='days_per_subject')
            chk.expect('reference_formulas', theirs['quantiles'] == mine['quantiles'], dataset=ds, metric=metric,
                       field='quantiles', artifact=theirs['quantiles'], recomputed=mine['quantiles'])
    return recomputed


def audit_distances(chk, analysis, recomputed):
    for r in analysis['analysis']:
        for ds in COHORTS:
            c, s = r['reference_comparisons'][ds], recomputed[(ds, r['metric'])]
            where = dict(subject_id=r['subject_id'], day=r['day'], metric=r['metric'], reference=ds)
            expected_status = r['status'] if r['status'] != 'ok' else s['status']
            signed = absolute = z = None
            if r['status'] == 'ok' and s['status'] in ('ok', 'zero_scale'):
                signed = r['value'] - s['median']
                absolute = abs(signed)
                z = signed / s['scale'] if s['scale'] > 0 else None
            for field, want in [('status', expected_status), ('signed_distance', signed), ('absolute_distance', absolute),
                                ('standardized_deviation', z), ('reference_median', s['median']),
                                ('reference_mad', s['mad']), ('n_participants', s['n_participants']),
                                ('n_days', s['n_days'])]:
                chk.expect('distances', _eq(c[field], want), field=field, report=c[field], recomputed=want, **where)
            chk.expect('distances', _eq(c['reference_scale'], s['scale'], rel=1e-15), field='reference_scale', **where)
            chk.expect('distances', r['split'] == 'train' or c['excluded_train_subject'] is None,
                       field='excluded_train_subject', value=c['excluded_train_subject'], **where)


def audit_nulls(chk, analysis, daily):
    """Each null rule is counted separately so unexercised cases are visible."""
    cases = {k: dict(n_exercised=0, n_violations=0) for k in
             ['daily_insufficient_all_null', 'reference_insufficient_null_distance', 'zero_scale_distance_without_z',
              'ok_comparison_complete', 'csv_null_not_zero', 'tremor_zero_requires_windows']}

    def hit(case, ok, **detail):
        cases[case]['n_exercised'] += 1
        cases[case]['n_violations'] += not ok
        chk.expect('null_handling', ok, case=case, **detail)

    lookup = {(d['dataset'], d['subject_id'], d['day'], d['metric']): d for d in daily.to_dict('records')}
    for r in analysis['analysis']:
        key = (r['dataset'], r['subject_id'], r['day'], r['metric'])
        raw = lookup[key]
        where = dict(subject_id=r['subject_id'], day=r['day'], metric=r['metric'])
        comps = r['reference_comparisons'].values()
        if r['status'] != 'ok':
            hit('daily_insufficient_all_null', r['value'] is None and bool(r['qc']['reasons']) and all(
                c['signed_distance'] is None and c['absolute_distance'] is None and c['standardized_deviation'] is None
                and c['status'] == 'insufficient_data' for c in comps), **where)
        else:
            for c in comps:
                if c['status'] == 'insufficient_data':
                    hit('reference_insufficient_null_distance', c['signed_distance'] is None and c['standardized_deviation'] is None, **where)
                elif c['status'] == 'zero_scale':
                    hit('zero_scale_distance_without_z', c['signed_distance'] is not None and c['standardized_deviation'] is None, **where)
                else:
                    hit('ok_comparison_complete', None not in (c['signed_distance'], c['absolute_distance'], c['standardized_deviation']), **where)
        if _none(raw['value']):
            hit('csv_null_not_zero', r['value'] is None, **where)
        if r['metric'] == 'rest_tremor_window_fraction' and r['value'] == 0:
            hit('tremor_zero_requires_windows', raw['metric_windows'] > 0, **where)
    for case, c in cases.items():
        c['status'] = 'not_exercised' if not c['n_exercised'] else 'fail' if c['n_violations'] else 'pass'
    return cases


def expected_previous_day(r, lookup):
    """The five ordered rules of src/reference/output.py build_analysis."""
    day = r['day']
    prior = lookup.get((r['dataset'], r['subject_id'], day - 1, r['metric']))
    if day == 0:
        status = 'no_previous_day'
    elif prior is None or not prior['observed_hour_rows']:
        status = 'previous_day_missing'
    elif r['status'] != 'ok':
        status = 'current_day_insufficient_data'
    elif prior['status'] != 'ok':
        status = 'previous_day_insufficient_data'
    else:
        status = 'ok'
    value = None if prior is None or _none(prior['value']) else float(prior['value'])
    return dict(day=day - 1 if day > 0 else None, value=value, status=status,
                delta=r['value'] - value if status == 'ok' else None)


def audit_previous_day(chk, analysis, daily):
    lookup = {(d['dataset'], d['subject_id'], d['day'], d['metric']): d for d in daily.to_dict('records')}
    counts = {s: 0 for s in ['no_previous_day', 'previous_day_missing', 'current_day_insufficient_data',
                             'previous_day_insufficient_data', 'ok']}
    for r in analysis['analysis']:
        want, got = expected_previous_day(r, lookup), r['previous_day']
        counts[want['status']] += 1
        for field in ['status', 'day', 'value', 'delta']:
            chk.expect('previous_day', _eq(got[field], want[field]), field=field, report=got[field], expected=want[field],
                       subject_id=r['subject_id'], day=r['day'], metric=r['metric'])
    return {k: dict(n=v, status='exercised' if v else 'not_exercised') for k, v in counts.items()}


def expected_reasons(row, cfg):
    p = cfg['feature_policy']
    req = p.get('metric_coverage', {}).get(row['metric'], dict(min_minutes=p['min_metric_minutes'], min_hours=p['min_metric_hours']))
    reasons = []
    if not (row['day_eligible_hours'] >= p['min_day_hours'] and row['day_valid_minutes'] >= p['min_day_valid_minutes']):
        reasons.append('day_coverage_below_minimum')
    if row['metric_eligible_hours'] == 0:
        reasons.append('no_eligible_windows' if row['available_metric_windows'] == 0 else 'metric_values_missing')
    if row['metric_minutes'] < req['min_minutes'] or row['metric_eligible_hours'] < req['min_hours']:
        reasons.append('metric_coverage_below_minimum')
    return reasons


def audit_coverage(chk, daily, hour_qc, cfg, expected_days, coverage_report):
    p = cfg['feature_policy']
    h0, h1 = p['clock_hours']
    for h in hour_qc.to_dict('records'):
        clock = h0 <= h['hour'] < h1
        included = clock and h['valid_min'] >= p['min_hour_valid_minutes']
        reason = '' if included else 'outside_reporting_clock' if not clock else 'hour_coverage_below_minimum'
        chk.expect('coverage', bool(h['included']) == included and (str(h['reason']) if not _none(h['reason']) else '') == reason,
                   check='hour_included_rule', subject_id=h['subject_id'], day=h['day'], hour=h['hour'])
    keys = ['dataset', 'subject_id', 'day']
    inc = hour_qc[hour_qc.included.astype(bool)]
    minutes = inc.groupby(keys).valid_min.sum()
    hours = inc.groupby(keys).size()
    observed = hour_qc.groupby(keys).size()
    for r in daily.to_dict('records'):
        k = (r['dataset'], r['subject_id'], r['day'])
        where = dict(subject_id=r['subject_id'], day=r['day'], metric=r['metric'])
        chk.expect('coverage', math.isclose(r['day_valid_minutes'], float(minutes.get(k, 0.)), rel_tol=1e-12, abs_tol=1e-9),
                   check='day_valid_minutes', report=r['day_valid_minutes'], recomputed=float(minutes.get(k, 0.)), **where)
        chk.expect('coverage', r['day_eligible_hours'] == int(hours.get(k, 0)), check='day_eligible_hours', **where)
        chk.expect('coverage', r['observed_hour_rows'] == int(observed.get(k, 0)), check='observed_hour_rows', **where)
        chk.expect('coverage', math.isclose(r['metric_minutes'], r['metric_windows'] * p['window_seconds'] / 60, rel_tol=1e-12, abs_tol=1e-12),
                   check='metric_minutes', **where)
        chk.expect('coverage', r['available_metric_windows'] == r['metric_windows'] + r['missing_metric_windows'],
                   check='window_identity', **where)
        want = expected_reasons(r, cfg)
        got = [] if _none(r['reasons']) or r['reasons'] == '' else str(r['reasons']).split('|')
        chk.expect('coverage', got == want and (r['status'] == 'ok') == (not want), check='status_reasons',
                   report=dict(status=r['status'], reasons=got), expected=want, **where)
        chk.expect('coverage', r['status'] != 'ok' or not _none(r['value']), check='ok_has_value', **where)
    actual_days = {(r['dataset'], r['subject_id'], int(r['day'])): set() for r in daily.to_dict('records')}
    for r in daily.to_dict('records'):
        actual_days[(r['dataset'], r['subject_id'], int(r['day']))].add(r['metric'])
    if expected_days is None:
        chk.note('coverage', 'no qc_valid_days available: expected-day completeness not checked')
    else:
        chk.expect('coverage', set(actual_days) == set(expected_days), check='expected_qc_days_one_to_one',
                   missing=sorted(set(expected_days) - set(actual_days)), unexpected=sorted(set(actual_days) - set(expected_days)))
        for k, metrics in actual_days.items():
            chk.expect('coverage', metrics == set(METRICS), check='three_metrics_per_day', key=list(k))
    if coverage_report is not None:
        policy = cfg['reference_policy']
        recount = {}
        for (ds, split, metric), g in daily.groupby(['dataset', 'split', 'metric']):
            usable = g[g.status.eq('ok')]
            per = usable.groupby('subject_id').size()
            eligible = per[per >= policy['min_days_per_participant']]
            recount[(ds, split, metric)] = dict(n_people_observed=int(g.subject_id.nunique()), n_days_observed=len(g),
                n_people_daily_usable=int(usable.subject_id.nunique()), n_days_daily_usable=len(usable),
                n_people_meeting_reference_day_minimum=len(eligible), n_days_meeting_reference_day_minimum=int(eligible.sum()))
        for row in coverage_report['by_cohort_split_metric']:
            mine = recount.get((row['dataset'], row['split'], row['metric']))
            chk.expect('coverage', mine is not None and all(row[k] == v for k, v in mine.items()), check='coverage_report',
                       dataset=row['dataset'], metric=row['metric'], report={k: row[k] for k in (mine or {})}, recomputed=mine)


def _strip_rows(document):
    return {(r['dataset'], r['subject_id'], r['day'], r['metric']): r for r in document['analysis']}


def _diff_values(a, b, path=''):
    """Yield (path, a, b) for differing leaves."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in set(a) | set(b):
            yield from _diff_values(a.get(k), b.get(k), f'{path}.{k}' if path else k)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _diff_values(x, y, f'{path}[{i}]')
    elif a != b:
        yield path, a, b


def audit_official(chk, analysis, daily, official, artifact, baseline_sha):
    """Agreement with the reports written by the original run (exact, or float round-off <= 1e-12 relative)."""
    summary = dict(exact_rows=0, roundoff_rows=0, mismatch_rows=0, max_relative_roundoff=0.)
    doc = official.get('analysis_VALIDATION_REAL.json')
    if doc is None:
        chk.note('official_reports', 'official analysis not available')
    else:
        meta = {k: v for k, v in analysis.items() if k != 'analysis'}
        chk.expect('official_reports', meta == {k: v for k, v in doc.items() if k != 'analysis'}, check='metadata')
        mine, theirs = _strip_rows(analysis), _strip_rows(doc)
        chk.expect('official_reports', set(mine) == set(theirs), check='row_keys',
                   missing=sorted(set(theirs) - set(mine))[:5], extra=sorted(set(mine) - set(theirs))[:5])
        for key in set(mine) & set(theirs):
            diffs = list(_diff_values(mine[key], theirs[key]))
            numeric = [(p, a, b) for p, a, b in diffs if isinstance(a, float) and isinstance(b, float)]
            other = [d for d in diffs if d not in numeric]
            rel = max((abs(a - b) / max(abs(a), abs(b)) for _, a, b in numeric), default=0.)
            ok = not other and rel <= 1e-12
            summary['exact_rows' if not diffs else 'roundoff_rows' if ok else 'mismatch_rows'] += 1
            summary['max_relative_roundoff'] = max(summary['max_relative_roundoff'], rel if ok else 0.)
            chk.expect('official_reports', ok, check='analysis_row', key=list(key), diffs=[list(d) for d in diffs[:5]])
        if summary['roundoff_rows']:
            chk.note('official_reports', 'float values differ only at round-off level: the official run used in-memory '
                     'daily values; this audit reads daily.csv back (see float_parse_check)')
    report = official.get('validation_report.json')
    if report is not None:
        chk.expect('official_reports', report.get('baseline_sha256') == baseline_sha, check='validation_report.baseline_sha256')
        chk.expect('official_reports', report.get('test_evaluated') is False, check='validation_report.test_evaluated')
        chk.expect('official_reports', report.get('rows') == len(analysis['analysis']), check='validation_report.rows')
        counts = pd.Series([r['status'] for r in analysis['analysis']]).value_counts().to_dict()
        chk.expect('official_reports', report.get('status_counts') == counts, check='validation_report.status_counts',
                   report=report.get('status_counts'), recomputed=counts)
        for row in report.get('daily_coverage', []):
            g = daily[daily.dataset.eq(row['dataset']) & daily.metric.eq(row['metric'])]
            u = g[g.status.eq('ok')]
            mine = dict(n_people=int(g.subject_id.nunique()), n_days=len(g), n_people_usable=int(u.subject_id.nunique()), n_days_usable=len(u))
            chk.expect('official_reports', all(row[k] == v for k, v in mine.items()), check='validation_report.daily_coverage',
                       dataset=row['dataset'], metric=row['metric'], report=row, recomputed=mine)
        for row in report.get('comparison_status_counts', []):  # [{reference, metric, status_counts}]
            mine = pd.Series([r['reference_comparisons'][row['reference']]['status'] for r in analysis['analysis']
                              if r['metric'] == row['metric']]).value_counts().to_dict()
            chk.expect('official_reports', row['status_counts'] == mine, check='validation_report.comparison_status_counts',
                       reference=row['reference'], metric=row['metric'], report=row['status_counts'], recomputed=mine)
    fit = official.get('fit_report.json')
    if fit is not None:
        chk.expect('official_reports', fit.get('artifact_id') == artifact['artifact_id'], check='fit_report.artifact_id')
        for ds in COHORTS:
            for metric in METRICS:
                chk.expect('official_reports', fit['references'][ds][metric] == artifact['references'][ds][metric]['summary'],
                           check='fit_report.summary', dataset=ds, metric=metric)
    exclusions = official.get('valid_exclusions.json')
    if exclusions is not None:
        excluded = {(e['dataset'], e['subject_id']) for e in exclusions.get('excluded', [])}
        present = set(zip(daily.dataset, daily.subject_id))
        chk.expect('official_reports', not excluded & present, check='valid_exclusions_not_in_daily',
                   overlap=sorted(excluded & present))
        summary['n_valid_excluded'] = len(excluded)
    return summary


def float_parse_check(bundle_dir):
    """Does pandas' default CSV float parser return the exact values written? (round_trip parser is exact)."""
    if bundle_dir is None:
        return None
    default = pd.read_csv(Path(bundle_dir) / 'daily.csv').value
    exact = pd.read_csv(Path(bundle_dir) / 'daily.csv', float_precision='round_trip').value
    differ = (default != exact) & default.notna()
    rel = ((default - exact).abs() / exact.abs())[differ]
    return dict(n_values=int(default.notna().sum()), n_not_round_trip=int(differ.sum()),
                max_relative_difference=float(rel.max()) if len(rel) else 0.)


def audit_test_lock(chk, bcfg, tracks, inputs):
    chk.expect('test_lock', bcfg['test']['allow_test_split'] is False, check='allow_test_split_false')
    chk.expect('test_lock', 'test' not in tracks, check='test_not_requested')
    chk.expect('test_lock', inputs['valid_daily'] is None or not inputs['valid_daily'].split.eq('test').any(), check='no_test_rows_in_valid')


def run_audit(inputs, analysis, bcfg, tracks, approved_sha):
    chk = Check()
    baseline_sha = audit_lock(chk, inputs, approved_sha)
    artifact = inputs['artifact']
    audit_hash_chain(chk, artifact, inputs['train_daily'])
    recomputed = audit_formulas(chk, artifact)
    valid = inputs['valid_daily']
    out = dict(baseline_sha256=baseline_sha, artifact_id=artifact['artifact_id'], baseline_version=artifact.get('baseline_version'),
               fit_called_during_validation=inputs.get('fit_called_during_validation'),
               fixture_fitted=inputs.get('fixture_fitted'), limitations=[])
    if valid is None or analysis is None:
        out['limitations'].append('valid bundle unavailable: distances/null/coverage not audited')
    else:
        audit_distances(chk, analysis, recomputed)
        out['null_cases'] = audit_nulls(chk, analysis, valid)
        out['previous_day_cases'] = audit_previous_day(chk, analysis, valid)
        bundle = inputs['bundles'].get('valid')
        hour_qc = pd.read_csv(Path(bundle) / 'hour_qc.csv')
        cov = json.loads((Path(bundle) / 'coverage_report.json').read_text(encoding='utf-8'))
        audit_coverage(chk, valid, hour_qc, inputs['cfg'], inputs['expected_days'].get('valid'), cov)
        out['official_reports'] = audit_official(chk, analysis, valid, inputs['official'], artifact, baseline_sha)
        out['float_parse_check'] = float_parse_check(bundle)
        if not inputs['hourly']:
            out['limitations'].append('no hourly features: daily metric values cannot be recomputed from hourly rows')
        n_people = valid.groupby('dataset').subject_id.nunique().to_dict()
        if min(n_people.values(), default=0) < bcfg['evaluability']['min_valid_subjects_for_distribution']:
            out['limitations'].append(f'valid people per cohort {n_people}: verifies pipeline correctness, not performance')
    audit_test_lock(chk, bcfg, tracks, inputs)
    out['sha_after'] = sha256(inputs['baseline_path'])
    chk.expect('baseline_lock', out['sha_after'] == baseline_sha, check='unchanged_after_audit')
    out['categories'] = chk.result()
    return clean_json(out)


def expected_days_from_manifest(bundle_dir):
    """(dataset, subject_id, day) from provenance.sources[*].qc_valid_days, or None when not recorded."""
    if bundle_dir is None:
        return None
    m = json.loads((Path(bundle_dir) / 'bundle.manifest.json').read_text(encoding='utf-8'))
    sources = m['provenance'].get('sources') or []
    if not sources:
        return None
    split = load_bundle(bundle_dir)[0].split.iloc[0]
    return {(s['dataset'], s['subject_id'], int(d)) for s in sources if s.get('split') == split for d in s['qc_valid_days']}
