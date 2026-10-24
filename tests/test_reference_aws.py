import copy
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from src.reference.common import ROOT, load_config
from src.reference.daily import aggregate_daily
from src.reference.io import write_bundle
from tools.prepare_reference_aws import TESTED_BOTO3, TESTED_BOTOCORE, build_request, prepare_script_archive
from tools.reference_demo import synthetic_inputs


def test_request_is_valid_against_installed_sdk_without_aws_call():
    assert importlib.metadata.version('boto3')==TESTED_BOTO3
    assert importlib.metadata.version('botocore')==TESTED_BOTOCORE
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    cfg=yaml.safe_load((ROOT/'config/sagemaker_reference.yaml').read_text(encoding='utf-8'))
    with pytest.raises(ValueError,match='Fill'):
        build_request(pipeline,cfg)
    # Deliberately NONRUNNABLE placeholder account; schema verification only, never submission.
    cfg.update(role_arn='arn:aws:iam::000000000000:role/SYNTHETIC-NOT-FOR-SUBMISSION',
               image_uri='000000000000.dkr.ecr.ap-southeast-1.amazonaws.com/synthetic-reference:test',
               input_relative='data/daily/reference-v1/SYNTHETIC-NOT-UPLOADED',job_name='synthetic-never-submitted')
    request=build_request(pipeline,cfg)
    assert request['InputDataConfig'][0]['DataSource']['S3DataSource']['S3Uri'].startswith(pipeline['storage']['s3_root']+'/')
    assert request['OutputDataConfig']['S3OutputPath'].startswith(pipeline['storage']['s3_root']+'/')
    assert request['ResourceConfig']['InstanceCount']==1
    assert request['EnableNetworkIsolation'] is True
    cfg['instance_count']=2
    with pytest.raises(ValueError,match='single'):
        build_request(pipeline,cfg)


def test_sagemaker_entrypoint_local_train_only_and_output_artifact(tmp_path):
    cfg=load_config()
    hourly,roster,provenance=synthetic_inputs(cfg)
    daily,hour_qc=aggregate_daily(hourly,roster,cfg,provenance)
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    write_bundle(tmp_path/'bundle',daily,hour_qc,roster,cfg,pipeline,provenance)
    proc=subprocess.run([sys.executable,str(ROOT/'tools/prepare_reference_training.py'),'--bundle',str(tmp_path/'bundle'),
                         '--out',str(tmp_path/'train')],capture_output=True,text=True)
    assert proc.returncode==0,proc.stderr
    # SageMaker provides an already-existing empty model directory.
    model=tmp_path/'model'
    model.mkdir()
    result=subprocess.run([sys.executable,str(ROOT/'tools/sagemaker_fit_reference.py'),'train','--bundle',str(tmp_path/'train'),
                           '--model-dir',str(model)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    artifact=json.loads((model/'baseline.json').read_text())
    assert artifact['data_kind']=='synthetic' and artifact['fit_split']=='train'
    bad=subprocess.run([sys.executable,str(ROOT/'tools/sagemaker_fit_reference.py'),'train','--bundle',str(tmp_path/'bundle'),
                        '--model-dir',str(tmp_path/'bad-model')],capture_output=True,text=True)
    assert bad.returncode!=0 and not (tmp_path/'bad-model/baseline.json').exists()


def test_prebuilt_script_request_archive_and_same_entrypoint(tmp_path):
    import tarfile
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    cfg=yaml.safe_load((ROOT/'config/sagemaker_reference_prebuilt.yaml').read_text(encoding='utf-8'))
    cfg.update(role_arn='arn:aws:iam::000000000000:role/SYNTHETIC-NOT-FOR-SUBMISSION',
               input_relative='data/daily/reference-v1/SYNTHETIC-NOT-UPLOADED',
               source_relative='data/models/reference-v1/SYNTHETIC-NOT-UPLOADED/source.tar.gz',
               job_name='synthetic-prebuilt-never-submitted')
    request=build_request(pipeline,cfg)
    assert request['EnableNetworkIsolation'] is True
    assert request['AlgorithmSpecification']['ContainerEntrypoint']==['train']
    assert json.loads(request['HyperParameters']['sagemaker_program'])=='train.py'
    assert json.loads(request['HyperParameters']['sagemaker_submit_directory'])=='/opt/ml/input/data/code/source.tar.gz'
    assert [channel['ChannelName'] for channel in request['InputDataConfig']]==['train','code']
    assert request['InputDataConfig'][1]['DataSource']['S3DataSource']['S3Uri'].endswith('/source.tar.gz')
    source=prepare_script_archive(tmp_path/'source.tar.gz')
    unpack=tmp_path/'source'
    with tarfile.open(source['path']) as archive:
        names=archive.getnames()
        assert set(names)==set(source['files'])
        assert all(n.endswith('.py') and n.startswith(('src/','tools/','train.py')) for n in names)
        assert 'requirements.txt' not in names
        archive.extractall(unpack,filter='data')
    # Exercise the actual archive entrypoint, not just the repository copy.
    cfg_ref=load_config()
    hourly,roster,p=synthetic_inputs(cfg_ref)
    daily,qc=aggregate_daily(hourly,roster,cfg_ref,p)
    write_bundle(tmp_path/'bundle',daily[daily.split.eq('train')],qc[qc.split.eq('train')],
                 roster,cfg_ref,pipeline,{**p,'training_bundle_only':True})
    proc=subprocess.run([sys.executable,str(unpack/'train.py'),'--bundle',str(tmp_path/'bundle'),
                         '--model-dir',str(tmp_path/'model')],capture_output=True,text=True,cwd=tmp_path)
    assert proc.returncode==0,proc.stderr
    assert json.loads((tmp_path/'model/baseline.json').read_text())['data_kind']=='synthetic'
