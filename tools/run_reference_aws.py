"""Real reference pilot lifecycle. Uses existing extraction, aggregation and fit.

Paid submission is explicit, single-attempt, and never retried after failure or an
ambiguous response. No endpoint API, test signal reads, or synthetic fallback.
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path,PurePosixPath
import subprocess
import sys
import tarfile
import time
from datetime import datetime,timezone

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import numpy as np
import pandas as pd
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.reference.common import sha256,write_json,load_config,load_roster,code_version
from src.reference.io import create_bundle,load_bundle
from src.reference.model import load_reference
from src.reference.provenance import validate_clean_record,validate_real_fit_provenance
from tools.prepare_reference_aws import build_request,prepare_script_archive,TESTED_BOTO3,TESTED_BOTOCORE


def now():return datetime.now(timezone.utc).isoformat()
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def safe(value):
    if not value or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in value):
        raise ValueError('Use a simple immutable run namespace')
    return value


def context(config):
    if {p:importlib.metadata.version(p) for p in ['boto3','botocore']}!={'boto3':TESTED_BOTO3,'botocore':TESTED_BOTOCORE}:
        raise ValueError('Use the verified SDK versions in requirements-reference-aws.txt')
    cfg=yaml.safe_load(Path(config).read_text(encoding='utf-8'));run=safe(cfg['run_id'])
    p={k:ROOT/base/run for k,base in dict(report='reports/reference_baseline',hourly='data/features/reference-v1',
        daily='data/daily/reference-v1',model='data/models/reference-v1').items()}
    p['report'].mkdir(parents=True,exist_ok=True)
    pipeline=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    session=boto3.Session(profile_name=pipeline['storage']['aws_profile'],region_name=pipeline['storage']['region'])
    return cfg,p,pipeline,session


def completed_subjects(pipeline,clean_root,split):
    """Inspect only final evidence for the requested split; never read in-flight outputs."""
    from src.clean.run import load_subjects
    selected=[];excluded=[]
    for subject in load_subjects(pipeline):
        if subject['split']!=split:continue
        ds,sid=subject['dataset'],subject['subject_id'];base=Path(clean_root)/ds
        manifest=base/f'{sid}.clean.manifest.json'
        if not manifest.exists():excluded.append(dict(**subject,reason='no_completed_manifest'));continue
        m=read(manifest)
        if m.get('status')!='completed':excluded.append(dict(**subject,reason='not_completed'));continue
        qpath=base/f'{sid}.qc.json';q=read(qpath)
        if sha256(qpath)!=m['qc_sha256']:raise ValueError('QC checksum changed')
        if not q.get('included') or q.get('status')!='ok':
            excluded.append(dict(**subject,reason='clean_qc_excluded',valid_days=q.get('valid_days')));continue
        validate_clean_record(m,pipeline['clean'],ds,sid,split)
        parquet=base/f'{sid}.parquet'
        if sha256(parquet)!=m['parquet_sha256']:raise ValueError('Completed clean checksum changed')
        selected.append(dict(**subject,clean_path=str(parquet),qc_path=str(qpath),manifest_path=str(manifest),
            clean_sha256=m['parquet_sha256'],qc_sha256=m['qc_sha256'],manifest_sha256=sha256(manifest)))
    return selected,excluded


def extract_snapshot(selected,out,pipeline,refcfg):
    from tools.extract_reference_features import extract
    roster=load_roster(pipeline=pipeline)
    for i,s in enumerate(selected):
        extract(s['dataset'],s['subject_id'],Path(s['clean_path']),Path(s['qc_path']),out,roster,refcfg,pipeline)
        for key,pathkey in [('clean_sha256','clean_path'),('qc_sha256','qc_path'),('manifest_sha256','manifest_path')]:
            if sha256(s[pathkey])!=s[key]:raise ValueError('Source changed while taking snapshot')
        print(f'Verified {i+1}/{len(selected)} completed {s["split"]} subjects',flush=True)
    return roster


def prepare(cfg,p,pipeline):
    from src.reference.io import write_bundle
    source=ROOT/'reports/reference_baseline'/safe(cfg['source_run'])
    original=read(source/'run_manifest.json')
    for name,expected in original['split_hashes'].items():
        if sha256(ROOT/name)!=expected:raise ValueError('Original split/cohort metadata changed')
    historical=yaml.safe_load((source/'pipeline_config.yaml').read_text(encoding='utf-8'))
    if sha256(source/'pipeline_config.yaml')!=original['pipeline_config_sha256']:raise ValueError('Source config changed')
    manifest=p['report']/'input_manifest.json'
    if manifest.exists():
        check_snapshot(p);print('Reusing verified immutable input snapshot',flush=True);return
    if p['hourly'].exists() or (p['daily']/'train').exists():raise ValueError('Partial snapshot exists; use a new version, never backfill')
    refcfg=load_config(source/'reference_config.yaml');refcfg['baseline_version']='real-data-pilot-'+cfg['run_id']
    clean_root=ROOT/historical['clean']['out_dir']
    selected,excluded=completed_subjects(historical,clean_root,'train')
    if not selected:raise ValueError('No completed provenance/QC-passing real train')
    roster=load_roster(pipeline=historical)
    ids=set(s['subject_id'] for s in selected)
    if ids & set(roster.loc[roster.split.ne('train'),'subject_id']):raise ValueError('Train/held-out person overlap')
    write_json(p['report']/'selection_STARTED.json',dict(schema_version=1,created_utc=now(),data_origin='real',
        is_demo=False,selection='ALL completed QC-included original TRAIN subjects at snapshot time',selected=selected,excluded=excluded))
    roster=extract_snapshot(selected,p['hourly']/'train',historical,refcfg)
    intermediate=p['daily']/'daily_train'
    create_bundle(p['hourly']/'train',intermediate,roster,refcfg,historical,original['split_hashes'])
    daily,roster,refcfg,provenance=load_bundle(intermediate)
    if not daily.split.eq('train').all():raise ValueError('Non-train feature in snapshot')
    provenance={**provenance,'training_bundle_only':True}
    hour_qc=pd.read_csv(intermediate/'hour_qc.csv')
    write_bundle(p['daily']/'train',daily,hour_qc,roster,refcfg,historical,provenance)
    validate_real_fit_provenance(provenance,daily,refcfg)
    archive=prepare_script_archive(p['report']/'source.tar.gz')
    train=p['daily']/'train'
    record=dict(schema_version=1,created_utc=now(),data_origin='real',is_demo=False,run_id=cfg['run_id'],
        intended_job_name=cfg['job_name'],fit_split='train',code_revision=code_version(),
        preprocessing_version=provenance['preprocessing_version'],selected=selected,excluded=excluded,
        split_hashes=original['split_hashes'],train_heldout_overlap=[],
        pipeline_config_sha256=sha256(train/'pipeline_config.yaml'),reference_config_sha256=sha256(train/'reference_config.yaml'),
        bundle_files={f.name:sha256(f) for f in sorted(train.iterdir()) if f.is_file()},
        source_archive=archive,coverage=read(train/'coverage_report.json'),
        hourly_files=[dict(path=str(f),sha256=sha256(f)) for f in sorted((p['hourly']/'train').rglob('*')) if f.is_file()],
        runtime_policy=dict(instance_count=1,max_runtime_seconds=cfg['max_runtime_seconds'],automatic_job_retry=False),
        test_signal_contents_opened=False)
    write_json(manifest,record)
    write_json(p['report']/'resolved_config.json',dict(aws=cfg,storage=pipeline['storage'],reference=refcfg))
    env={**os.environ,'REFERENCE_CODE_REVISION':record['code_revision']['git_commit'],
        'REFERENCE_INPUT_MANIFEST_SHA256':sha256(manifest)}
    result=subprocess.run([sys.executable,'-u','-X','utf8',str(ROOT/'tools/sagemaker_fit_reference.py'),'train',
        '--bundle',str(train),'--model-dir',str(p['model']/'local_same_snapshot')],env=env)
    if result.returncode:raise RuntimeError('Local fit of frozen input failed')
    print('Immutable REAL train snapshot and local fit complete',flush=True)


def check_snapshot(p):
    m=read(p['report']/'input_manifest.json')
    for name,expected in m['bundle_files'].items():
        if Path(name).name!=name or sha256(p['daily']/'train'/name)!=expected:raise ValueError('Frozen training input changed')
    if sha256(p['report']/'source.tar.gz')!=m['source_archive']['sha256']:raise ValueError('Frozen source changed')
    daily,_,refcfg,provenance=load_bundle(p['daily']/'train')
    if not daily.split.eq('train').all() or not provenance.get('training_bundle_only'):raise ValueError('Input is not train-only')
    validate_real_fit_provenance(provenance,daily,refcfg)
    return m


def role_documents(cfg,pipeline,account):
    root=pipeline['storage']['s3_root'];bucket,prefix=root[5:].split('/',1);region=pipeline['storage']['region']
    trust=dict(Version='2012-10-17',Statement=[dict(Effect='Allow',Principal={'Service':'sagemaker.amazonaws.com'},
        Action='sts:AssumeRole',Condition={'StringEquals':{'aws:SourceAccount':account},
        'ArnEquals':{'aws:SourceArn':f'arn:aws:sagemaker:{region}:{account}:training-job/{cfg["job_name"]}'}})])
    input_prefixes=[prefix+'/'+cfg['input_relative'],prefix+'/'+str(Path(cfg['source_relative']).parent).replace('\\','/')]
    output=prefix+'/'+cfg['output_relative']
    policy=dict(Version='2012-10-17',Statement=[
        dict(Effect='Allow',Action=['s3:GetBucketLocation'],Resource=f'arn:aws:s3:::{bucket}'),
        dict(Effect='Allow',Action=['s3:ListBucket'],Resource=f'arn:aws:s3:::{bucket}',
            Condition={'StringLike':{'s3:prefix':[v+'*' for v in input_prefixes+[output]]}}),
        dict(Effect='Allow',Action=['s3:GetObject'],Resource=[f'arn:aws:s3:::{bucket}/{v}/*' for v in input_prefixes]),
        dict(Effect='Allow',Action=['s3:PutObject','s3:AbortMultipartUpload'],Resource=f'arn:aws:s3:::{bucket}/{output}/*'),
        dict(Effect='Allow',Action=['logs:CreateLogGroup','logs:CreateLogStream','logs:PutLogEvents'],
            Resource=[f'arn:aws:logs:{region}:{account}:log-group:/aws/sagemaker/TrainingJobs',f'arn:aws:logs:{region}:{account}:log-group:/aws/sagemaker/TrainingJobs:*']),
        dict(Effect='Allow',Action=['ecr:GetAuthorizationToken'],Resource='*'),
        dict(Effect='Allow',Action=['ecr:BatchCheckLayerAvailability','ecr:GetDownloadUrlForLayer','ecr:BatchGetImage'],
            Resource=f'arn:aws:ecr:{region}:121021644041:repository/sagemaker-scikit-learn')])
    return trust,policy


def preflight(cfg,p,pipeline,session):
    sts=session.client('sts').get_caller_identity();iam=session.client('iam');sq=session.client('service-quotas');sm=session.client('sagemaker')
    blockers=[];role=None
    try:
        role=iam.get_role(RoleName=cfg['role_arn'].split('/')[-1])['Role']
        services=[x for s in role['AssumeRolePolicyDocument']['Statement'] for x in ([s.get('Principal',{}).get('Service')] if isinstance(s.get('Principal',{}).get('Service'),str) else s.get('Principal',{}).get('Service',[]))]
        if role['Arn']!=cfg['role_arn'] or 'sagemaker.amazonaws.com' not in services:blockers.append('Execution role ARN/trust mismatch')
    except ClientError as e:
        if e.response['Error']['Code']!='NoSuchEntity':raise
        blockers.append('Execution role missing: '+cfg['role_arn'])
    quota=sq.get_service_quota(ServiceCode='sagemaker',QuotaCode=cfg['training_quota_code'])['Quota']
    if quota['Value']<1:blockers.append(f'Training quota {quota["QuotaCode"]}={quota["Value"]}; need 1')
    existing=None
    try:existing=sm.describe_training_job(TrainingJobName=cfg['job_name'])
    except ClientError as e:
        if e.response['Error']['Code'] not in ['ValidationException','ResourceNotFound']:raise
        if 'not found' not in str(e).lower() and 'could not find' not in str(e).lower():raise
    if existing:
        root=pipeline['storage']['s3_root'].rstrip('/')
        train=[v for v in existing['InputDataConfig'] if v['ChannelName']=='train']
        if len(train)!=1 or train[0]['DataSource']['S3DataSource']['S3Uri']!=root+'/'+cfg['input_relative']:
            raise ValueError('Existing job name belongs to different training inputs; do not submit or reuse')
        if (p['report']/'input_manifest.json').exists() and existing.get('Environment',{}).get('REFERENCE_INPUT_MANIFEST_SHA256')!=sha256(p['report']/'input_manifest.json'):
            raise ValueError('Existing job input manifest identity differs')
    pricing=session.client('pricing',region_name='us-east-1').get_products(ServiceCode='AmazonSageMaker',Filters=[
        dict(Type='TERM_MATCH',Field='regionCode',Value=pipeline['storage']['region']),
        dict(Type='TERM_MATCH',Field='instanceType',Value=cfg['instance_type']+'-Training')])
    prices=[json.loads(v) for v in pricing['PriceList']]
    rates=[float(d['pricePerUnit']['USD']) for v in prices for t in v['terms'].get('OnDemand',{}).values() for d in t['priceDimensions'].values() if d['unit']=='Hrs']
    if len(set(rates))!=1:raise ValueError('Cannot determine unique current TRAINING hourly rate')
    rate=rates[0]
    result=dict(schema_version=1,checked_utc=now(),account=sts['Account'],region=pipeline['storage']['region'],
        launcher_sdk={k:importlib.metadata.version(k) for k in ['boto3','botocore']},
        execution_role=cfg['role_arn'],role_found=role is not None,quota=quota,blockers=blockers,
        existing_job=dict(arn=existing['TrainingJobArn'],status=existing['TrainingJobStatus']) if existing else None,
        price_evidence=prices,cost_estimate=dict(currency='USD',training_per_hour=rate,
            assumed_billed_minutes=[5,10],compute_estimate_usd=[rate*5/60,rate*10/60],
            excluded='S3 requests/storage, EBS, CloudWatch, tax; not an actual bill or a hard total-cost cap'),
        status='BLOCKED_NOT_SUBMITTED' if blockers else 'READY')
    write_json(p['report']/'aws_preflight.json',result)
    trust,policy=role_documents(cfg,pipeline,sts['Account'])
    write_json(p['report']/'execution_role_trust.json',trust);write_json(p['report']/'execution_role_permissions.json',policy)
    print(json.dumps({k:result[k] for k in ['status','account','region','blockers','existing_job','cost_estimate']},indent=2),flush=True)
    return result


def provision(cfg,p,pipeline,session):
    """Only the one service role and one-instance quota needed by this authorized pilot."""
    account=session.client('sts').get_caller_identity()['Account'];iam=session.client('iam');sq=session.client('service-quotas')
    trust,policy=role_documents(cfg,pipeline,account)
    write_json(p['report']/'execution_role_trust.json',trust);write_json(p['report']/'execution_role_permissions.json',policy)
    role_name=cfg['role_arn'].split('/')[-1]
    try:role=iam.get_role(RoleName=role_name)['Role']
    except ClientError as e:
        if e.response['Error']['Code']!='NoSuchEntity':raise
        role=iam.create_role(RoleName=role_name,Path='/service-role/',AssumeRolePolicyDocument=json.dumps(trust),
            Description='Single real reference pilot; no endpoints; scoped input/output only',
            Tags=[dict(Key='ReferencePilotRun',Value=cfg['run_id'])])['Role']
        write_json(p['report']/'created_role.json',dict(arn=role['Arn'],created_utc=now(),trust=trust))
    if role['Arn']!=cfg['role_arn'] or role['AssumeRolePolicyDocument']!=trust:raise ValueError('Do not alter an existing role with different scope/trust')
    policy_name='ReferencePilotOnly'
    try:
        old=iam.get_role_policy(RoleName=role_name,PolicyName=policy_name)['PolicyDocument']
        if old!=policy:raise ValueError('Do not overwrite an existing different IAM policy')
    except ClientError as e:
        if e.response['Error']['Code']!='NoSuchEntity':raise
        iam.put_role_policy(RoleName=role_name,PolicyName=policy_name,PolicyDocument=json.dumps(policy))
    quota=sq.get_service_quota(ServiceCode='sagemaker',QuotaCode=cfg['training_quota_code'])['Quota']
    if quota['Value']<1:
        history=sq.list_requested_service_quota_change_history_by_quota(ServiceCode='sagemaker',QuotaCode=cfg['training_quota_code'])['RequestedQuotas']
        pending=[r for r in history if r['DesiredValue']>=1 and r['Status'] in ['PENDING','CASE_OPENED','APPROVED']]
        request=pending[0] if pending else sq.request_service_quota_increase(ServiceCode='sagemaker',QuotaCode=cfg['training_quota_code'],DesiredValue=1)['RequestedQuota']
        write_json(p['report']/'quota_request.json',json.loads(json.dumps(request,default=str)))
        print('Quota request:',request['Id'],request['Status'],flush=True)
    return preflight(cfg,p,pipeline,session)


def upload_file(s3,local,uri):
    bucket,key=uri[5:].split('/',1);digest=sha256(local)
    try:
        head=s3.head_object(Bucket=bucket,Key=key)
        if head.get('Metadata',{}).get('sha256')!=digest:raise ValueError('Refuse to overwrite different immutable S3 snapshot: '+uri)
        return
    except ClientError as e:
        if e.response['Error']['Code'] not in ['404','NoSuchKey','NotFound']:raise
    with Path(local).open('rb') as f:s3.put_object(Bucket=bucket,Key=key,Body=f,IfNoneMatch='*',Metadata={'sha256':digest})


def upload(cfg,p,pipeline,session):
    check_snapshot(p);root=pipeline['storage']['s3_root'].rstrip('/');s3=session.client('s3')
    for f in sorted((p['daily']/'train').iterdir()):
        if f.is_file():upload_file(s3,f,root+'/'+cfg['input_relative']+'/'+f.name)
    upload_file(s3,p['report']/'source.tar.gz',root+'/'+cfg['source_relative'])
    upload_file(s3,p['report']/'input_manifest.json',root+'/reports/reference_baseline/'+cfg['run_id']+'/input_manifest.json')
    print('Frozen real train input, input manifest and code uploaded',flush=True)


def submit(cfg,p,pipeline,session):
    check_snapshot(p);flight=preflight(cfg,p,pipeline,session)
    local=load_reference(p['model']/'local_same_snapshot/baseline.json')
    if local['data_kind']!='real' or local['fit_split']!='train':raise ValueError('Verified real local pilot is required')
    if flight['existing_job']:
        if not (p['report']/'submission.json').exists():
            write_json(p['report']/'submission.json',dict(job_name=cfg['job_name'],job_arn=flight['existing_job']['arn'],reused=True))
        print('Existing job reused; no new submission',flush=True);return
    if flight['blockers']:raise RuntimeError('No job submitted: '+'; '.join(flight['blockers']))
    upload(cfg,p,pipeline,session)
    request=build_request(pipeline,cfg)
    request['Environment']=dict(REFERENCE_CODE_REVISION=read(p['report']/'input_manifest.json')['code_revision']['git_commit'],
        REFERENCE_INPUT_MANIFEST_SHA256=sha256(p['report']/'input_manifest.json'))
    request['ProfilerConfig']={'DisableProfiler':True}
    # Omit RetryStrategy: opt-in automatic InternalServerError retries are disabled.
    write_json(p['report']/'training_request.json',request)
    intent=p['report']/'submission_intent.json'
    with intent.open('x',encoding='utf-8') as f:json.dump(dict(recorded_utc=now(),job_name=cfg['job_name'],request_sha256=sha256(p['report']/'training_request.json'),launcher_sha256=sha256(__file__)),f)
    client=session.client('sagemaker',config=Config(retries={'total_max_attempts':1}))
    response=client.create_training_job(**request)
    write_json(p['report']/'submission.json',dict(submitted_utc=now(),job_name=cfg['job_name'],job_arn=response['TrainingJobArn'],reused=False))
    print('SUBMITTED',response['TrainingJobArn'],flush=True)


def monitor(cfg,p,pipeline,session):
    sm=session.client('sagemaker');logs=session.client('logs');seen=set()
    while True:
        description=sm.describe_training_job(TrainingJobName=cfg['job_name'])
        write_json(p['report']/'job_description.json',json.loads(json.dumps(description,default=str)))
        print(now(),description['TrainingJobStatus'],description.get('SecondaryStatus'),flush=True)
        try:
            events=logs.filter_log_events(logGroupName='/aws/sagemaker/TrainingJobs',logStreamNamePrefix=cfg['job_name'])['events']
            with (p['report']/'cloudwatch.log').open('a',encoding='utf-8') as f:
                for e in events:
                    if e['eventId'] not in seen:f.write(e['message']+'\n');seen.add(e['eventId']);print(e['message'],flush=True)
        except ClientError as e:
            if e.response['Error']['Code'] not in ['ResourceNotFoundException','AccessDeniedException']:raise
            if e.response['Error']['Code']=='AccessDeniedException':
                write_json(p['report']/'cloudwatch_access_blocked.json',dict(recorded_utc=now(),reason=str(e)))
        if description['TrainingJobStatus'] in ['Completed','Failed','Stopped']:break
        time.sleep(20)
    if description['TrainingJobStatus']!='Completed':raise RuntimeError('Training did not complete: '+description.get('FailureReason','Stopped')+'; NO paid retry')
    model=p['model']/'aws';model.mkdir(parents=True,exist_ok=True)
    uri=description['ModelArtifacts']['S3ModelArtifacts'];bucket,key=uri[5:].split('/',1)
    session.client('s3').download_file(bucket,key,str(model/'model.tar.gz'))
    extract_model_archive(model/'model.tar.gz',model)
    aws=load_reference(model/'baseline.json');local=load_reference(p['model']/'local_same_snapshot/baseline.json')
    snapshot=read(p['report']/'input_manifest.json');runtime=read(model/'runtime_metadata.json')
    if aws['preprocessing_version']!=snapshot['preprocessing_version'] or aws['config_hash']!=local['config_hash']:
        raise ValueError('AWS preprocessing/config differs from frozen input')
    if runtime['baseline_sha256']!=sha256(model/'baseline.json') or runtime.get('input_manifest_sha256')!=sha256(p['report']/'input_manifest.json'):
        raise ValueError('AWS runtime baseline/input identity mismatch')
    if runtime.get('declared_code_revision')!=snapshot['code_revision']['git_commit']:
        raise ValueError('AWS runtime code revision metadata mismatch')
    maximum_error=compare_numeric(aws['references'],local['references'],cfg['numerical_rtol'],cfg['numerical_atol'])
    if aws['fit_split']!='train' or aws['data_kind']!='real':raise ValueError('Wrong AWS model origin/split')
    result=dict(schema_version=1,status='COMPLETED_NUMERICAL_PARITY_PASSED',job_name=cfg['job_name'],job_arn=description['TrainingJobArn'],
        model_s3_uri=uri,aws_model_archive_sha256=sha256(model/'model.tar.gz'),
        aws_baseline_sha256=sha256(model/'baseline.json'),local_baseline_sha256=sha256(p['model']/'local_same_snapshot/baseline.json'),
        artifact_bytes_expected_to_differ='Creation timestamp and runtime/code metadata differ; compare all reference entries/summaries within tolerance',
        rtol=cfg['numerical_rtol'],atol=cfg['numerical_atol'],training_seconds=description.get('TrainingTimeInSeconds'),
        billable_seconds=description.get('BillableTimeInSeconds'),test_evaluated=False,endpoint_created=False,
        input_manifest_sha256=sha256(p['report']/'input_manifest.json'),resolved_config=cfg,
        runtime_metadata=read(model/'runtime_metadata.json'),
        input_manifest_s3_uri=pipeline['storage']['s3_root'].rstrip('/')+'/reports/reference_baseline/'+cfg['run_id']+'/input_manifest.json',
        reference_counts={ds:{metric:v['summary'] for metric,v in metrics.items()} for ds,metrics in aws['references'].items()})
    result['maximum_reference_absolute_difference']=maximum_error
    price=read(p['report']/'aws_preflight.json')['cost_estimate']['training_per_hour']
    result['estimated_compute_cost_usd']=price*description['BillableTimeInSeconds']/3600 if description.get('BillableTimeInSeconds') is not None else None
    result['cost_note']='Estimate from AWS-reported billable seconds and observed list rate; excludes storage/logs/taxes; not an actual invoice.'
    verified_prefix=pipeline['storage']['s3_root'].rstrip('/')+'/data/models/reference-v1/'+cfg['run_id']+'/verified'
    for name in ['baseline.json','fit_report.json','runtime_metadata.json']:
        upload_file(session.client('s3'),model/name,verified_prefix+'/'+name)
    result['baseline_json_s3_uri']=verified_prefix+'/baseline.json'
    write_json(p['report']/'aws_result.json',result)
    validate(cfg,p,pipeline,session,aws,local)


def extract_model_archive(archive,destination):
    """Read only the three expected root JSONs; never extract SDK code or links."""
    wanted={'baseline.json','fit_report.json','runtime_metadata.json'};contents={}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            name=PurePosixPath(member.name)
            if len(name.parts)!=1 or name.name not in wanted:continue
            if not member.isfile() or member.size>50_000_000 or name.name in contents:
                raise ValueError('Unexpected/duplicate model JSON member')
            contents[name.name]=tar.extractfile(member).read()
    if set(contents)!=wanted:raise ValueError('Required baseline/runtime JSON missing from model archive')
    for name,data in contents.items():
        target=Path(destination)/name
        if target.exists() and target.read_bytes()!=data:raise ValueError('Refuse to replace different verified model content')
        if not target.exists():target.write_bytes(data)


def compare_numeric(a,b,rtol,atol):
    if isinstance(a,dict):
        if not isinstance(b,dict) or set(a)!=set(b):raise ValueError('Reference keys differ')
        return max((compare_numeric(a[k],b[k],rtol,atol) for k in a),default=0.)
    elif isinstance(a,list):
        if not isinstance(b,list) or len(a)!=len(b):raise ValueError('Reference lengths differ')
        return max((compare_numeric(x,y,rtol,atol) for x,y in zip(a,b)),default=0.)
    elif isinstance(a,(int,float)) and not isinstance(a,bool):
        if not isinstance(b,(int,float)) or not np.isclose(a,b,rtol=rtol,atol=atol,equal_nan=False):raise ValueError('Numerical parity failed')
        return abs(float(a)-float(b))
    elif a!=b:raise ValueError('Status, unit, identity or null semantics differ')
    return 0.


def verify_local(cfg,p,pipeline,session):
    check_snapshot(p)
    main=p['model']/'local_same_snapshot';compatible=p['model']/'local_py312_compat'
    first=load_reference(main/'baseline.json');second=load_reference(compatible/'baseline.json')
    maximum=compare_numeric(first['references'],second['references'],cfg['numerical_rtol'],cfg['numerical_atol'])
    write_json(p['report']/'local_dependency_parity.json',dict(schema_version=1,checked_utc=now(),
        scope='LOCAL_PYTHON_DEPENDENCY_COMPATIBILITY_NOT_AWS_EXECUTION',data_origin='real',is_demo=False,
        passed=True,rtol=cfg['numerical_rtol'],atol=cfg['numerical_atol'],maximum_absolute_difference=maximum,
        baseline_sha256_main=sha256(main/'baseline.json'),baseline_sha256_python312=sha256(compatible/'baseline.json'),
        main_runtime=read(main/'runtime_metadata.json'),python312_runtime=read(compatible/'runtime_metadata.json'),
        aws_job_executed=False,test_evaluated=False))
    print('LOCAL dependency parity passed; max absolute difference',maximum,'; this is NOT an AWS execution',flush=True)


def validate(cfg,p,pipeline,session,aws,local):
    from src.reference.output import build_analysis
    before=sha256(p['model']/'aws/baseline.json')
    bundle=p['daily']/'valid'
    daily,_,refcfg,provenance=load_bundle(bundle)
    if set(daily.subject_id)!={'COPS-7','NHANES-81262'} or not daily.split.eq('valid').all():
        raise ValueError('Validator requires exactly COPS-7 and NHANES-81262 validation only')
    if refcfg!=aws['config'] or provenance['preprocessing_version']!=aws['preprocessing_version']:
        raise ValueError('Validation config/preprocessing differs from frozen train model')
    selected=[dict(dataset=m['dataset'],subject_id=m['subject_id'],split=m['split']) for m in provenance['sources']]
    excluded=[]  # Explicit requested validation subset, not a census of unfinished participants.
    cloud=build_analysis(daily,aws);same=build_analysis(daily,local)
    if len(cloud['analysis'])!=len(same['analysis']):raise ValueError('Validation row count differs')
    for key in ['schema_version','data_origin','is_demo','baseline_version']:
        if cloud[key]!=same[key]:raise ValueError('Validation metadata differs')
    for a,b in zip(cloud['analysis'],same['analysis']):
        a={k:v for k,v in a.items() if k!='artifact_id'};b={k:v for k,v in b.items() if k!='artifact_id'}
        compare_numeric(a,b,cfg['numerical_rtol'],cfg['numerical_atol'])
    if before!=sha256(p['model']/'aws/baseline.json'):raise ValueError('Baseline changed during validation')
    write_json(p['report']/'analysis_VALIDATION_AWS_REAL.json',cloud)
    write_json(p['report']/'validation_report.json',dict(schema_version=1,data_origin='real',is_demo=False,
        job_arn=read(p['report']/'aws_result.json')['job_arn'],baseline_sha256=before,baseline_unchanged=True,
        selected=selected,excluded=excluded,coverage=read(bundle/'coverage_report.json'),rows=len(cloud['analysis']),
        selection_policy='Requested validator subset: COPS-7 and NHANES-81262 only',
        schema_version_analysis=cloud['schema_version'],numerical_parity_passed=True,test_evaluated=False,endpoint_created=False))
    root=pipeline['storage']['s3_root'].rstrip('/');s3=session.client('s3')
    for name in ['aws_result.json','validation_report.json','analysis_VALIDATION_AWS_REAL.json','job_description.json']:
        upload_file(s3,p['report']/name,root+'/reports/reference_baseline/'+cfg['run_id']+'/'+name)
    print('AWS real baseline and validation complete; no test or endpoint',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'config/sagemaker_reference_pilot.yaml')
    parser.add_argument('--stage',choices=['prepare','preflight','provision','upload','submit','monitor','verify-local'],required=True)
    args=parser.parse_args();cfg,p,pipeline,session=context(args.config)
    for stream in [sys.stdout,sys.stderr]:
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8')
    try:
        if args.stage=='prepare':prepare(cfg,p,pipeline)
        else:globals()[args.stage.replace('-','_')](cfg,p,pipeline,session)
    except Exception as e:
        write_json(p['report']/f'blocked_{args.stage}.json',dict(recorded_utc=now(),stage=args.stage,
            exception_type=type(e).__name__,reason=str(e),job_submission_record_exists=(p['report']/'submission.json').exists()))
        raise


if __name__=='__main__':main()
