import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(8 << 20), b''):
            h.update(block)
    return h.hexdigest()


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer, np.bool_)):
        return value.item()
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean_json(value), ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def load_config(path=ROOT / 'config/reference_baseline.yaml'):
    cfg = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    validate_config(cfg)
    return cfg


def validate_config(cfg):
    p, r = cfg['feature_policy'], cfg['reference_policy']
    start, end = p['clock_hours']
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= 24):
        raise ValueError('Invalid reporting clock hours')
    if p['window_seconds'] != 10 or not 0 <= p['min_hour_valid_minutes'] <= 60:
        raise ValueError('Invalid window/coverage policy')
    if not 0 <= p['min_day_valid_minutes'] <= (end-start)*60 or not 1 <= p['min_day_hours'] <= end-start:
        raise ValueError('Invalid daily coverage policy')
    for spec in p.get('metric_coverage', {}).values():
        if not 0 <= spec['min_minutes'] <= (end-start)*60 or not 1 <= spec['min_hours'] <= end-start:
            raise ValueError('Invalid indicator coverage policy')
    for key in ['min_participants','min_days','min_days_per_participant']:
        if not isinstance(r[key],int) or r[key] < 1:
            raise ValueError('Reference minima must be positive integers')
    if not np.isfinite(r['mad_scale_factor']) or r['mad_scale_factor'] <= 0 or not r['quantiles'] or any(not 0 <= q <= 1 for q in r['quantiles']):
        raise ValueError('Invalid reference scale/quantile policy')


def fingerprint(value):
    return hashlib.sha256(json.dumps(clean_json(value), sort_keys=True).encode()).hexdigest()


def code_version():
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT, stderr=subprocess.DEVNULL).decode().strip()
    try:
        commit, dirty = git('rev-parse', 'HEAD'), bool(git('status', '--porcelain'))
    except (OSError, subprocess.SubprocessError):
        commit, dirty = 'unavailable', None
    paths = sorted((ROOT / 'src/reference').glob('*.py')) + sorted((ROOT / 'tools').glob('*reference*.py'))
    return dict(git_commit=commit, dirty=dirty, files={p.relative_to(ROOT).as_posix(): sha256(p) for p in paths})


def load_roster(root=ROOT, pipeline=None):
    pipeline = pipeline or yaml.safe_load((root / 'config/config.yaml').read_text(encoding='utf-8'))
    c = pipeline['clean']['subjects']
    cops = pd.read_csv(root / c['cops_splits'], dtype=str).fillna('')
    nh = pd.read_csv(root / c['nhanes_selected'], dtype=str).fillna('')
    cops = cops.assign(dataset='cops')
    nh = nh.assign(dataset='nhanes', subject_id='NHANES-' + nh.seqn)
    roster = pd.concat([cops, nh], ignore_index=True)[['dataset', 'subject_id', 'split']]
    if roster.subject_id.duplicated().any() or not roster.split.isin(['train', 'valid', 'test']).all():
        raise ValueError('Invalid/duplicate roster; do not modify splits automatically')
    return roster.sort_values(['dataset', 'subject_id']).reset_index(drop=True)


def validate_roster(frame, roster):
    keys = ['dataset', 'subject_id', 'split']
    actual = frame[keys].drop_duplicates()
    merged = actual.merge(roster[keys], on=keys, how='left', indicator=True, validate='one_to_one')
    if not merged._merge.eq('both').all():
        raise ValueError('Unknown subject, dataset or split does not match authoritative roster')
