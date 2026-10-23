"""Validation audit and AWS-vs-local comparison on synthetic data only (no S3, no AWS)."""
import copy
import io
import json
import tarfile

import numpy as np
import pytest

from src.reference.common import fingerprint, load_config, sha256
from src.reference.daily import aggregate_daily
from src.reference.io import load_bundle, write_bundle
from src.reference.output import build_analysis
from tools.reference_baseline import fit_bundle

from backtest import aws_compare
from backtest.runners import data_loader
from backtest.runners.data_loader import BaselineNotApproved, build_synthetic, load_backtest_config, load_real, pipeline_config
from backtest.runners.validation_audit import run_audit

FAST = {'stability': {'n_bootstrap': 20}, 'symptom': {'n_bootstrap': 20}}
TRACKS = ['valid', 'audit']


@pytest.fixture(scope='module')
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp('audit')
    inputs = build_synthetic(load_backtest_config(overrides=FAST), root / 'ws')
    return dict(inputs=inputs, analysis=build_analysis(inputs['valid_daily'], inputs['artifact']), root=root)


def audit(env, analysis=None, **override):
    inputs = dict(env['inputs'], **override)
    return run_audit(inputs, analysis or env['analysis'], load_backtest_config(), TRACKS, inputs['approved_sha'])


def failed(result):
    return {k for k, v in result['categories'].items() if v['status'] == 'fail'}


def test_clean_audit_passes_and_exercises_every_case(env):
    result = audit(env)
    assert failed(result) == set()
    assert {k for k, v in result['categories'].items() if v['status'] == 'pass'} >= {
        'baseline_lock', 'hash_chain', 'reference_formulas', 'distances', 'null_handling', 'previous_day', 'coverage', 'test_lock'}
    assert all(c['status'] == 'pass' for c in result['null_cases'].values()), result['null_cases']
    assert all(c['status'] == 'exercised' for c in result['previous_day_cases'].values()), result['previous_day_cases']
    assert result['fit_called_during_validation'] is False
    assert result['sha_after'] == result['baseline_sha256']


def _tamper_artifact(artifact, fn):
    a = copy.deepcopy(artifact)
    fn(a)
    return a


@pytest.mark.parametrize('name, fn, category', [
    ('summary median', lambda a: a['references']['cops']['enmo_hourly_median_mean_g']['summary'].update(median=1.), 'reference_formulas'),
    ('summary quantile', lambda a: a['references']['nhanes']['enmo_hourly_median_mean_g']['summary']['quantiles'].update({'0.95': 9.}), 'reference_formulas'),
    ('entries vs train daily', lambda a: a['references']['cops']['rest_tremor_window_fraction']['entries'][0].update(value=.5), 'hash_chain'),
    ('config hash', lambda a: a.update(config_hash='0' * 64), 'hash_chain'),
    ('roster hash', lambda a: a.update(roster_hash='0' * 64), 'hash_chain'),
])
def test_artifact_tampering_is_caught(env, name, fn, category):
    assert category in failed(audit(env, artifact=_tamper_artifact(env['inputs']['artifact'], fn)))


def _tamper_analysis(env, fn):
    doc = copy.deepcopy(env['analysis'])
    fn(doc['analysis'])
    return doc


def _first(rows, **match):
    return next(r for r in rows if all(r[k] == v if not callable(v) else v(r[k]) for k, v in match.items()))


def test_wrong_standardized_deviation_is_caught(env):
    def fn(rows):
        c = _first(rows, status='ok')['reference_comparisons']['cops']
        c['standardized_deviation'] += 1e-6
    assert 'distances' in failed(audit(env, _tamper_analysis(env, fn)))


def test_zero_in_place_of_null_is_caught(env):
    def fn(rows):
        _first(rows, status='insufficient_data')['value'] = 0.
    assert 'null_handling' in failed(audit(env, _tamper_analysis(env, fn)))


def test_previous_day_rule_order_is_enforced(env):
    # Day after a missing day with insufficient current data must be previous_day_missing (rule 2 before rule 3).
    def fn(rows):
        r = _first(rows, previous_day=lambda p: p['status'] == 'previous_day_missing')
        r['previous_day'].update(status='current_day_insufficient_data', reason='current_day_insufficient_data')
    assert 'previous_day' in failed(audit(env, _tamper_analysis(env, fn)))


def test_coverage_tampering_is_caught(env):
    daily = env['inputs']['valid_daily']
    minutes = daily.copy()
    minutes.loc[minutes.index[0], 'day_valid_minutes'] += 30
    assert 'coverage' in failed(audit(env, valid_daily=minutes))
    status = daily.copy()
    i = status.index[status.status.eq('ok')][0]
    status.loc[i, ['status', 'reasons']] = ['insufficient_data', 'metric_coverage_below_minimum']
    assert 'coverage' in failed(audit(env, valid_daily=status))
    dropped = daily[daily.day.ne(daily.day.max())]
    analysis = build_analysis(dropped, env['inputs']['artifact'])
    result = audit(env, analysis, valid_daily=dropped)
    assert any(e.get('check') == 'expected_qc_days_one_to_one' for e in result['categories']['coverage']['examples'])


def test_unapproved_baseline_is_not_read(env, monkeypatch, tmp_path):
    monkeypatch.setattr(data_loader, 'load_reference', lambda *_: pytest.fail('baseline was read'))
    cfg = load_backtest_config(overrides={'real': {'baseline': str(env['inputs']['baseline_path']), 'approved_baseline_sha256': '0' * 64,
                                                   'report_dir': None, 'fit_report': None}, 'paths': {'workspace': str(tmp_path)}})
    with pytest.raises(BaselineNotApproved):
        load_real(cfg)


def test_validation_never_fits(env, monkeypatch):
    import src.reference.model as model
    import tools.reference_baseline as rb
    for mod, name in [(model, 'fit_reference'), (rb, 'fit_bundle'), (data_loader, 'fit_bundle')]:
        monkeypatch.setattr(mod, name, lambda *a, **k: pytest.fail('fit called during validation'))
    analysis = build_analysis(env['inputs']['valid_daily'], env['inputs']['artifact'])
    assert failed(audit(env, analysis)) == set()


# ---------------------------------------------------------------------------
# AWS vs local
# ---------------------------------------------------------------------------
def _tar(path, members):
    with tarfile.open(path, 'w:gz') as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == 'sym':
                info.type, info.linkname = tarfile.SYMTYPE, '/etc/passwd'
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


def _rewrite(artifact, path, fn):
    a = copy.deepcopy(artifact)
    a.pop('artifact_id')
    fn(a)
    a['artifact_id'] = fingerprint(a)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(a), encoding='utf-8')
    return path


def _train_subset_bundle(env, out, drop_subject=None, cfg=None):
    inputs = env['inputs']
    cfg = cfg or load_config()
    hourly = inputs['hourly']['train']
    if drop_subject:
        hourly = hourly[hourly.subject_id.ne(drop_subject)]
    prov = dict(inputs['provenance'], config_hash=fingerprint(cfg))
    daily, qc = aggregate_daily(hourly, inputs['roster'], cfg, prov)
    write_bundle(out, daily, qc, inputs['roster'], cfg, pipeline_config(), prov)
    return out


def compare(env, tmp_path, local, aws_file, validate_calls=None, **kw):
    def fake_validate(run_id, baseline, sha, train_bundle, valid_bundle):
        validate_calls.append(dict(sha=sha, baseline=str(baseline)))
        return dict(run_id=run_id)
    return aws_compare.run_compare(aws_file, sha256(aws_file), run_id=tmp_path.name, local_baseline=local,
                                   local_approved_sha=sha256(local), valid_bundle=env['inputs']['bundles']['valid'],
                                   outputs_root=tmp_path / 'out', workspace_root=tmp_path / 'ws',
                                   validate_fn=fake_validate, **kw)[0]


def test_aws_refit_same_inputs_matches(env, tmp_path):
    fit_bundle(env['inputs']['bundles']['train'], tmp_path / 'aws_model')
    tar = _tar(tmp_path / 'model.tar.gz', [('baseline.json', (tmp_path / 'aws_model/baseline.json').read_bytes(), 'file')])
    doc = compare(env, tmp_path, env['inputs']['baseline_path'], tar)
    assert doc['comparison']['category'] == 'same_inputs_match'
    assert doc['aws']['baseline_sha256'] != doc['local']['baseline_sha256']  # created_utc differs
    assert doc['validation']['status'] == 'reuse_local_validation'
    registry = json.loads((tmp_path / 'out/baseline_registry.json').read_text(encoding='utf-8'))
    assert registry[doc['aws']['baseline_sha256']]['equivalent_to'] == doc['local']['baseline_sha256']


def test_more_train_is_new_version_and_revalidated(env, tmp_path):
    local_dir = fit_bundle(_train_subset_bundle(env, tmp_path / 'b_local', drop_subject='SYNTHETIC-COPS-TRAIN-08'), tmp_path / 'm_local')
    calls = []
    doc = compare(env, tmp_path, tmp_path / 'm_local/baseline.json', env['inputs']['baseline_path'], calls)
    assert doc['comparison']['category'] == 'new_version_more_train'
    assert doc['comparison']['new_subjects'] == [['cops', 'SYNTHETIC-COPS-TRAIN-08']]
    assert doc['validation']['status'] == 'revalidated' and calls[0]['sha'] == doc['aws']['baseline_sha256']
    assert local_dir['artifact_id'] != doc['aws']['artifact_id']


def test_baseline_version_only_blocks_reuse_of_valid_bundle(env, tmp_path):
    fit_bundle(_train_subset_bundle(env, tmp_path / 'b_local', drop_subject='SYNTHETIC-COPS-TRAIN-08'), tmp_path / 'm_local')
    cfg2 = dict(load_config(), baseline_version='real-cohort-reference-OTHER-RUN')
    fit_bundle(_train_subset_bundle(env, tmp_path / 'b_aws', cfg=cfg2), tmp_path / 'm_aws')
    calls = []
    doc = compare(env, tmp_path, tmp_path / 'm_local/baseline.json', tmp_path / 'm_aws/baseline.json', calls)
    c = doc['comparison']
    assert c['core']['policy_config_hash']['equal'] and not c['exact_config_hash']['equal']
    assert c['category'] == 'new_version_more_train'
    assert doc['validation']['status'] == 'blocked_valid_bundle_config_hash_mismatch' and not calls


@pytest.mark.parametrize('delta, kind', [(lambda v: v + .01, 'value_changed'), (lambda v: float(np.nextafter(v, 1.)), 'float_roundoff_only')])
def test_changed_value_for_same_subject_day_is_mismatch(env, tmp_path, delta, kind):
    def fn(a):
        e = a['references']['cops']['enmo_hourly_median_mean_g']['entries'][0]
        e['value'] = delta(e['value'])
    aws = _rewrite(env['inputs']['artifact'], tmp_path / 'aws/baseline.json', fn)
    doc = compare(env, tmp_path, env['inputs']['baseline_path'], aws)
    assert doc['comparison']['category'] == 'same_inputs_mismatch'
    assert doc['comparison']['mismatch_kind'] == kind
    assert doc['validation']['status'] == 'blocked'


@pytest.mark.parametrize('members', [
    [('../baseline.json', b'{}', 'file')],
    [('/abs/baseline.json', b'{}', 'file')],
    [('baseline.json', b'', 'sym')],
    [('other.txt', b'x', 'file')],
])
def test_unsafe_or_empty_archives_are_rejected(tmp_path, members):
    with pytest.raises(ValueError):
        aws_compare.safe_extract(_tar(tmp_path / 'bad.tar.gz', members), tmp_path / 'x')
    assert not (tmp_path.parent / 'baseline.json').exists()


def test_wrong_handed_over_sha_is_rejected(env, tmp_path):
    with pytest.raises(ValueError, match='SHA256'):
        aws_compare.fetch(env['inputs']['baseline_path'], '0' * 64, tmp_path)
