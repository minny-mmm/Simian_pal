"""Test-split hygiene: nothing pulls/opens test before unlock, readiness gate, test bundle identity, prepare_test locks,
and the synthetic train -> valid -> test flow on ONE baseline file. Synthetic/tmp data only; never touches S3."""
import json

import pandas as pd
import pytest

from src.reference.common import fingerprint, sha256
from src.reference.io import load_bundle

from backtest import prepare_symptom as symptom_prep
from backtest import prepare_test as prep
from backtest.evaluators.report_builder import update_registry
from backtest.run import main as run_main
from backtest.run import run
from backtest.runners import data_loader
from backtest.runners import reference_backtest as ref
from backtest.runners import symptom_cv_backtest as symptom
from backtest.runners.data_loader import build_synthetic, load_backtest_config, load_real, real_paths

FAST = {'stability': {'n_bootstrap': 30}, 'symptom': {'n_bootstrap': 30}}
VALIDATION_TRACKS = ['stability', 'loo', 'walkforward', 'valid', 'audit']
UNLOCK = dict(FAST, test={'allow_test_split': True})
CELL = dict(dataset='cops', metric='enmo_hourly_median_mean_g', ready=True, reasons=[])


def _record(cfg, **kw):
    return dict(dict(run_id='v1', overall='pass', audit_pass=True, valid_rows=10,
                     gates_fingerprint=fingerprint(cfg['gates']), ready_cells=[CELL]), **kw)


@pytest.fixture
def inputs(tmp_path):
    return build_synthetic(load_backtest_config(overrides=FAST), tmp_path / 'ws')


# ---------------------------------------------------------------------------
# Nothing reaches test data before unlock
# ---------------------------------------------------------------------------
def test_real_paths_full_run_layout():
    cfg = load_backtest_config(overrides={'real': {'checkpoint': None}})
    p = real_paths(cfg)
    assert p['baseline'] == f"data/models/reference-v1/{cfg['real']['run_id']}/baseline.json"
    assert p['test_plan'] == f"reports/reference_baseline/{cfg['real']['run_id']}/test_plan_NOT_EXECUTED.json"
    checkpoint = real_paths(load_backtest_config(overrides={'real': {'checkpoint': 'ckpt-x'}}))['train_bundle']
    assert 'checkpoints' not in p['train_bundle'] and checkpoint.endswith('checkpoints/ckpt-x/train')


def test_materialize_prefers_data_root(tmp_path):
    rel = 'data/models/x/baseline.json'
    assert data_loader.materialize(rel, 'backtest/workspace', data_root=tmp_path) is None
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_text('{}')
    assert data_loader.materialize(rel, 'backtest/workspace', data_root=tmp_path) == tmp_path / rel


def test_load_real_never_pulls_test_bundle(monkeypatch, tmp_path):
    calls = {}
    monkeypatch.setattr(data_loader, 'materialize', lambda rel, ws, pull=False, root=None: calls.setdefault(rel, pull) and None)
    cfg = load_backtest_config(overrides={'real': {'test_bundle': 'data/daily/x/test'}, 'paths': {'workspace': str(tmp_path)}})
    with pytest.raises(FileNotFoundError):
        load_real(cfg, pull=True)
    assert calls['data/daily/x/test'] is False
    assert calls[real_paths(cfg)['baseline']] is True


def test_symptom_loader_never_opens_test_subject_files(monkeypatch, tmp_path):
    (tmp_path / 'f').mkdir()
    for sid in ['COPS-A', 'COPS-B', 'COPS-T']:
        pd.DataFrame(dict(subject_id=sid, dataset='cops', day=[0, 0], hour=[9, 10], tremor_frac_all=[.1, .2])).to_csv(
            tmp_path / 'f' / f'{sid}.csv', index=False)
    pd.DataFrame(dict(subject_id=['COPS-A', 'COPS-B', 'COPS-T'], day=0, hour=9, tremor_score=[0, 1, 2])).to_csv(
        tmp_path / 'labels.csv', index=False)
    roster = pd.DataFrame(dict(dataset='cops', subject_id=['COPS-A', 'COPS-B', 'COPS-T'], split=['train', 'valid', 'test']))
    opened, real_read = [], pd.read_csv
    monkeypatch.setattr(symptom.pd, 'read_csv', lambda p, *a, **k: opened.append(str(p)) or real_read(p, *a, **k))
    scfg = dict(load_backtest_config()['symptom'], features_glob='f/*.csv', labels_path='labels.csv')
    table, note = symptom.load_real_symptom_table(scfg, roster, root=tmp_path)
    assert not any('COPS-T' in p for p in opened)
    assert set(table.subject_id) == {'COPS-A', 'COPS-B'} and '1 other files not opened' in note


# ---------------------------------------------------------------------------
# Readiness + test bundle identity + once per baseline
# ---------------------------------------------------------------------------
def test_ready_cells_require_pass_not_unevaluable():
    ev = [dict(dataset='cops', metric='m', reference_status='ok', valid_level='distribution'),
          dict(dataset='nhanes', metric='m', reference_status='ok', valid_level='descriptive_only'),
          dict(dataset='nhanes', metric='z', reference_status='zero_scale', valid_level='distribution')]
    gates = [dict(gate='jackknife_median_shift[cops/m]', status='pass'), dict(gate='bootstrap_median_ci_width[cops/m]', status='pass'),
             dict(gate='jackknife_median_shift[nhanes/m]', status='not_evaluable'), dict(gate='bootstrap_median_ci_width[nhanes/m]', status='pass')]
    cells = {c['dataset']: c for c in ref.ready_cells(ev, gates)}
    assert set(cells) == {'cops', 'nhanes'}  # zero_scale reference is not comparable -> not a cell
    assert cells['cops']['ready'] and not cells['nhanes']['ready']
    assert cells['nhanes']['reasons'] == ['jackknife_median_shift=not_evaluable', 'valid_level=descriptive_only']


@pytest.mark.parametrize('change, reason', [
    (dict(overall='fail'), 'overall=fail'),
    (dict(audit_pass=False), 'validation_audit_not_passed'),
    (dict(gates_fingerprint='x'), 'gates_fingerprint_differs_or_missing'),
    (dict(ready_cells=[]), 'no_comparable_cells_recorded'),
    (dict(ready_cells=[dict(CELL, ready=False, reasons=['valid_level=descriptive_only'])]), 'descriptive_only'),
])
def test_readiness_rejects(tmp_path, change, reason):
    cfg = load_backtest_config()
    update_registry(tmp_path, 'sha', validation_run=_record(cfg, **change))
    r = ref.readiness_for_test(tmp_path, 'real', 'sha', fingerprint(cfg['gates']))
    assert not r['ready'] and reason in json.dumps(r['reasons'])
    update_registry(tmp_path, 'sha', validation_run=_record(cfg, run_id='v2'))
    assert ref.readiness_for_test(tmp_path, 'real', 'sha', fingerprint(cfg['gates']))['validation_run'] == 'v2'
    # Real readiness never comes from the synthetic registry.
    assert not ref.readiness_for_test(tmp_path, 'synthetic', 'sha', fingerprint(cfg['gates']))['ready']


def test_real_checkpoint_baseline_cannot_open_test(inputs, tmp_path):
    cfg = load_backtest_config(overrides=UNLOCK)
    with pytest.raises(ref.TestLocked, match='full-train fit'):
        ref.run_test(dict(inputs, mode='real', test_plan=None), cfg, tmp_path, True, sha256(inputs['baseline_path']))


def test_check_test_bundle_identity(inputs):
    daily, _, cfg, _ = load_bundle(inputs['test_bundle'])
    art, roster = inputs['artifact'], inputs['roster']
    ref.check_test_bundle(daily, cfg, art, roster)
    with pytest.raises(ValueError, match='config_hash'):
        ref.check_test_bundle(daily, dict(cfg, baseline_version='other'), art, roster)
    with pytest.raises(ValueError, match='preprocessing_version'):
        ref.check_test_bundle(daily.assign(preprocessing_version='x'), cfg, art, roster)
    with pytest.raises(ValueError, match='test_plan'):
        ref.check_test_bundle(daily, cfg, art, roster, plan_subjects=[dict(subject_id='SOMEONE-ELSE')])
    with pytest.raises(ValueError, match='roster test split'):
        ref.check_test_bundle(daily, cfg, art, roster.assign(split='train'))


def test_failed_test_attempt_still_counts_as_opened(inputs, tmp_path):
    cfg = load_backtest_config(overrides=UNLOCK)
    sha = sha256(inputs['baseline_path'])
    update_registry(tmp_path, sha, name='synthetic_registry.json', validation_run=_record(cfg))
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps(dict(locked_baseline_sha256=sha, subjects=[dict(subject_id='NOT-IN-BUNDLE')])))
    with pytest.raises(ValueError, match='test_plan'):
        ref.run_test(dict(inputs, test_plan=plan), cfg, tmp_path, True, sha)
    assert json.loads((tmp_path / '_test_opened' / f'{sha}.json').read_text())['status'] == 'opening'
    with pytest.raises(ref.TestLocked, match='already opened'):
        ref.run_test(inputs, cfg, tmp_path, True, sha)


# ---------------------------------------------------------------------------
# Synthetic end-to-end: validate (A) -> open test once on the same baseline (B) -> refuse again (C)
# ---------------------------------------------------------------------------
def test_synthetic_train_valid_test_flow(tmp_path):
    # 8 synthetic people per cohort do not reliably meet the 2.0-scale bootstrap gate, so this flow pre-registers a
    # looser one for BOTH runs (same gates fingerprint); the default gates are exercised by the not-ready test below.
    gates = {'gates': {'bootstrap_median_ci_width_max_scale': 10.0}}
    validate, unlock = dict(FAST, **gates), dict(UNLOCK, **gates)
    out, ws = tmp_path / 'out', tmp_path / 'ws'
    a, _ = run('synthetic', 'A', VALIDATION_TRACKS, outputs_root=out, workspace_root=ws, overrides=validate)
    sha = a['baseline_sha256']
    assert a['overall'] != 'fail' and all(c['ready'] for c in a['test_ready_cells'])
    assert not (out / 'baseline_registry.json').exists()  # synthetic never enters the real registry
    record = json.loads((out / 'synthetic_registry.json').read_text())[sha]['validation_runs'][0]
    assert record['gates_fingerprint'] == fingerprint(load_backtest_config(overrides=validate)['gates'])

    locked, _ = run('synthetic', 'B0', ['test'], outputs_root=out, workspace_root=ws, overrides=validate,
                    unlock_test=True, approved_sha=sha, synthetic_from='A')
    assert locked['tracks']['test']['status'] == 'locked'  # config lock still closed
    other_gates, _ = run('synthetic', 'B1', ['test'], outputs_root=out, workspace_root=ws, overrides=UNLOCK,
                         unlock_test=True, approved_sha=sha, synthetic_from='A')
    assert other_gates['tracks']['test']['status'] == 'locked_not_ready'  # gates changed after validation

    b, b_out = run('synthetic', 'B', ['test'], outputs_root=out, workspace_root=ws, overrides=unlock,
                   unlock_test=True, approved_sha=sha, synthetic_from='A')
    assert b['baseline_sha256'] == sha  # the same file that was validated, never refitted
    test = b['tracks']['test']
    assert test['status'] == 'compared_once' and test['unchanged'] and test['readiness']['validation_run'] == 'A'
    assert {r['split'] for r in json.loads((b_out / 'analysis_TEST_SYNTHETIC.json').read_text(encoding='utf-8'))['analysis']} == {'test'}
    assert b['protected_paths_unchanged']

    c, _ = run('synthetic', 'C', ['test'], outputs_root=out, workspace_root=ws, overrides=unlock,
               unlock_test=True, approved_sha=sha, synthetic_from='A')
    assert c['tracks']['test']['status'] == 'locked' and 'already opened' in c['tracks']['test']['reason']


def test_synthetic_test_without_validation_is_not_ready(tmp_path):
    ws = tmp_path / 'ws'
    a, _ = run('synthetic', 'A', ['valid'], outputs_root=tmp_path / 'o1', workspace_root=ws, overrides=FAST)
    b, _ = run('synthetic', 'B', ['test'], outputs_root=tmp_path / 'o2', workspace_root=ws, overrides=UNLOCK,
               unlock_test=True, approved_sha=a['baseline_sha256'], synthetic_from='A')
    assert b['tracks']['test']['status'] == 'locked_not_ready'
    assert not (tmp_path / 'o2' / '_test_opened').exists()


def test_synthetic_default_gates_are_not_ready(tmp_path):
    """Default pre-registered gates with 200 bootstraps: 8 people per cohort fail the bootstrap gate -> test stays locked."""
    out, ws = tmp_path / 'out', tmp_path / 'ws'
    a, _ = run('synthetic', 'A', VALIDATION_TRACKS, outputs_root=out, workspace_root=ws)
    assert any(not c['ready'] for c in a['test_ready_cells'])
    b, _ = run('synthetic', 'B', ['test'], outputs_root=out, workspace_root=ws, overrides={'test': {'allow_test_split': True}},
               unlock_test=True, approved_sha=a['baseline_sha256'], synthetic_from='A')
    assert b['tracks']['test']['status'] == 'locked_not_ready'


# ---------------------------------------------------------------------------
# prepare_test: locks and assembly (the real clean/extract step is injected; no raw data in tests)
# ---------------------------------------------------------------------------
def test_prepare_refuses_checkpoint_baseline():
    with pytest.raises(ref.TestLocked, match='full-train'):
        prep.locked_context(load_backtest_config(overrides=UNLOCK), True, 'sha')


def _run_files(tmp_path, artifact_version_source):
    (tmp_path / 'pipeline_config.yaml').write_text('clean: {a: 1}\n', encoding='utf-8')
    (tmp_path / 'reference_config.yaml').write_text('x: 1\n', encoding='utf-8')
    source = dict(git_commit='0' * 40, files={'src/clean/run.py': 'h'})
    (tmp_path / 'run_manifest.json').write_text(json.dumps(dict(run_id='r', source_version=source)))
    (tmp_path / 'noise_lock.json').write_text(json.dumps(dict(pipeline_config_sha256=sha256(tmp_path / 'pipeline_config.yaml'),
                                                              target_axes_mg=[1, 1, 1])))
    files = {n: tmp_path / n for n in ['pipeline_config.yaml', 'reference_config.yaml', 'run_manifest.json', 'noise_lock.json']}
    plan = dict(locked_pipeline_config_sha256=sha256(files['pipeline_config.yaml']),
                locked_reference_config_sha256=sha256(files['reference_config.yaml']))
    artifact = dict(preprocessing_version=prep.expected_preprocessing_version({'a': 1}, artifact_version_source or source))
    return files, plan, artifact, source


def test_verify_run_files(tmp_path):
    files, plan, artifact, source = _run_files(tmp_path, None)
    pipeline, lock, _ = prep.verify_run_files(files, plan, artifact, dict(source, git_commit='1' * 40))
    assert pipeline['clean'] == {'a': 1} and lock['target_axes_mg'] == [1, 1, 1]
    with pytest.raises(FileNotFoundError):
        prep.verify_run_files(dict(files, **{'noise_lock.json': None}), plan, artifact, source)
    with pytest.raises(ValueError, match='locked_pipeline_config'):
        prep.verify_run_files(files, dict(plan, locked_pipeline_config_sha256='x'), artifact, source)
    with pytest.raises(ValueError, match='locked_reference_config'):
        prep.verify_run_files(files, dict(plan, locked_reference_config_sha256='x'), artifact, source)
    with pytest.raises(ValueError, match='Clean source differs'):
        prep.verify_run_files(files, plan, artifact, dict(source, files={'src/clean/run.py': 'changed'}))
    with pytest.raises(ValueError, match='preprocessing_version'):
        prep.verify_run_files(files, plan, dict(preprocessing_version='other'), source)


def _prepare(tmp_path, monkeypatch, outcomes, **kw):
    subjects = [dict(subject_id=s, dataset='cops', split='test') for s in outcomes]
    ctx = dict(ws=tmp_path / 'ws', sha='a' * 64, plan=dict(subjects=subjects), readiness=dict(validation_run='v1'),
               pipeline=dict(datasets={'cops': {}, 'nhanes': {}}, clean=dict(subjects={})), cfg={}, outputs_root=tmp_path / 'out')
    built = []

    def create_bundle(hourly, out, *a):
        built.append(out)
        out.mkdir(parents=True)
        (out / 'bundle.manifest.json').write_text('{}')
    monkeypatch.setattr('src.reference.io.create_bundle', create_bundle)
    monkeypatch.setattr(prep, 'load_roster', lambda pipeline: pd.DataFrame())

    def process(ctx, s, pipeline, roster):
        outcome = outcomes[s['subject_id']]
        if isinstance(outcome, Exception):
            raise outcome
        return dict(status=outcome)
    result = prep.prepare({}, True, 'a' * 64, process=process, context=lambda *a: ctx, **kw)
    return result, built, ctx


def test_prepare_assembly(tmp_path, monkeypatch):
    assert _prepare(tmp_path, monkeypatch, {'T1': 'included'}, dry_run=True)[0]['status'] == 'dry_run_locks_passed'
    with pytest.raises(ValueError, match='not in the test plan'):
        _prepare(tmp_path, monkeypatch, {'T1': 'included'}, ids=['X'])
    result, built, ctx = _prepare(tmp_path, monkeypatch, {'T1': 'included', 'T2': RuntimeError('boom')})
    assert result['status'] == 'incomplete' and result['pending'] == ['T2'] and not built
    # Resume: T1 is not processed again; once T2 resolves the bundle is built exactly once.
    result, built, _ = _prepare(tmp_path, monkeypatch, {'T1': RuntimeError('must not rerun'), 'T2': 'excluded'})
    assert result['status'] == 'prepared' and result['n_included'] == 1 and result['n_excluded'] == 1 and len(built) == 1
    marker = json.loads((tmp_path / 'out' / '_test_prepared' / f"{'a' * 64}.json").read_text())
    assert marker['status'] == 'prepared'
    with pytest.raises(ValueError, match='already prepared'):
        _prepare(tmp_path, monkeypatch, {'T1': 'included', 'T2': 'excluded'})


def test_prepare_symptom_requests_only_train_valid_keys(tmp_path):
    roster = pd.DataFrame(dict(dataset=['cops', 'cops', 'cops', 'nhanes'], subject_id=['COPS-A', 'COPS-V', 'COPS-T', 'NHANES-1'],
                               split=['train', 'valid', 'test', 'train']))
    subjects = symptom_prep.select_subjects(roster)
    assert [s['subject_id'] for s in subjects] == ['COPS-A', 'COPS-V']
    with pytest.raises(ValueError, match='COPS-T'):
        symptom_prep.select_subjects(roster, ['COPS-T'])
    diary = ('Day;Time;TremorScore;KinesiaScore;Visit;Kinesia;WearableDataAvailability\n'
             'Day 1;10 hr;1;0;outpatient;Awake;Wearable Worn\nDay 1;11 hr;0;0;outpatient;Sleep;Wearable Worn\n').encode()
    qc = json.dumps(dict(included=True, status='ok', days=[dict(day=1, valid=True)])).encode()
    features = b'subject_id,dataset,day,hour,tremor_frac_all\nCOPS-A,cops,1,9,0.1\n'
    files = {f'data/raw/cops/COPS-A/COPS-A_symptomdiary.csv': diary, 'data/clean/cops/COPS-A.qc.json': qc,
             'data/features/cops/COPS-A.csv': features}
    requested = []
    manifest = symptom_prep.build_snapshot(tmp_path / 'snap', subjects, lambda k: requested.append(k) or files.get(k))
    assert not any('COPS-T' in k or 'nhanes' in k for k in requested)
    assert {e['subject_id']: e['status'] for e in manifest['subjects']} == {'COPS-A': 'ok', 'COPS-V': 'missing'}
    labels = pd.read_csv(tmp_path / 'snap/labels.csv')
    assert labels[['subject_id', 'day', 'hour', 'tremor_score']].values.tolist() == [['COPS-A', 1, 9, 1]]  # Time 10 hr -> hour 9
    assert (tmp_path / 'snap/features/COPS-A.csv').read_bytes() == features
    with pytest.raises(ValueError, match='never overwrite'):
        symptom_prep.build_snapshot(tmp_path / 'snap', subjects, files.get)


@pytest.mark.parametrize('main', [run_main, prep.main, symptom_prep.main])
def test_cli_parsers_build(main):
    with pytest.raises(SystemExit) as exc:
        main(['--help'])
    assert exc.value.code == 0


def test_prepare_with_no_included_subject_builds_nothing(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='No QC-included'):
        _prepare(tmp_path, monkeypatch, {'T1': 'excluded'})
