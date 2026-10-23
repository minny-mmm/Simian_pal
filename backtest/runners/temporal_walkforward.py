"""Level 2: day-by-day replay. The reference is fixed, so the useful checks are causality and stability:
no look-ahead (day t identical whether or not later days exist), days until each indicator is first usable,
and how much a person's standardized deviation moves from day to day.
"""
import json

import numpy as np
import pandas as pd

from src.reference.daily import aggregate_daily
from src.reference.output import build_analysis

from backtest.evaluators.metrics import robust_summary


def _canonical(row):
    return json.dumps(row, sort_keys=True, allow_nan=False)


def no_lookahead(daily, artifact, analyze=build_analysis):
    """Replay each subject day by day and compare day t with the full-history output for day t."""
    if daily is None or daily.empty:
        return dict(n_checked=0, n_mismatches=0, mismatches=[])
    full = {(r['subject_id'], r['day'], r['metric']): _canonical(r) for r in analyze(daily, artifact)['analysis']}
    checked, mismatches = 0, []
    # Day indices are per subject, so "everyone up to day t" is each subject's own history up to t.
    for t in sorted(daily.day.unique()):
        for r in analyze(daily[daily.day.le(t)].reset_index(drop=True), artifact)['analysis']:
            if r['day'] != t:
                continue
            checked += 1
            if _canonical(r) != full[(r['subject_id'], t, r['metric'])]:
                mismatches.append(dict(subject_id=r['subject_id'], day=int(t), metric=r['metric']))
    return dict(n_checked=checked, n_mismatches=len(mismatches), mismatches=mismatches[:50])


def no_lookahead_hourly(hourly, roster, cfg, provenance, days):
    """Synthetic only: daily aggregation of day t from hourly rows up to t equals the full-history result."""
    if hourly is None or hourly.empty:
        return dict(n_checked=0, n_mismatches=0, mismatches=[])
    keys = ['dataset', 'subject_id', 'split', 'day', 'metric']

    def expected(frame, upto):
        return [dict(dataset=r.dataset, subject_id=r.subject_id, split=r.split, day=d)
                for r in frame[['dataset', 'subject_id', 'split']].drop_duplicates().itertuples() for d in range(upto + 1)]

    last = days - 1
    full, _ = aggregate_daily(hourly, roster, cfg, provenance, expected_days=expected(hourly, last))
    full = full.set_index(keys)
    checked, mismatches = 0, []
    for t in range(days):
        part, _ = aggregate_daily(hourly[hourly.day.le(t)], roster, cfg, provenance, expected_days=expected(hourly, t))
        part = part[part.day.eq(t)].set_index(keys)
        for key, row in part.iterrows():
            checked += 1
            ref = full.loc[key]
            if not all((pd.isna(row[c]) and pd.isna(ref[c])) or row[c] == ref[c] for c in part.columns):
                mismatches.append(dict(zip(keys, key)))
    return dict(n_checked=checked, n_mismatches=len(mismatches), mismatches=mismatches[:50])


def time_to_first_usable(daily):
    out = []
    if daily is None or daily.empty:
        return out
    for (ds, sid, split, metric), g in daily.groupby(['dataset', 'subject_id', 'split', 'metric']):
        g = g.sort_values('day')
        ok = g[g.status.eq('ok')]
        first = int(ok.day.iloc[0] - g.day.iloc[0]) if len(ok) else None
        cumulative = g.status.ne('ok').cumsum() / np.arange(1, len(g) + 1)
        out.append(dict(dataset=ds, subject_id=sid, split=split, metric=metric, n_days=len(g), n_ok=len(ok),
                        days_until_first_ok=first, frac_insufficient=float(cumulative.iloc[-1])))
    return out


def within_subject_stability(analysis_rows):
    f = pd.DataFrame(analysis_rows)
    out = []
    if f.empty:
        return out
    for (sid, split, metric, ref), g in f.groupby(['subject_id', 'split', 'metric', 'reference']):
        g = g.sort_values('day')
        z = g.set_index('day').standardized_deviation.dropna()
        # Day-to-day steps only between consecutive day indices; gaps are not bridged.
        steps = [z[d] - z[d - 1] for d in z.index if d - 1 in z.index]
        deltas = g.drop_duplicates('day').previous_day_delta.dropna()
        out.append(dict(subject_id=sid, split=split, metric=metric, reference=ref, n_z=len(z),
                        z_median=float(z.median()) if len(z) else None,
                        z_sd=float(z.std(ddof=1)) if len(z) > 1 else None,
                        z_step_abs_median=float(np.median(np.abs(steps))) if steps else None,
                        n_previous_day_delta=len(deltas),
                        previous_day_delta_abs_median=float(deltas.abs().median()) if len(deltas) else None))
    return out


def summarize_stability(rows):
    f = pd.DataFrame(rows)
    if f.empty:
        return []
    return [dict(split=split, metric=metric, reference=ref, n_subjects=len(g),
                 z_sd=robust_summary(g.z_sd.dropna().tolist()),
                 z_step_abs_median=robust_summary(g.z_step_abs_median.dropna().tolist()))
            for (split, metric, ref), g in f.groupby(['split', 'metric', 'reference'])]
