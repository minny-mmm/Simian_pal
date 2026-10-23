"""Unit tests use temporary fake files only, never reported as real model results."""
from pathlib import Path
import os
import subprocess
import sys

import pytest

from tools import run_real_reference as runner


def test_raw_selection_requires_diary_uses_agreed_wrist_and_prefers_csv():
    root='bucket/pd'
    prefix=root+'/data/raw/cops/COPS-10/'
    subject=dict(dataset='cops',subject_id='COPS-10',wrist='left',split='train')
    left=prefix+'Accelerometry/COPS-10_Day0_09h-10h_leftWrist'
    right=prefix+'Accelerometry/COPS-10_Day0_09h-10h_rightWrist.zip'
    diary=prefix+'COPS-10_symptomdiary.csv'
    files={left+'.zip':{},left+'.csv':{},right:{}}
    assert runner.select_raw(files,root,subject)==[]
    files[diary]={}
    assert set(runner.select_raw(files,root,subject))=={left+'.csv',diary}


def test_run_namespace_cannot_escape_workspace_or_mix_unsafe_paths():
    for value in ['../old','x/y','x\\y','C:\\data','', 'real:old']:
        with pytest.raises(ValueError):runner.paths(value)
    result=runner.paths('real-unit-test')
    assert all(p.is_relative_to(runner.ROOT) for p in result.values())


def test_inspect_cannot_overwrite_run_inventory_or_read_remote_again(tmp_path,monkeypatch):
    inventory=tmp_path/'inventory.json';inventory.write_bytes(b'unit-test-original-snapshot')
    monkeypatch.setattr(runner,'initialize',lambda _: ({'report':tmp_path},{}))
    monkeypatch.setattr(runner,'remote_files',lambda _: pytest.fail('Must refuse before accessing remote'))
    with pytest.raises(ValueError,match='snapshot already exists'):
        runner.inspect('unit-test')
    assert inventory.read_bytes()==b'unit-test-original-snapshot'


def test_resource_guard_uses_commit_headroom_not_only_free_physical_ram(monkeypatch):
    cfg={'clean':{'resample':{'fs_out':50}}}
    subject=dict(dataset='cops')
    inputs=[dict(path='COPS-10_Day0_09h-10h_leftWrist.zip')]
    monkeypatch.setattr(runner,'memory_headroom',lambda:dict(available_ram=2**20,available_commit=10*2**30))
    assert runner.resource_check(cfg,subject,inputs)['available_ram']==2**20
    monkeypatch.setattr(runner,'memory_headroom',lambda:dict(available_ram=2**20,available_commit=2**30))
    with pytest.raises(MemoryError,match='memory guard'):runner.resource_check(cfg,subject,inputs)


def test_clean_does_not_backfill_manifest_for_preexisting_output(tmp_path,monkeypatch):
    p={k:tmp_path/k for k in ['report','clean','raw']}
    c={'clean':{}}
    monkeypatch.setattr(runner,'initialize',lambda _: (p,c))
    monkeypatch.setattr(runner.clean,'load_subjects',lambda _: [dict(dataset='cops',subject_id='COPS-10',split='train',wrist='left')])
    d=p['clean']/'cops';d.mkdir(parents=True)
    (d/'COPS-10.parquet').write_bytes(b'unit-test-placeholder')
    with pytest.raises(ValueError,match='never backfill'):runner.clean_one('unit-test','COPS-10')
    assert not (d/'COPS-10.clean.manifest.json').exists()


def test_test_subject_is_refused_before_raw_or_signal_reads(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'initialize',lambda _: ({'clean':tmp_path},{'clean':{}}))
    monkeypatch.setattr(runner.clean,'load_subjects',lambda _: [dict(dataset='cops',subject_id='COPS-TEST',split='test')])
    with pytest.raises(ValueError,match='test is locked'):runner.clean_one('unit-test','COPS-TEST')


def test_fixed_noise_target_is_frozen_without_inventing_train_estimates(tmp_path,monkeypatch):
    p={'report':tmp_path/'report','clean':tmp_path/'clean'}
    p['report'].mkdir()
    runner.write_json(p['report']/'inventory.json',dict(files={}))
    cfg={'clean':{'harmonize':{'add_noise':{'target_mg':[4.93,5.07,6.]}}}}
    (p['report']/'pipeline_config.yaml').write_text('unit-test-config')
    monkeypatch.setattr(runner,'initialize',lambda _: (p,cfg))
    monkeypatch.setattr(runner,'remote_files',lambda _: (None,'bucket'))
    monkeypatch.setattr(runner.clean,'load_subjects',lambda _: [dict(dataset='cops',subject_id='COPS-10',split='train')])
    monkeypatch.setattr(runner,'select_raw',lambda *_: ['present'])
    runner.lock_noise('unit-test')
    lock=runner.read_json(p['report']/'noise_lock.json')
    assert lock['target_axes_mg']==[4.93,5.07,6.]
    diagnostics=runner.read_json(p['report']/'train_noise_diagnostics.json')
    assert diagnostics['current_train_median_axes_mg'] is None
    assert diagnostics['pending_train_subjects']==['COPS-10']
    assert lock['mode']=='fixed_agreed_config_not_refitted'


def test_retry_cannot_relabel_or_restart_a_worker_without_exit_evidence(tmp_path,monkeypatch):
    p={k:tmp_path/k for k in ['report','clean','raw']}
    p['report'].mkdir();dest=p['clean']/'cops';dest.mkdir(parents=True)
    runner.write_json(dest/'COPS-10.started.json',dict(started_utc='2026-01-01T00:00:00Z'))
    runner.write_json(p['report']/'blocked_clean-one_COPS-10.json',dict(exception_type='ValueError'))
    monkeypatch.setattr(runner,'initialize',lambda _: (p,{'clean':{}}))
    monkeypatch.setattr(runner.clean,'load_subjects',lambda _: [dict(dataset='cops',subject_id='COPS-10',split='train',wrist='left')])
    with pytest.raises(ValueError,match='controller evidence'):
        runner.clean_one('unit-test','COPS-10')
    assert (dest/'COPS-10.started.json').exists()


def test_redirected_worker_log_is_utf8_even_with_windows_legacy_encoding(tmp_path):
    code="""import sys
from pathlib import Path
from tools import run_real_reference as r
r.initialize=lambda _: ({'report':Path(sys.argv[1])},{})
r.clean_one=lambda *_: print('ทดสอบ UTF-8 unit test')
sys.argv=['run_real_reference.py','--run-id','unit-test','--stage','clean-one','--ids','UNIT-TEST']
r.main()
"""
    env={**os.environ,'PYTHONIOENCODING':'cp1252','PYTHONUTF8':'0'}
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],cwd=runner.ROOT,env=env,capture_output=True)
    assert result.returncode==0,result.stderr.decode('utf-8',errors='replace')
    assert 'ทดสอบ UTF-8' in result.stdout.decode('utf-8')


@pytest.mark.parametrize('exception_type',['EndpointConnectionError','FSTimeoutError'])
def test_network_outage_stops_queue_and_blocks_automatic_fit(tmp_path,monkeypatch,exception_type):
    report=tmp_path/'report';report.mkdir()
    runner.write_json(report/'inventory.json',dict(files={}))
    subjects=[dict(dataset='cops',subject_id=f'UNIT-{i}',split='train',wrist='left') for i in range(5)]
    monkeypatch.setattr(runner,'initialize',lambda _: ({'report':report},{}))
    monkeypatch.setattr(runner,'remote_files',lambda _: (None,'bucket'))
    monkeypatch.setattr(runner,'lock_noise',lambda _: None)
    monkeypatch.setattr(runner.clean,'load_subjects',lambda _: subjects)
    monkeypatch.setattr(runner,'select_raw',lambda *_: ['unit-test-raw'])
    calls=[]
    def failed_worker(command,**kwargs):
        sid=command[-1];calls.append(sid)
        runner.write_json(report/f'blocked_clean-one_{sid}.json',dict(exception_type=exception_type))
        return subprocess.CompletedProcess(command,1)
    monkeypatch.setattr(runner.subprocess,'run',failed_worker)
    with pytest.raises(RuntimeError,match='No automatic fit/evaluation'):
        runner.run_subjects('unit-test','train',workers=1)
    assert len(calls)==3
    rows=runner.read_json(report/'train_progress.json')['rows']
    assert [r['status'] for r in rows]==['failed']*3+['deferred_network_outage']*2
