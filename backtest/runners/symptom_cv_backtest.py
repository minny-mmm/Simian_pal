"""Track B (exploratory): COPS hourly tremor presence (TremorScore 0 vs 1/2) with subject-grouped CV.

COPS only, so label and device do not overlap here. Train-only statistics inside every fold (median
imputation + missing indicators, standardization, class weights); NaN is never filled with 0; forbidden
identity/time/count columns are never inputs; test is never read. numpy-only to avoid new dependencies.
"""
import glob
from pathlib import Path

import numpy as np
import pandas as pd

from src.features.run import AMPLITUDE_PREFIXES
from src.reference.common import ROOT

from backtest.evaluators.metrics import (average_precision, balanced_accuracy, recall_per_class,
                                         subject_bootstrap_ci)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def synthetic_symptom_table(seed, n_train=12, n_valid=4, days=4):
    """Fabricated COPS-like hourly rows with a planted tremor signal plus columns that must be excluded."""
    rng = np.random.default_rng(seed)
    rows = []
    for split, count in [('train', n_train), ('valid', n_valid), ('test', 2)]:
        for i in range(1, count + 1):
            sid, propensity = f'SYNTHETIC-COPS-{split.upper()}-{i:02d}', rng.uniform(-1.5, .5)
            for day in range(days):
                for hour in range(9, 21):
                    latent = propensity + rng.normal(0, 1)
                    score = 0 if latent < 0 else 1 if latent < 1.2 else 2
                    n_rest = int(rng.integers(40, 160))
                    rows.append(dict(subject_id=sid, dataset='cops', label=1, split=split, day=day, hour=hour,
                        hour_idx=day * 24 + hour, n_windows=360, valid_min=60., n_rest=n_rest,
                        tremor_frac_all=float(np.clip(.02 + .05 * latent + rng.normal(0, .03), 0, 1)),
                        dom_freq_rest_median=rng.normal(5, 1) if n_rest else np.nan,
                        step_freq_walk_median=rng.normal(1.8, .1) if rng.random() > .3 else np.nan,
                        enmo_median=abs(rng.normal(.03, .01)), tremor_score=score, candidate_tremor=True))
    return pd.DataFrame(rows)


def load_real_symptom_table(scfg, roster, root=ROOT):
    split = roster[roster.dataset.eq('cops') & roster.split.isin(['train', 'valid'])][['subject_id', 'split']]
    allowed = set(split.subject_id)
    # One CSV per subject (<subject_id>.csv): select by roster split BEFORE reading, so test files are never opened.
    found = sorted(glob.glob(str(Path(root) / scfg['features_glob'])))
    files = [f for f in found if Path(f).stem in allowed]
    labels_path = Path(root) / scfg['labels_path'] if scfg.get('labels_path') else None
    if not files:
        return None, f"no train/valid COPS feature CSVs at {scfg['features_glob']}"
    if labels_path is None or not labels_path.exists():
        return None, 'diary labels not configured/found (symptom.labels_path)'
    features = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    features = features[features.dataset.eq('cops')].drop(columns=['split'], errors='ignore')
    features = features.merge(split, on='subject_id', how='inner', validate='many_to_one')
    labels = pd.read_csv(labels_path)
    labels = labels[labels.subject_id.isin(allowed)]  # drop test label rows immediately
    if scfg.get('candidate_column') in labels:
        labels = labels[labels[scfg['candidate_column']].astype(str).str.lower().eq('true')]
    labels = labels.drop(columns=['split'], errors='ignore')
    table = features.merge(labels, on=['subject_id', 'day', 'hour'], how='inner', validate='one_to_one')
    # A low match rate usually means diary and feature day/hour indexing disagree (README_signal_audit.md).
    note = (f'{len(table)} matched hours from {len(features)} feature hours and {len(labels)} label hours '
            f'({len(files)} train/valid files; {len(found) - len(files)} other files not opened); '
            f'label match rate {len(table) / len(labels):.3f}' if len(labels) else 'no label hours')
    return table, note


# ---------------------------------------------------------------------------
# Model pieces
# ---------------------------------------------------------------------------
def feature_columns(table, scfg):
    excluded = set(scfg['exclude_columns']) | {scfg['label_column']}
    cols = [c for c in table.columns if c not in excluded and pd.api.types.is_numeric_dtype(table[c])]
    if scfg.get('drop_amplitude'):
        cols = [c for c in cols if not c.startswith(AMPLITUDE_PREFIXES)]
    return cols


def group_kfold(groups, n_folds):
    """Deterministic GroupKFold: largest subjects first into the currently smallest fold."""
    sizes = pd.Series(groups).value_counts()
    n_folds = min(n_folds, len(sizes))
    load, assign = np.zeros(n_folds), {}
    for sid, size in sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0])):
        k = int(np.argmin(load))
        assign[sid], load[k] = k, load[k] + size
    fold = np.array([assign[g] for g in groups])
    return [(np.flatnonzero(fold != k), np.flatnonzero(fold == k)) for k in range(n_folds)]


class Preprocessor:
    """Train-only median imputation with missing indicators, then standardization.
    Columns with no finite train value are dropped rather than filled."""

    def fit(self, X):
        finite = np.isfinite(X)
        self.keep = finite.any(0)
        X = np.where(finite, X, np.nan)[:, self.keep]
        self.median = np.nanmedian(X, axis=0)
        Z = self._impute(X, select=False)
        self.mean, self.sd = Z.mean(0), Z.std(0)
        self.sd[self.sd == 0] = 1.
        return self

    def _impute(self, X, select=True):
        if select:
            X = X[:, self.keep]
        missing = ~np.isfinite(X)
        return np.hstack([np.where(missing, self.median, X), missing.astype(float)])

    def transform(self, X):
        return (self._impute(X) - self.mean) / self.sd


def fit_logistic(X, y, l2, iters=50):
    """L2 logistic regression (intercept unpenalized), class-balanced weights, Newton iterations."""
    X1 = np.hstack([np.ones((len(X), 1)), X])
    pos = y.mean()
    w = np.where(y == 1, .5 / pos, .5 / (1 - pos))
    beta = np.zeros(X1.shape[1])
    penalty = np.full(X1.shape[1], l2)
    penalty[0] = 0.
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(X1 @ beta, -30, 30)))
        grad = X1.T @ (w * (p - y)) + penalty * beta
        hess = (X1 * (w * p * (1 - p))[:, None]).T @ X1 + np.diag(penalty) + 1e-9 * np.eye(len(beta))
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def predict(beta, X):
    return 1 / (1 + np.exp(-np.clip(np.hstack([np.ones((len(X), 1)), X]) @ beta, -30, 30)))


def _fit_predict(train, test, cols, scfg):
    prep = Preprocessor().fit(train[cols].to_numpy(float))
    beta = fit_logistic(prep.transform(train[cols].to_numpy(float)), train.target.to_numpy(float), scfg['l2'])
    return predict(beta, prep.transform(test[cols].to_numpy(float)))


def scores(frame):
    y, p = frame.target.to_numpy(int), frame.prob.to_numpy(float)
    pred = (p >= .5).astype(int)
    prevalence = float(y.mean()) if len(y) else None
    per_subject = [dict(subject_id=s, n_hours=len(g), positive_rate=float(g.target.mean()),
                        accuracy=float(np.mean((g.prob >= .5).astype(int) == g.target)))
                   for s, g in frame.groupby('subject_id')]
    return dict(n_hours=len(y), n_subjects=int(frame.subject_id.nunique()), prevalence=prevalence,
                balanced_accuracy=balanced_accuracy(y, pred), pr_auc=average_precision(y, p),
                recall_per_class=recall_per_class(y, pred),
                dummy=dict(balanced_accuracy=.5 if len(set(y)) == 2 else None, pr_auc=prevalence),
                per_subject=per_subject)


def run_symptom(table, scfg, source_note=''):
    if table is None:
        return dict(status='blocked', reason=source_note)
    table = table[table.split.isin(['train', 'valid'])].copy()  # test is never read by Track B
    table = table[table[scfg['label_column']].isin([0, 1, 2])]
    table['target'] = table[scfg['label_column']].isin(scfg['positive_scores']).astype(int)
    cols = feature_columns(table, scfg)
    train = table[table.split.eq('train')].reset_index(drop=True)
    if train.subject_id.nunique() < 2 or train.target.nunique() < 2:
        return dict(status='insufficient_train', reason='need >=2 train subjects and both classes', source=source_note)
    folds, oof = group_kfold(train.subject_id.to_numpy(), scfg['n_folds']), np.full(len(train), np.nan)
    fold_report = []
    for k, (tr, te) in enumerate(folds):
        overlap = set(train.subject_id.iloc[tr]) & set(train.subject_id.iloc[te])
        if overlap:
            raise AssertionError(f'Subject leakage across folds: {sorted(overlap)}')
        fit_part = train.iloc[tr]
        if fit_part.target.nunique() < 2:
            fold_report.append(dict(fold=k, status='single_class_train_fold'))
            continue
        oof[te] = _fit_predict(fit_part, train.iloc[te], cols, scfg)
        fold_report.append(dict(fold=k, status='ok', n_test_subjects=int(train.iloc[te].subject_id.nunique())))
    cv = train.assign(prob=oof).dropna(subset=['prob'])
    cv_scores = scores(cv)
    for name, stat in [('balanced_accuracy', lambda f: balanced_accuracy(f.target, (f.prob >= .5).astype(int))),
                       ('pr_auc', lambda f: average_precision(f.target, f.prob))]:
        cv_scores[f'{name}_subject_ci95'] = subject_bootstrap_ci(cv, stat, scfg['n_bootstrap'], scfg['seed'])
    valid = table[table.split.eq('valid')].reset_index(drop=True)
    if valid.subject_id.nunique() < scfg['min_valid_subjects'] or valid.target.nunique() < 2:
        valid_scores = dict(status='insufficient_valid', n_subjects=int(valid.subject_id.nunique()),
                            n_classes=int(valid.target.nunique()))
    else:
        valid_scores = dict(status='ok', **scores(valid.assign(prob=_fit_predict(train, valid, cols, scfg))))
    return dict(status='ok', source=source_note, features=cols, n_folds=len(folds), folds=fold_report,
                cv=cv_scores, valid=valid_scores,
                note='Exploratory hourly symptom task, not the baseline target (README_model_plan.md).')
