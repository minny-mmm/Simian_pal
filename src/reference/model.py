"""Subject-balanced empirical reference distributions, with strict train/LOO handling."""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .common import clean_json, code_version, fingerprint, validate_config, validate_roster, write_json
from .daily import METRICS
from .provenance import validate_real_fit_provenance


def weighted_quantile(values, weights, q):
    """Inverse empirical CDF: smallest x with normalized cumulative weight >= q."""
    x, w = np.asarray(values, float), np.asarray(weights, float)
    if not len(x) or len(x) != len(w) or not np.isfinite(x).all() or not np.isfinite(w).all() or (w <= 0).any() or not 0 <= q <= 1:
        raise ValueError('Invalid weighted quantile inputs')
    order = np.argsort(x, kind='stable')
    x, w = x[order], w[order]
    # Avoid a normalized cumulative mass of 0.49999999999999994 selecting the next
    # order statistic at q=.5. Recompute only the boundary with compensated sums.
    target = float(q) * math.fsum(w)
    index = min(int(np.searchsorted(np.cumsum(w), target, side='left')), len(x) - 1)
    while index > 0 and math.fsum(w[:index]) >= target:
        index -= 1
    while index < len(x) - 1 and math.fsum(w[:index + 1]) < target:
        index += 1
    return float(x[index])


def summarize(entries, policy):
    f = pd.DataFrame(entries, columns=['subject_id', 'day', 'value'])
    if len(f):
        counts = f.groupby('subject_id').size()
        f = f[f.subject_id.isin(counts[counts >= policy['min_days_per_participant']].index)]
    ids = sorted(f.subject_id.unique()) if len(f) else []
    result = dict(status='insufficient_data', reason='reference_participants_or_days_below_minimum',
                  n_participants=len(ids), n_days=len(f), subject_ids=ids,
                  median=None, quantiles={}, mad=None, scale=None,
                  days_per_subject=f.groupby('subject_id').size().to_dict() if len(f) else {})
    if len(ids) < policy['min_participants'] or len(f) < policy['min_days']:
        return result
    # Exactly one total unit of weight per subject, regardless of their number of days.
    weights = 1 / f.subject_id.map(f.groupby('subject_id').size()).to_numpy(float)
    median = weighted_quantile(f.value, weights, .5)
    mad = weighted_quantile(np.abs(f.value.to_numpy(float) - median), weights, .5)
    scale = float(policy['mad_scale_factor'] * mad)
    result.update(status='ok' if scale > 0 else 'zero_scale', reason='' if scale > 0 else 'weighted_MAD_is_zero',
                  median=median, mad=mad, scale=scale,
                  quantiles={str(q): weighted_quantile(f.value, weights, q) for q in policy['quantiles']})
    return result


def validate_daily(daily, roster, cfg):
    validate_config(cfg)
    needed = ['dataset', 'subject_id', 'split', 'day', 'metric', 'unit', 'value', 'status',
              'feature_contract', 'config_hash', 'preprocessing_version', 'data_kind']
    if set(needed) - set(daily):
        raise ValueError(f'Daily schema missing: {sorted(set(needed) - set(daily))}')
    validate_roster(daily, roster)
    if daily.duplicated(['dataset', 'subject_id', 'day', 'metric']).any():
        raise ValueError('Duplicate daily indicators')
    if not daily.feature_contract.eq(cfg['feature_contract']).all() or not daily.config_hash.eq(fingerprint(cfg)).all():
        raise ValueError('Feature/config contract mismatch; do not mix preprocessing versions')
    if not daily.status.isin(['ok', 'insufficient_data']).all():
        raise ValueError('Unknown daily status')
    if not daily.metric.isin(METRICS).all():
        raise ValueError('Unknown daily metric')
    if not daily.unit.eq(daily.metric.map({k: v['unit'] for k, v in METRICS.items()})).all():
        raise ValueError('Metric unit mismatch')
    values = pd.to_numeric(daily.value, errors='raise')
    days = pd.to_numeric(daily.day, errors='raise')
    if not np.isfinite(days).all() or (days < 0).any() or not days.eq(np.floor(days)).all():
        raise ValueError('Invalid daily day index')
    if (daily.status.eq('ok') & (~np.isfinite(values))).any():
        raise ValueError('OK daily values must be finite')
    if (daily.status.ne('ok') & values.notna()).any():
        raise ValueError('Insufficient daily values must be null')
    if ((daily.metric.eq('rest_tremor_window_fraction') & values.notna()) & ~values.between(0,1)).any():
        raise ValueError('Daily fraction outside [0,1]')
    if ((daily.metric.eq('enmo_hourly_median_mean_g') & values.notna()) & (values < 0)).any():
        raise ValueError('Negative daily ENMO')
    if ((daily.metric.eq('walking_cadence_hourly_median_mean_hz') & values.notna()) & (values <= 0)).any():
        raise ValueError('Nonpositive daily walking cadence')
    if not daily.preprocessing_version.notna().all() or daily.preprocessing_version.astype(str).eq('').any():
        raise ValueError('Missing preprocessing version')
    if not daily.data_kind.isin(['real', 'synthetic']).all() or daily.data_kind.nunique() != 1 or daily.preprocessing_version.nunique() != 1:
        raise ValueError('Mixed data kind/preprocessing versions')


def fit_reference(daily, roster, cfg, provenance):
    validate_daily(daily, roster, cfg)
    train = daily[daily.split.eq('train')].copy()
    if train.empty:
        raise ValueError('No train daily records')
    if provenance.get('feature_contract') != cfg['feature_contract'] or provenance.get('config_hash') != fingerprint(cfg):
        raise ValueError('Provenance contract/config mismatch')
    if provenance.get('preprocessing_version') != train.preprocessing_version.iloc[0] or provenance.get('data_kind') != train.data_kind.iloc[0]:
        raise ValueError('Provenance does not match daily rows')
    if not provenance.get('verified'):
        raise ValueError('Unverified feature provenance: regenerate with versioned extractor first')
    validate_real_fit_provenance(provenance, train, cfg)
    refs = {}
    for dataset in ['cops', 'nhanes']:
        refs[dataset] = {}
        for metric, definition in METRICS.items():
            f = train[train.dataset.eq(dataset) & train.metric.eq(metric) & train.status.eq('ok')]
            entries = f.sort_values(['subject_id', 'day'])[['subject_id', 'day', 'value']].to_dict('records')
            refs[dataset][metric] = dict(unit=definition['unit'], entries=clean_json(entries),
                                         summary=summarize(entries, cfg['reference_policy']))
    artifact = dict(schema_version=1, kind='separate_cohort_reference_not_diagnosis',
                    created_utc=datetime.now(timezone.utc).isoformat(), data_kind=train.data_kind.iloc[0],
                    fit_split='train', config=cfg, config_hash=fingerprint(cfg), metrics=METRICS,
                    preprocessing_version=train.preprocessing_version.iloc[0], provenance=provenance,
                    code_version=code_version(), roster=roster.to_dict('records'),
                    roster_hash=fingerprint(roster.to_dict('records')),
                    training_daily_hash=fingerprint(clean_json(train.sort_values(['dataset','subject_id','day','metric']).to_dict('records'))),
                    method=dict(weight='each eligible subject has total weight 1/N; each day weight 1/(N*days_for_subject_and_metric)',
                                quantile='inverse weighted empirical CDF, first cumulative mass >= q; no interpolation',
                                mad='weighted median(abs(value - weighted median)) with the same subject-balanced weights',
                                standardized_deviation='(daily_value - reference_median)/(1.4826*MAD); null if scale==0',
                                train_scoring='exclude ALL days of the scored train subject and refit subject weights and summary',
                                interpretation='signed distance from a cohort reference, not severity, diagnostic probability or a combined score'),
                    references=refs)
    artifact.update(is_demo=train.data_kind.iloc[0]=='synthetic',data_origin=train.data_kind.iloc[0],
        baseline_version=cfg.get('baseline_version',('demo-' if train.data_kind.iloc[0]=='synthetic' else '')+cfg['version']))
    artifact['artifact_id'] = fingerprint(artifact)
    return clean_json(artifact)


def save_reference(path, artifact):
    write_json(path, artifact)


def load_reference(path):
    artifact = json.loads(Path(path).read_text(encoding='utf-8'))
    expected = artifact.pop('artifact_id', None)
    if expected != fingerprint(artifact) or artifact.get('schema_version') != 1 or artifact.get('fit_split') != 'train':
        raise ValueError('Invalid/tampered/unsupported reference artifact')
    artifact['artifact_id'] = expected
    # Artifact must never contain held-out participants, even if loaded from elsewhere.
    roster = pd.DataFrame(artifact['roster'])
    for ds, metrics in artifact['references'].items():
        for ref in metrics.values():
            ids = {r['subject_id'] for r in ref['entries']}
            allowed = set(roster.loc[roster.dataset.eq(ds) & roster.split.eq('train'), 'subject_id'])
            if not ids <= allowed:
                raise ValueError('Non-train participant in reference artifact')
    return artifact


def compare_daily(daily, artifact):
    cfg = artifact['config']
    roster = pd.DataFrame(artifact['roster'])
    validate_daily(daily, roster, cfg)
    if not daily.preprocessing_version.eq(artifact['preprocessing_version']).all() or not daily.data_kind.eq(artifact['data_kind']).all():
        raise ValueError('Query does not match fitted preprocessing/data kind')
    outputs, memo = [], {}
    for r in daily.to_dict('records'):
        comparisons = {}
        for dataset in ['cops', 'nhanes']:
            ref = artifact['references'][dataset][r['metric']]
            excluded = r['subject_id'] if r['split'] == 'train' else None
            key = (dataset, r['metric'], excluded)
            if key not in memo:
                memo[key] = summarize([v for v in ref['entries'] if v['subject_id'] != excluded], cfg['reference_policy']) if excluded else ref['summary']
            summary = memo[key]
            status = r['status'] if r['status'] != 'ok' else summary['status']
            distance = standardized = None
            if r['status'] == 'ok' and summary['status'] in ['ok', 'zero_scale']:
                distance = float(r['value'] - summary['median'])
                if summary['scale'] > 0:
                    standardized = distance / summary['scale']
            comparisons[dataset] = dict(status=status, reason=r.get('reasons', '') if r['status'] != 'ok' else summary['reason'],
                                        signed_distance=distance, absolute_distance=abs(distance) if distance is not None else None,
                                        unit=r['unit'], standardized_deviation=standardized,
                                        reference_median=summary['median'], reference_scale=summary['scale'],
                                        reference_mad=summary['mad'], reference_quantiles=summary['quantiles'],
                                        n_participants=summary['n_participants'], n_days=summary['n_days'],
                                        excluded_train_subject=excluded if excluded in {v['subject_id'] for v in ref['entries']} else None)
        outputs.append(dict(subject_id=r['subject_id'], dataset=r['dataset'], split=r['split'], day=r['day'],
                            metric=r['metric'], value=r['value'], unit=r['unit'], status=r['status'],
                            reporting_clock_hours=cfg['feature_policy']['clock_hours'],
                            daily_definition=METRICS[r['metric']]['daily_statistic'],
                            coverage={k:r[k] for k in ['day_valid_minutes','day_eligible_hours','metric_minutes','metric_windows','metric_eligible_hours','missing_metric_windows'] if k in r},
                            reference_comparisons=comparisons, artifact_id=artifact['artifact_id'],
                            interpretation='distance_from_cohort_reference_only'))
    return clean_json(outputs)
