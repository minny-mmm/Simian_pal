"""Backtest inputs: synthetic fixtures with known answers, or the real reference run (read-only).

Nothing here writes outside backtest/workspace. Real inputs are used in place when present locally,
otherwise pulled from the S3 mirror into backtest/workspace/real/<same relative path>.
Test bundles are only located here, never loaded: opening them is the job of the gated test track.
"""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.reference.common import ROOT, fingerprint, load_config, sha256, write_json
from src.reference.daily import aggregate_daily
from src.reference.io import load_bundle, write_bundle
from src.reference.model import load_reference
from tools.reference_baseline import fit_bundle

BACKTEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = BACKTEST_ROOT / 'config/backtest_config.yaml'


def deep_merge(base, overrides):
    out = copy.deepcopy(base)
    for k, v in (overrides or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_backtest_config(path=DEFAULT_CONFIG, overrides=None):
    return deep_merge(yaml.safe_load(Path(path).read_text(encoding='utf-8')), overrides)


def pipeline_config():
    return yaml.safe_load((ROOT / 'config/config.yaml').read_text(encoding='utf-8'))


# ---------------------------------------------------------------------------
# Synthetic
# ---------------------------------------------------------------------------
COHORT = dict(cops=dict(enmo=.030, cadence=1.70, tremor_p=.08), nhanes=dict(enmo=.040, cadence=1.85, tremor_p=.02))


def synthetic_hourly(scfg, seed=None):
    """Fabricated hourly features (SYNTHETIC- IDs) with planted edge cases; returns hourly, roster, truth."""
    rng = np.random.default_rng(scfg['seed'] if seed is None else seed)
    h0, h1 = scfg['clock_hours']
    shift = scfg['planted_shift']
    roster, rows = [], []
    truth = dict(shifted=[], no_walking=[], missing_day=scfg['missing_day'], zero_scale=[])
    for ds, base in COHORT.items():
        n_valid = scfg['participants']['valid']
        for split, count in scfg['participants'].items():
            for i in range(1, count + 1):
                sid = f'SYNTHETIC-{ds.upper()}-{split.upper()}-{i:02d}'
                roster.append(dict(dataset=ds, subject_id=sid, split=split))
                shifted = split == 'valid' and i <= shift['subjects_per_cohort']
                no_walk = split == 'valid' and i == n_valid and n_valid > shift['subjects_per_cohort']
                if shifted:
                    truth['shifted'].append(dict(dataset=ds, subject_id=sid, metric=shift['metric'], sd_units=shift['sd_units']))
                if no_walk:
                    truth['no_walking'].append(dict(dataset=ds, subject_id=sid, day=scfg['no_walking_day']))
                # A shifted subject sits at the cohort centre so the planted shift is the whole answer.
                eff_enmo = 0. if shifted else rng.normal(0, .006)
                eff_cad, eff_trem = rng.normal(0, .08), rng.uniform(.5, 1.5)
                tremor_p = 0. if ds == 'nhanes' and scfg['nhanes_tremor_zero'] else base['tremor_p'] * eff_trem
                for day in range(scfg['days']):
                    if split == 'valid' and day == scfg['missing_day']:
                        continue
                    day_noise = rng.normal(0, .002)
                    for hour in range(h0, h1):
                        # NHANES train walks on few days -> cadence reference insufficient, as in the real checkpoint.
                        sparse_walk = ds == 'nhanes' and split == 'train' and day >= scfg['nhanes_train_walking_days']
                        n_walk = 0 if (no_walk and day == scfg['no_walking_day']) or sparse_walk else int(rng.integers(20, 80))
                        n_rest = int(rng.integers(80, 160))
                        n_tremor = int(rng.binomial(n_rest, tremor_p))
                        rows.append(dict(dataset=ds, subject_id=sid, split=split, day=day, hour=hour,
                            n_windows=360, valid_min=60., n_walk=n_walk, n_clean_walk=n_walk // 2,
                            n_rest=n_rest, n_tremor=n_tremor,
                            enmo_median=max(.001, base['enmo'] + eff_enmo + day_noise + rng.normal(0, .003)),
                            step_freq_walk_median=(base['cadence'] + eff_cad + rng.normal(0, .05)) if n_walk else np.nan,
                            tremor_frac_of_rest=n_tremor / n_rest))
    if scfg['nhanes_tremor_zero']:
        truth['zero_scale'].append(dict(dataset='nhanes', metric='rest_tremor_window_fraction'))
    return pd.DataFrame(rows), pd.DataFrame(roster), truth


def _expected_days(roster, split, days):
    return [dict(dataset=r.dataset, subject_id=r.subject_id, split=split, day=d)
            for r in roster[roster.split.eq(split)].itertuples() for d in range(days)]


def _write_split_bundle(out, hourly, roster, split, days, cfg, provenance):
    part = hourly[hourly.split.eq(split)]
    daily, qc = aggregate_daily(part, roster, cfg, provenance, expected_days=_expected_days(roster, split, days))
    write_bundle(out, daily, qc, roster, cfg, pipeline_config(), provenance)
    return daily


def build_synthetic(bcfg, workdir):
    """Write split bundles + fit the train reference with the project's own code, then plant the shift."""
    workdir = Path(workdir)
    if workdir.exists():
        raise ValueError(f'Synthetic workspace exists: {workdir}; use a new run ID')
    scfg, cfg = bcfg['synthetic'], load_config(ROOT / bcfg['reference_config'])
    hourly, roster, truth = synthetic_hourly(scfg)
    provenance = dict(verified=True, data_kind='synthetic', preprocessing_version='SYNTHETIC-BACKTEST-v1',
                      feature_contract=cfg['feature_contract'], config_hash=fingerprint(cfg),
                      note='All subjects and values fabricated for backtest software verification')
    bundles = {s: workdir / 'bundles' / s for s in ['train', 'valid', 'test']}
    _write_split_bundle(bundles['train'], hourly, roster, 'train', scfg['days'], cfg, provenance)
    fit_bundle(bundles['train'], workdir / 'model')
    baseline = workdir / 'model/baseline.json'
    artifact = load_reference(baseline)
    unshifted, _ = aggregate_daily(hourly[hourly.split.eq('valid')], roster, cfg, provenance,
                                   expected_days=_expected_days(roster, 'valid', scfg['days']))
    for t in truth['shifted']:
        summary = artifact['references'][t['dataset']][t['metric']]['summary']
        if not summary['scale']:
            raise ValueError('Synthetic reference scale unavailable; cannot plant a known shift')
        own = unshifted[unshifted.subject_id.eq(t['subject_id']) & unshifted.metric.eq(t['metric']) & unshifted.status.eq('ok')]
        t.update(scale=summary['scale'], shift_value=t['sd_units'] * summary['scale'],
                 unshifted_median_sd=float(((own.value - summary['median']) / summary['scale']).median()))
        rows = hourly.subject_id.eq(t['subject_id'])
        hourly.loc[rows, 'enmo_median'] += t['shift_value']
    for split in ['valid', 'test']:
        _write_split_bundle(bundles[split], hourly, roster, split, scfg['days'], cfg, provenance)
    # Persist the fixture so a later run can reuse the SAME baseline file (e.g. a separate test-unlock run).
    hourly.to_csv(workdir / 'hourly.csv', index=False)
    roster.to_csv(workdir / 'roster.csv', index=False)
    write_json(workdir / 'fixture.json', dict(truth=truth, days=scfg['days'], provenance=provenance))
    return _synthetic_inputs(workdir, hourly, roster, truth, scfg['days'], provenance)


def load_synthetic(workdir):
    """Reload a synthetic fixture written by build_synthetic: no rebuild and no refit, so the baseline SHA is kept."""
    workdir = Path(workdir)
    if not (workdir / 'fixture.json').exists():
        raise FileNotFoundError(f'No synthetic fixture at {workdir}; run the source run first')
    meta = json.loads((workdir / 'fixture.json').read_text(encoding='utf-8'))
    return _synthetic_inputs(workdir, pd.read_csv(workdir / 'hourly.csv'), pd.read_csv(workdir / 'roster.csv'),
                             meta['truth'], meta['days'], meta['provenance'])


def _synthetic_inputs(workdir, hourly, roster, truth, days, provenance):
    bundles = {s: workdir / 'bundles' / s for s in ['train', 'valid', 'test']}
    baseline = workdir / 'model/baseline.json'
    artifact = load_reference(baseline)
    train_daily, *_ = load_bundle(bundles['train'])
    valid_daily, *_ = load_bundle(bundles['valid'])
    expected = {(r['dataset'], r['subject_id'], r['day']) for r in _expected_days(roster, 'valid', days)}
    return dict(mode='synthetic', artifact=artifact, baseline_path=baseline, cfg=artifact['config'],
                train_daily=train_daily, valid_daily=valid_daily, test_bundle=bundles['test'], test_plan=None,
                hourly={s: hourly[hourly.split.eq(s)].reset_index(drop=True) for s in ['train', 'valid']},
                roster=roster, provenance=provenance, truth=truth, days=days,
                sources=_sources(baseline, {s: bundles[s] for s in ['train', 'valid']}), unavailable=[],
                bundles={s: bundles[s] for s in ['train', 'valid']}, official={}, expected_days={'valid': expected},
                # The fixture baseline is fitted while building inputs; validation itself never fits.
                fit_called_during_validation=False, fixture_fitted=True, approved_sha=sha256(baseline))


# ---------------------------------------------------------------------------
# Real
# ---------------------------------------------------------------------------
PATH_KEYS = ['train_bundle', 'valid_bundle', 'baseline', 'test_plan', 'test_bundle', 'report_dir', 'fit_report']


def real_paths(bcfg):
    """checkpoint: null selects the full-run layout (no checkpoints/<name>/), where fit writes the test plan."""
    r = bcfg['real']

    def resolve(template):
        if not r.get('checkpoint'):
            template = template.replace('/checkpoints/{checkpoint}', '')
        return template.format(run_id=r['run_id'], checkpoint=r.get('checkpoint'))
    return {k: (resolve(str(r[k])) if r.get(k) else None) for k in PATH_KEYS}


class BaselineNotApproved(ValueError):
    pass


def check_approved(path, approved_sha):
    """Refuse to read a baseline whose file SHA256 differs from the approved (sent) one."""
    actual = sha256(path)
    if not approved_sha or actual != approved_sha:
        raise BaselineNotApproved(f'Baseline SHA256 {actual} != approved {approved_sha}; nothing was read')
    return actual


def _pull(rel, dest):
    from src import storage
    pipeline = pipeline_config()
    storage.configure(pipeline['storage'])
    remote = f"{storage.s3_root(pipeline).rstrip('/')}/{rel}"
    fs = storage.fs_of(remote)
    key = remote.split('://', 1)[1]
    if not fs.exists(key):
        return False
    files = fs.find(key) if fs.isdir(key) else [key]
    for f in files:
        sub = f[len(key):].lstrip('/')
        storage.download('s3://' + f, dest / sub if sub else dest)
    return True


def materialize(rel, workspace, pull=False, data_root=None):
    """data_root (e.g. the git worktree of a long real run) > local project copy > backtest workspace copy > S3 pull."""
    if not rel:
        return None
    roots = [Path(data_root)] if data_root else []
    for candidate in [r / rel for r in roots] + [ROOT / rel, ROOT / workspace / 'real' / rel]:
        if candidate.exists():
            return candidate
    dest = ROOT / workspace / 'real' / rel
    if pull and _pull(rel, dest):
        return dest
    return None


def load_real(bcfg, pull=False):
    rel = real_paths(bcfg)
    ws = bcfg['paths']['workspace']
    # The test bundle is only located locally, never pulled: test contents must not be downloaded before unlock.
    root = bcfg['real'].get('data_root')
    found = {k: materialize(v, ws, pull and k != 'test_bundle', root) for k, v in rel.items() if k not in ('report_dir',)}
    if found['baseline'] is None:
        raise FileNotFoundError(f"Real baseline not found: {rel['baseline']}. Run with --pull "
                                "(after `aws sso login --profile minny`) or copy it into the workspace.")
    approved = check_approved(found['baseline'], bcfg['real'].get('approved_baseline_sha256'))
    artifact = load_reference(found['baseline'])
    if artifact['data_kind'] != 'real':
        raise ValueError('Real mode requires a real baseline artifact')
    daily, unavailable = {}, []
    for split in ['train', 'valid']:
        path = found[f'{split}_bundle']
        if path is None:
            daily[split] = None
            unavailable.append(dict(input=f'{split}_bundle', path=rel[f'{split}_bundle'], status='unavailable'))
            continue
        d, _, cfg, _ = load_bundle(path)
        if fingerprint(cfg) != artifact['config_hash'] or not d.split.eq(split).all():
            raise ValueError(f'{split} bundle does not match the baseline config or split')
        daily[split] = d
    if found['test_bundle'] is None:
        unavailable.append(dict(input='test_bundle', path=rel['test_bundle'], status='blocked_no_test_features'))
    official = {}
    for name in bcfg['real'].get('official_reports', []):
        path = materialize(f"{rel['report_dir']}/{name}", ws, pull, root) if rel['report_dir'] else None
        if path is not None:
            official[name] = json.loads(path.read_text(encoding='utf-8'))
    if found['fit_report'] is not None:
        official['fit_report.json'] = json.loads(found['fit_report'].read_text(encoding='utf-8'))
    from backtest.runners.validation_audit import expected_days_from_manifest
    return dict(mode='real', artifact=artifact, baseline_path=found['baseline'], cfg=artifact['config'],
                train_daily=daily['train'], valid_daily=daily['valid'], test_bundle=found['test_bundle'],
                test_plan=found['test_plan'], hourly={}, roster=pd.DataFrame(artifact['roster']),
                provenance=artifact['provenance'], truth=None, days=None,
                sources=_sources(found['baseline'], {s: found[f'{s}_bundle'] for s in ['train', 'valid']}),
                unavailable=unavailable, bundles={s: found[f'{s}_bundle'] for s in ['train', 'valid']},
                official=official, expected_days={'valid': expected_days_from_manifest(found['valid_bundle'])},
                fit_called_during_validation=False, fixture_fitted=False, approved_sha=approved)


def _sources(baseline, bundles):
    out = dict(baseline=dict(path=str(baseline), sha256=sha256(baseline)))
    for split, path in bundles.items():
        if path is not None:
            out[f'{split}_bundle'] = dict(path=str(path), manifest_sha256=sha256(Path(path) / 'bundle.manifest.json'))
    return out


def load_inputs(mode, bcfg, workdir, pull=False, synthetic_from=None):
    if mode == 'synthetic':
        return load_synthetic(synthetic_from) if synthetic_from else build_synthetic(bcfg, workdir)
    if mode == 'real':
        return load_real(bcfg, pull)
    raise ValueError(f'Unknown mode: {mode}')
