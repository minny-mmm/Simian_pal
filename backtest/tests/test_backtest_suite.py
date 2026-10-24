"""Backtest suite on synthetic data only; never touches S3 or project data/outputs."""
import json

import numpy as np
import pandas as pd
import pytest

from src.reference.common import ROOT, fingerprint, load_config, sha256
from src.reference.output import build_analysis

from backtest.evaluators.metrics import (average_precision, balanced_accuracy, snapshot_diff, tree_snapshot)
from backtest.evaluators.report_builder import update_registry
from backtest.run import run
from backtest.runners import reference_backtest as ref
from backtest.runners import symptom_cv_backtest as symptom
from backtest.runners import temporal_walkforward as walk
from backtest.runners.data_loader import build_synthetic, load_backtest_config, load_real

FAST = {'stability': {'n_bootstrap': 30}, 'symptom': {'n_bootstrap': 30}}


@pytest.fixture(scope='module')
def synthetic_run(tmp_path_factory):
    root = tmp_path_factory.mktemp('bt')
    results, out = run('synthetic', 'suite', outputs_root=root / 'out', workspace_root=root / 'ws', overrides=FAST)
    return results, out, root


@pytest.fixture
def inputs(tmp_path):
    return build_synthetic(load_backtest_config(overrides=FAST), tmp_path / 'ws')


def gates(results):
    return {g['gate']: g for g in results['gates']}


def test_full_run_writes_reports_and_manifest(synthetic_run):
    results, out, _ = synthetic_run
    for name in ['results.json', 'summary.md', 'dashboard.html', 'run_manifest.json', 'analysis_VALID_SYNTHETIC.json',
                 'evaluability.csv', 'gates.csv', 'jackknife.csv', 'valid_rows.csv']:
        assert (out / name).exists(), name
    manifest = json.loads((out / 'run_manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'completed'
    assert manifest['gates_fingerprint'] == fingerprint(load_backtest_config()['gates'])
    page = (out / 'dashboard.html').read_text(encoding='utf-8')
    assert '<svg' in page and 'http://' not in page and 'https://' not in page
    assert results['data_kind'] == 'synthetic'


def test_core_gates_pass_on_synthetic(synthetic_run):
    g = gates(synthetic_run[0])
    for name in ['baseline_unchanged', 'protected_paths_unchanged', 'known_answer', 'no_lookahead[train]',
                 'no_lookahead[valid]', 'no_lookahead[train_hourly]', 'no_lookahead[valid_hourly]',
                 'schema_and_semantic_validation']:
        assert g[name]['status'] == 'pass', name
    # zero_scale reference cannot be bootstrapped/standardized: reported, not faked.
    assert g['bootstrap_median_ci_width[nhanes/rest_tremor_window_fraction]']['status'] == 'not_evaluable'


def test_known_answer_recovers_planted_shift(synthetic_run):
    known = synthetic_run[0]['tracks']['valid']['known_answer']
    shifts = [k for k in known if 'recovered_increment_sd' in k]
    assert len(shifts) == 2
    assert all(abs(k['recovered_increment_sd'] - k['sd_units']) < 1e-9 for k in shifts)
    assert {k.get('check') for k in known} >= {'zero_scale_has_null_standardization', 'no_walking_windows_is_null',
                                               'day_after_missing_day_is_previous_day_missing'}


def test_test_track_not_run_by_default_and_caveat_present(synthetic_run):
    results = synthetic_run[0]
    assert 'test' not in results['tracks']
    assert 'device' in results['caveat']
    for gap in results['tracks']['valid']['device_confound']['gaps']:
        assert set(gap) >= {'reference_gap_in_pooled_scale', 'signal_type'}
        assert not any('auc' in k or 'accuracy' in k for k in gap)


def test_run_id_is_never_overwritten(synthetic_run):
    _, _, root = synthetic_run
    with pytest.raises(ValueError, match='never overwrite'):
        run('synthetic', 'suite', outputs_root=root / 'out', workspace_root=root / 'ws2', overrides=FAST)


def _fake_artifact(n_subjects, days=3, min_participants=5):
    policy = dict(min_participants=min_participants, min_days=10, min_days_per_participant=2,
                  quantiles=[.05, .25, .5, .75, .95], mad_scale_factor=1.4826)
    entries = [dict(subject_id=f'S{i}', day=d, value=float(i + .1 * d)) for i in range(n_subjects) for d in range(days)]
    metrics = {m: dict(entries=entries) for m in ['enmo_hourly_median_mean_g', 'walking_cadence_hourly_median_mean_hz',
                                                  'rest_tremor_window_fraction']}
    return dict(config=dict(reference_policy=policy), references={'cops': metrics, 'nhanes': metrics})


def test_bootstrap_relabels_duplicate_subjects():
    rows = ref.bootstrap(_fake_artifact(5), n=40, seed=1)
    # Without relabeling, repeated subjects merge and 5 draws almost never give 5 distinct people.
    assert all(r['n_insufficient'] == 0 for r in rows)
    assert all(r['median_ci_low'] <= r['full_median'] <= r['median_ci_high'] for r in rows)


def test_jackknife_with_minimum_cohort_is_insufficient_and_sensitivity_is_memory_only():
    art = _fake_artifact(5)
    assert all(r['status'] == 'insufficient_data' for r in ref.jackknife(art))
    before = fingerprint(load_config())
    rows = ref.sensitivity(art, [4, 5])
    assert fingerprint(load_config()) == before
    assert all(r['label'] == 'sensitivity_only_not_production' for r in rows)
    assert {r['loo_insufficient'] for r in rows if r['min_participants'] == 4} == {0}
    assert {r['loo_insufficient'] for r in rows if r['min_participants'] == 5} == {5}


def test_evaluability_reports_locked_test_and_levels(inputs):
    rows = ref.evaluability(inputs, load_backtest_config())
    assert len(rows) == 6 and all(r['test_level'] == 'available_locked' for r in rows)
    nh = next(r for r in rows if r['dataset'] == 'nhanes' and r['metric'] == 'rest_tremor_window_fraction')
    assert nh['reference_status'] == 'zero_scale' and not nh['bootstrap_evaluable']


def test_no_lookahead_detects_a_leaking_analysis(inputs):
    def leaking(daily, artifact):
        doc = build_analysis(daily, artifact)
        for r in doc['analysis']:
            r['n_days_seen'] = int(daily.day.max())  # depends on future days
        return doc
    valid = inputs['valid_daily']
    assert walk.no_lookahead(valid, inputs['artifact'])['n_mismatches'] == 0
    assert walk.no_lookahead(valid, inputs['artifact'], analyze=leaking)['n_mismatches'] > 0


def test_valid_analysis_keeps_baseline_hash(inputs):
    before = sha256(inputs['baseline_path'])
    _, check = ref.run_analysis(inputs['valid_daily'], inputs['artifact'], inputs['baseline_path'])
    assert check['unchanged'] and check['baseline_sha256_before'] == before


def test_test_gate(inputs, tmp_path):
    cfg = load_backtest_config(overrides=FAST)
    sha = sha256(inputs['baseline_path'])
    with pytest.raises(ref.TestLocked, match='allow_test_split'):
        ref.run_test(inputs, cfg, tmp_path, unlock=True, approved_sha=sha)
    cfg['test']['allow_test_split'] = True
    with pytest.raises(ref.TestLocked):
        ref.run_test(inputs, cfg, tmp_path, unlock=False, approved_sha=sha)
    with pytest.raises(ref.TestLocked, match='SHA256'):
        ref.run_test(inputs, cfg, tmp_path, unlock=True, approved_sha='0' * 64)
    with pytest.raises(ref.TestNotReady, match='no validation run'):
        ref.run_test(inputs, cfg, tmp_path, unlock=True, approved_sha=sha)
    update_registry(tmp_path, sha, name='synthetic_registry.json', validation_run=dict(
        run_id='v', overall='pass', audit_pass=True, valid_rows=10, gates_fingerprint=fingerprint(cfg['gates']),
        ready_cells=[dict(dataset='cops', metric='enmo_hourly_median_mean_g', ready=True, reasons=[])]))
    blocked, doc = ref.run_test(dict(inputs, test_bundle=None), cfg, tmp_path, unlock=True, approved_sha=sha)
    assert blocked['status'] == 'blocked_no_test_features' and doc is None
    assert not (tmp_path / '_test_opened').exists()
    status, doc = ref.run_test(inputs, cfg, tmp_path, unlock=True, approved_sha=sha)
    assert status['status'] == 'compared_once' and status['unchanged']
    assert {r['split'] for r in doc['analysis']} == {'test'}
    with pytest.raises(ref.TestLocked, match='already opened'):
        ref.run_test(inputs, cfg, tmp_path, unlock=True, approved_sha=sha)


def test_real_mode_without_inputs_fails_clearly(tmp_path):
    cfg = load_backtest_config(overrides={'real': {'run_id': 'no-such-run'}, 'paths': {'workspace': str(tmp_path)}})
    with pytest.raises(FileNotFoundError, match='--pull'):
        load_real(cfg, pull=False)


def test_symptom_cv_rules():
    scfg = load_backtest_config()['symptom']
    table = symptom.synthetic_symptom_table(3, n_train=8, n_valid=3, days=2)
    cols = symptom.feature_columns(table, scfg)
    assert not set(cols) & set(scfg['exclude_columns'])
    folds = symptom.group_kfold(table.subject_id.to_numpy(), 5)
    for tr, te in folds:
        assert not set(table.subject_id.iloc[tr]) & set(table.subject_id.iloc[te])
    result = symptom.run_symptom(table, dict(scfg, n_bootstrap=20))
    assert result['status'] == 'ok'
    assert result['cv']['n_subjects'] == 8 and result['valid']['status'] == 'ok'
    assert result['cv']['pr_auc'] > result['cv']['dummy']['pr_auc']


def test_preprocessor_imputes_train_median_never_zero():
    X = np.array([[1., np.nan], [3., np.nan], [np.nan, np.nan], [5., np.nan]])
    prep = symptom.Preprocessor().fit(X)
    assert prep.median.tolist() == [3.]  # all-NaN column dropped, not filled
    z = prep.transform(np.array([[np.nan, 7.], [3., 7.]]))
    assert z[0, 0] == z[1, 0]  # missing value placed at train median
    assert z[0, 1] != z[1, 1]  # missing indicator still distinguishes it


def test_metric_hand_values():
    assert average_precision([1, 0, 1, 0], [.9, .8, .7, .1]) == pytest.approx((1 + 2 / 3) / 2)
    assert balanced_accuracy([0, 0, 1, 1], [0, 1, 1, 1]) == pytest.approx(.75)
    assert balanced_accuracy([1, 1], [1, 0]) is None


def test_tree_snapshot_detects_changes(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/a.py').write_text('x = 1')
    before = tree_snapshot(tmp_path, ['src'])
    (tmp_path / 'src/a.py').write_text('x = 2')
    assert snapshot_diff(before, tree_snapshot(tmp_path, ['src'])) == ['src/a.py']


def test_project_paths_untouched_by_suite(synthetic_run):
    assert synthetic_run[0]['protected_paths_unchanged'] is True
    assert not (ROOT / 'backtest/outputs/suite').exists()
