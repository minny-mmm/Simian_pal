"""Small, dependency-free metrics shared by the backtest tracks."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def tree_snapshot(root, dirs):
    """SHA256 of every file under root/<dir>, ignoring bytecode caches."""
    root = Path(root)
    out = {}
    for d in dirs:
        for p in sorted((root / d).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc':
                out[p.relative_to(root).as_posix()] = sha256_bytes(p.read_bytes())
    return out


def snapshot_diff(before, after):
    keys = set(before) | set(after)
    return sorted(k for k in keys if before.get(k) != after.get(k))


def robust_summary(values):
    x = np.asarray([v for v in values if v is not None and np.isfinite(v)], float)
    if not len(x):
        return dict(n=0, median=None, q25=None, q75=None, mad=None, frac_abs_gt_2=None)
    med = float(np.median(x))
    return dict(n=int(len(x)), median=med, q25=float(np.quantile(x, .25)), q75=float(np.quantile(x, .75)),
                mad=float(np.median(np.abs(x - med))), frac_abs_gt_2=float(np.mean(np.abs(x) > 2)))


def balanced_accuracy(y, pred):
    y, pred = np.asarray(y, int), np.asarray(pred, int)
    recalls = [float(np.mean(pred[y == c] == c)) for c in (0, 1) if (y == c).any()]
    return float(np.mean(recalls)) if len(recalls) == 2 else None


def recall_per_class(y, pred):
    y, pred = np.asarray(y, int), np.asarray(pred, int)
    return {str(c): (float(np.mean(pred[y == c] == c)) if (y == c).any() else None) for c in (0, 1)}


def average_precision(y, score):
    """PR-AUC as step-wise average precision (ties broken by stable descending sort)."""
    y, score = np.asarray(y, int), np.asarray(score, float)
    if not y.any():
        return None
    order = np.argsort(-score, kind='stable')
    y = y[order]
    tp = np.cumsum(y)
    precision = tp / np.arange(1, len(y) + 1)
    return float(np.sum(precision[y == 1]) / y.sum())


def subject_bootstrap_ci(frame, stat, n, seed, group='subject_id'):
    """95% percentile CI of stat(frame) when resampling whole subjects with replacement."""
    rng = np.random.default_rng(seed)
    groups = {k: g for k, g in frame.groupby(group)}
    keys = sorted(groups)
    values = []
    for _ in range(n):
        sample = pd.concat([groups[k] for k in rng.choice(keys, len(keys), replace=True)], ignore_index=True)
        v = stat(sample)
        if v is not None and np.isfinite(v):
            values.append(v)
    if len(values) < max(10, n // 10):
        return dict(low=None, high=None, n_valid=len(values))
    return dict(low=float(np.quantile(values, .025)), high=float(np.quantile(values, .975)), n_valid=len(values))
