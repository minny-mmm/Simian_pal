"""Offline lifecycle regressions; no AWS clients, jobs or real patient fixtures."""
import json
import io
import tarfile
from types import SimpleNamespace

from botocore.exceptions import ClientError
import pytest

from tools import run_reference_aws as runner


def test_numeric_parity_preserves_units_status_null_and_rejects_large_drift():
    runner.compare_numeric({'value':1.0,'unit':'g','scale':None},
                           {'value':1.0+1e-14,'unit':'g','scale':None},1e-12,1e-12)
    for other in [dict(value=1.1,unit='g',scale=None),dict(value=1.,unit='steps/s',scale=None),dict(value=1.,unit='g',scale=0.)]:
        with pytest.raises(ValueError):runner.compare_numeric(dict(value=1.,unit='g',scale=None),other,1e-12,1e-12)


def test_snapshot_upload_is_conditional_and_cannot_overwrite_other_content(tmp_path):
    f=tmp_path/'unit.txt';f.write_text('unit fixture')
    calls=[]
    class Missing:
        def head_object(self,**kwargs):raise ClientError({'Error':{'Code':'404'}},'HeadObject')
        def put_object(self,**kwargs):calls.append(kwargs)
    runner.upload_file(Missing(),f,'s3://unit-bucket/unit-snapshot/file')
    assert calls[0]['IfNoneMatch']=='*'
    assert calls[0]['Metadata']['sha256']==runner.sha256(f)
    class Existing(Missing):
        def head_object(self,**kwargs):return {'Metadata':{'sha256':'different'}}
    with pytest.raises(ValueError,match='overwrite'):runner.upload_file(Existing(),f,'s3://unit-bucket/unit-snapshot/file')
    assert len(calls)==1


def test_ambiguous_paid_submission_is_not_automatically_submitted_twice(tmp_path,monkeypatch):
    p={'report':tmp_path,'model':tmp_path/'model'}
    runner.write_json(tmp_path/'input_manifest.json',{'code_revision':{'git_commit':'unit-test'}})
    monkeypatch.setattr(runner,'check_snapshot',lambda _:None)
    monkeypatch.setattr(runner,'preflight',lambda *_:dict(existing_job=None,blockers=[]))
    monkeypatch.setattr(runner,'upload',lambda *_:None)
    monkeypatch.setattr(runner,'load_reference',lambda _:dict(data_kind='real',fit_split='train'))
    monkeypatch.setattr(runner,'build_request',lambda *_:dict(TrainingJobName='unit-never-submitted'))
    calls=[]
    class Client:
        def create_training_job(self,**kwargs):
            calls.append(kwargs);raise ConnectionError('Unit fixture: response lost after sending request')
    session=SimpleNamespace(client=lambda *_,**__:Client())
    cfg={'job_name':'unit-never-submitted'}
    with pytest.raises(ConnectionError):runner.submit(cfg,p,{},session)
    with pytest.raises(FileExistsError):runner.submit(cfg,p,{},session)
    assert len(calls)==1
    assert 'RetryStrategy' not in calls[0]


def test_existing_completed_job_is_reused_without_any_create_call(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,'check_snapshot',lambda _:None)
    monkeypatch.setattr(runner,'preflight',lambda *_:dict(existing_job={'arn':'unit-existing-arn','status':'Completed'},blockers=[]))
    monkeypatch.setattr(runner,'load_reference',lambda _:dict(data_kind='real',fit_split='train'))
    session=SimpleNamespace(client=lambda *_,**__:pytest.fail('Existing job must not be submitted again'))
    runner.submit({'job_name':'unit-existing'},dict(report=tmp_path,model=tmp_path/'model'),{},session)
    assert runner.read(tmp_path/'submission.json')['reused'] is True
    assert not (tmp_path/'submission_intent.json').exists()


def test_role_is_service_and_job_scoped_without_endpoint_or_patient_source_permissions():
    cfg=dict(job_name='unit-never-submitted',input_relative='data/daily/unit/train',
             source_relative='data/models/unit/source/source.tar.gz',output_relative='data/models/unit/output')
    pipeline={'storage':dict(s3_root='s3://unit-bucket/pd',region='ap-southeast-1')}
    trust,policy=runner.role_documents(cfg,pipeline,'000000000000')
    assert trust['Statement'][0]['Principal']=={'Service':'sagemaker.amazonaws.com'}
    assert trust['Statement'][0]['Condition']['ArnEquals']['aws:SourceArn'].endswith('/unit-never-submitted')
    assert 'CreateEndpoint' not in json.dumps(policy)
    get=[s for s in policy['Statement'] if 's3:GetObject' in s['Action']][0]
    assert all('/unit/' in r for r in get['Resource'])
    assert not any('raw/' in r or 'clean/' in r for r in get['Resource'])


def test_model_archive_reads_only_expected_json_and_skips_sdk_code(tmp_path):
    archive=tmp_path/'unit-model.tar.gz';out=tmp_path/'out';out.mkdir()
    with tarfile.open(archive,'w:gz') as tar:
        for name in ['./baseline.json','fit_report.json','runtime_metadata.json','code/unused.py','../do-not-extract.json']:
            data=b'{}';info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
    runner.extract_model_archive(archive,out)
    assert {p.name for p in out.iterdir()}=={'baseline.json','fit_report.json','runtime_metadata.json'}
    assert not (tmp_path/'do-not-extract.json').exists()
    (out/'baseline.json').write_text('different')
    with pytest.raises(ValueError,match='replace'):runner.extract_model_archive(archive,out)
