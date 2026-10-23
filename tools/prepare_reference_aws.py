"""Prepare/review a CreateTrainingJob request. NEVER submits or uploads anything."""
import argparse
import importlib.metadata
import io
import json
import sys
import tarfile
from pathlib import Path

import botocore.session
from botocore.validate import validate_parameters
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.reference.common import sha256, write_json
from src.reference.provenance import CLEAN_SOURCE_PATHS

TESTED_BOTO3='1.43.106'
TESTED_BOTOCORE='1.43.106'


def prepare_script_archive(path):
    """Explicit source whitelist; no configs, bundles, patient data or credentials."""
    path=Path(path)
    if path.exists():
        raise ValueError('Source archive exists; use a new run directory')
    path.parent.mkdir(parents=True,exist_ok=True)
    files=['src/__init__.py','src/features/__init__.py','src/features/run.py',
           'tools/reference_baseline.py','tools/sagemaker_fit_reference.py']
    files += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'src/reference').glob('*.py'))]
    files += CLEAN_SOURCE_PATHS
    entry=b'from tools.sagemaker_fit_reference import main\nif __name__ == "__main__":\n    main()\n'
    with tarfile.open(path,'w:gz') as archive:
        for name in sorted(set(files)):
            archive.add(ROOT/name,arcname=name,recursive=False)
        info=tarfile.TarInfo('train.py')
        info.size=len(entry)
        archive.addfile(info,io.BytesIO(entry))
    return dict(path=str(path),sha256=sha256(path),files=sorted(set(files)|{'train.py'}),
                entry_point='train.py',dependencies='preinstalled sklearn 1.9-0 image; no pip install')


def build_request(pipeline, cfg):
    root=pipeline['storage']['s3_root'].rstrip('/')
    if not root.startswith('s3://'):
        raise ValueError('Use existing S3 root from pipeline config')
    if not cfg.get('role_arn') or not cfg.get('image_uri') or 'CHANGE' in cfg['input_relative'] or 'CHANGE' in cfg['job_name']:
        raise ValueError('Fill execution role, ECR image URI, run name and prepared TRAIN input prefix first')
    if cfg['instance_count'] != 1:
        raise ValueError('Reference fit is single-process/single-instance, not distributed')
    relatives=[cfg['input_relative'],cfg['output_relative']]
    if cfg.get('mode')=='prebuilt_script':
        if not cfg.get('source_relative') or 'CHANGE' in cfg['source_relative']:
            raise ValueError('Fill prepared source archive S3 prefix first')
        relatives.append(cfg['source_relative'])
    if any(not value.startswith('data/') or '..' in value.split('/') or ':' in value or '\\' in value for value in relatives):
        raise ValueError('Use relative data/ prefixes under the existing S3 root')
    request=dict(TrainingJobName=cfg['job_name'],RoleArn=cfg['role_arn'],
        AlgorithmSpecification=dict(TrainingImage=cfg['image_uri'],TrainingInputMode='File'),
        InputDataConfig=[dict(ChannelName='train',DataSource=dict(S3DataSource=dict(
            S3DataType='S3Prefix',S3Uri=root+'/'+cfg['input_relative'].strip('/'),S3DataDistributionType='FullyReplicated')))],
        OutputDataConfig=dict(S3OutputPath=root+'/'+cfg['output_relative'].strip('/')),
        ResourceConfig=dict(InstanceType=cfg['instance_type'],InstanceCount=cfg['instance_count'],VolumeSizeInGB=cfg['volume_size_gb']),
        StoppingCondition=dict(MaxRuntimeInSeconds=cfg['max_runtime_seconds']),
        EnableNetworkIsolation=cfg['network_isolation'],
        Tags=[dict(Key='Purpose',Value='cohort-reference-not-diagnosis')])
    if cfg.get('mode')=='prebuilt_script':
        request['AlgorithmSpecification']['ContainerEntrypoint']=['train']
        request['HyperParameters']={
            'sagemaker_program':json.dumps('train.py'),
            'sagemaker_submit_directory':json.dumps(root+'/'+cfg['source_relative']),
            'sagemaker_container_log_level':'20',
            'sagemaker_region':json.dumps(pipeline['storage']['region'])}
    shape=botocore.session.Session().get_service_model('sagemaker').operation_model('CreateTrainingJob').input_shape
    validate_parameters(request,shape)  # offline validation against the installed SDK's real API shape
    return request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',type=Path,default=ROOT/'config/sagemaker_reference.yaml')
    p.add_argument('--out',type=Path,default=ROOT/'reports/reference_baseline/aws_plan.json')
    a=p.parse_args()
    versions={name:importlib.metadata.version(name) for name in ['boto3','botocore']}
    if versions != {'boto3':TESTED_BOTO3,'botocore':TESTED_BOTOCORE}:
        raise ValueError('Use requirements-reference-aws.txt; this SDK version has not been verified')
    cfg=yaml.safe_load(a.config.read_text(encoding='utf-8'))
    archive=prepare_script_archive(a.out.parent/'source.tar.gz') if cfg.get('mode')=='prebuilt_script' else None
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    try:
        request=build_request(pipeline,cfg)
        plan=dict(status='request_validated_offline_NOT_SUBMITTED',sdk=versions,request=request)
    except ValueError as exc:
        plan=dict(status='configuration_incomplete_NOT_SUBMITTED',sdk=versions,reason=str(exc),config=cfg,
                  input_root=pipeline['storage']['s3_root'],required_actions=['prepare train-only bundle','fill role/image/run identifiers','build and push container separately','upload bundle separately','review request before any job submission'])
    if cfg.get('mode')=='prebuilt_script':
        plan['source_archive']=archive
        plan['required_actions']=['obtain verified real clean lineage and pass local pilot first',
            'fill execution role, run and TRAIN/source prefixes',
            'upload reviewed source archive and train-only bundle separately',
            'review request; AWS container execution remains untested; no job submitted']
        plan['container_evidence']={
            'dockerfile':'https://raw.githubusercontent.com/aws/deep-learning-containers/main/docker/sklearn/1.9-0/Dockerfile',
            'dependencies':'https://raw.githubusercontent.com/aws/deep-learning-containers/main/docker/sklearn/1.9-0/pyproject.toml',
            'python':'3.12','fit_dependencies':{'numpy':'2.5.1','pandas':'2.2.3','PyYAML':'6.0.3'},
            'container_execution_verified':False}
    write_json(a.out,plan)
    print(f"{plan['status']}; {a.out}; no AWS API call, upload, job or endpoint")


if __name__=='__main__':
    main()
