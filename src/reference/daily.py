"""Explicit summaries of hourly medians and count ratios; never pretend to pool raw medians."""
import numpy as np
import pandas as pd

from .common import fingerprint, validate_config, validate_roster

METRICS = {
    'enmo_hourly_median_mean_g': dict(unit='g', hourly='enmo_median', denominator='n_windows',
        hourly_statistic='median of window mean(max(norm(a)-1g,0))',
        daily_statistic='sum(hourly_median * n_windows) / sum(n_windows); NOT pooled window median'),
    'walking_cadence_hourly_median_mean_hz': dict(unit='steps/s', hourly='step_freq_walk_median', denominator='n_walk',
        hourly_statistic='median(2*50/stride_lag) on walking windows in Mew 37d5722',
        daily_statistic='sum(hourly_median * n_walk) / sum(n_walk); NOT pooled walking median'),
    'rest_tremor_window_fraction': dict(unit='fraction', hourly='tremor_frac_of_rest', denominator='n_rest',
        hourly_statistic='n_tremor/n_rest; null when n_rest=0',
        daily_statistic='sum(n_tremor) / sum(n_rest); NOT mean of hourly ratios'),
}
COUNTS = ['n_windows', 'n_walk', 'n_clean_walk', 'n_rest', 'n_tremor']
KEYS = ['dataset', 'subject_id', 'day', 'hour']


def validate_hourly(hourly, roster, cfg):
    validate_config(cfg)
    required = KEYS + ['split', 'valid_min', *COUNTS, 'enmo_median', 'step_freq_walk_median', 'tremor_frac_of_rest']
    missing = set(required) - set(hourly)
    if missing:
        raise ValueError(f'Hourly schema missing: {sorted(missing)}')
    f = hourly.copy()
    validate_roster(f, roster)
    for c in ['day', 'hour', 'valid_min', *COUNTS]:
        f[c] = pd.to_numeric(f[c], errors='raise')
        if not np.isfinite(f[c]).all() or (f[c] < 0).any():
            raise ValueError(f'Invalid {c}')
    if f.duplicated(KEYS).any():
        raise ValueError('Duplicate hourly keys; never average duplicates')
    if not f.hour.between(0, 23).all() or not f[['day', 'hour', *COUNTS]].eq(np.floor(f[['day', 'hour', *COUNTS]])).all().all():
        raise ValueError('Noninteger day/hour/window counts')
    if (f.n_windows <= 0).any() or (f.n_windows > 360).any() or (f.valid_min > 60).any():
        raise ValueError('Invalid hourly coverage')
    if not np.allclose(f.valid_min, f.n_windows * cfg['feature_policy']['window_seconds'] / 60, atol=1e-6):
        raise ValueError('valid_min must describe complete windows, not all valid sample minutes')
    if ((f.n_clean_walk > f.n_walk) | (f.n_walk + f.n_rest > f.n_windows) | (f.n_tremor > f.n_rest)).any():
        raise ValueError('Impossible walk/rest/tremor counts')
    for c in [m['hourly'] for m in METRICS.values()]:
        f[c] = pd.to_numeric(f[c], errors='raise')
        if np.isinf(f[c]).any():
            raise ValueError(f'Infinite indicator: {c}')
    if (f.enmo_median.dropna() < 0).any() or (f.step_freq_walk_median.dropna() <= 0).any():
        raise ValueError('Impossible ENMO/cadence')
    for count, value in [('n_walk', 'step_freq_walk_median'), ('n_rest', 'tremor_frac_of_rest')]:
        if (f.loc[f[count].eq(0), value].notna()).any():
            raise ValueError(f'{value} must be null when {count}=0')
    ratio_rows = (f.n_rest > 0) & f.tremor_frac_of_rest.notna()
    if not np.allclose(f.loc[ratio_rows, 'tremor_frac_of_rest'], f.loc[ratio_rows, 'n_tremor'] / f.loc[ratio_rows, 'n_rest'], atol=1e-8):
        raise ValueError('Rest tremor ratio does not match numerator/denominator')
    return f


def aggregate_daily(hourly, roster, cfg, provenance, expected_days=None):
    f = validate_hourly(hourly, roster, cfg)
    p = cfg['feature_policy']
    outputs, hour_qc = [], []
    groups = dict(tuple(f.groupby(['dataset', 'subject_id', 'split', 'day'], sort=True)))
    if expected_days:
        expected = pd.DataFrame(expected_days)
        validate_roster(expected, roster)
        for r in expected.to_dict('records'):
            groups.setdefault((r['dataset'], r['subject_id'], r['split'], r['day']), f.iloc[:0])
    for (ds, sid, split, day), raw in sorted(groups.items()):
        in_clock = raw.hour.ge(p['clock_hours'][0]) & raw.hour.lt(p['clock_hours'][1])
        eligible = in_clock & raw.valid_min.ge(p['min_hour_valid_minutes'])
        g = raw[eligible]
        for row, clock, keep in zip(raw.to_dict('records'), in_clock, eligible):
            hour_qc.append(dict(dataset=ds, subject_id=sid, split=split, day=int(day), hour=int(row['hour']),
                                valid_min=row['valid_min'], included=bool(keep),
                                reason='' if keep else 'outside_reporting_clock' if not clock else 'hour_coverage_below_minimum'))
        day_ok = len(g) >= p['min_day_hours'] and g.valid_min.sum() >= p['min_day_valid_minutes']
        for name, definition in METRICS.items():
            den, col = definition['denominator'], definition['hourly']
            measurable = g[den].gt(0) & g[col].notna()
            good = g[measurable]
            covered_windows = int(good[den].sum())
            metric_minutes = covered_windows * p['window_seconds'] / 60
            reasons = []
            if not day_ok:
                reasons.append('day_coverage_below_minimum')
            if not len(good):
                reasons.append('no_eligible_windows' if g[den].sum() == 0 else 'metric_values_missing')
            requirement = p.get('metric_coverage', {}).get(name, dict(min_minutes=p['min_metric_minutes'], min_hours=p['min_metric_hours']))
            if metric_minutes < requirement['min_minutes'] or len(good) < requirement['min_hours']:
                reasons.append('metric_coverage_below_minimum')
            value = None
            if not reasons:
                value = (float(good.n_tremor.sum() / good.n_rest.sum()) if name == 'rest_tremor_window_fraction'
                         else float(np.average(good[col], weights=good[den])))
            outputs.append(dict(dataset=ds, subject_id=sid, split=split, day=int(day), metric=name,
                                unit=definition['unit'], value=value, status='ok' if not reasons else 'insufficient_data',
                                reasons='|'.join(reasons), day_valid_minutes=float(g.valid_min.sum()),
                                day_eligible_hours=len(g), observed_hour_rows=len(raw), metric_eligible_hours=len(good),
                                metric_windows=covered_windows, metric_minutes=metric_minutes,
                                available_metric_windows=int(g[den].sum()),
                                missing_metric_windows=int(g.loc[~measurable, den].sum()),
                                feature_contract=cfg['feature_contract'], config_hash=fingerprint(cfg),
                                preprocessing_version=provenance['preprocessing_version'],
                                data_kind=provenance['data_kind']))
    return pd.DataFrame(outputs), pd.DataFrame(hour_qc, columns=[
        'dataset','subject_id','split','day','hour','valid_min','included','reason'])
