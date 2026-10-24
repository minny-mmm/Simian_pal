"""Deploy one authenticated Lambda/API demo; never fit or submit training jobs."""
import argparse
import base64
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import secrets
import shutil
import sys
import tarfile
import time
import zipfile
import re

import boto3
from botocore.exceptions import ClientError
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.reference.common import sha256,write_json
from tools.run_reference_aws import upload_file,check_snapshot


def context():
    c=yaml.safe_load((ROOT/'config/reference_demo.yaml').read_text(encoding='utf-8'))
    p=ROOT/'reports/deployment'/c['deployment_id'];p.mkdir(parents=True,exist_ok=True)
    return c,p,boto3.Session(profile_name=c['profile'],region_name=c['region'])


def build(c,p,s):
    run=ROOT/'reports/reference_baseline/aws-real-pilot-20261024-v1'
    snapshot=json.loads((run/'input_manifest.json').read_text(encoding='utf-8'))
    archive=run/'source.tar.gz'
    if sha256(archive)!=snapshot['source_archive']['sha256']:raise ValueError('Frozen source changed')
    target=p/'build'
    if not (target/'numpy').exists():raise ValueError('Install Linux wheels into deployment build directory first')
    if not (target/'typing_extensions.py').exists():raise ValueError('Python3.12 schema runtime requires explicit typing_extensions in cross-built ZIP')
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            name=PurePosixPath(member.name)
            if not member.isfile() or '..' in name.parts or name.is_absolute():raise ValueError('Unsafe frozen archive')
            if not (str(name).startswith('src/') or str(name)=='tools/reference_baseline.py'):continue
            dest=target/str(name);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(tar.extractfile(member).read())
    for name in ['app.py','app.js','index.html']:shutil.copyfile(ROOT/'aws/demo'/name,target/name)
    package=p/'function.zip'
    files={}
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for file in sorted(target.rglob('*')):
            if not file.is_file() or '__pycache__' in file.parts or 'tests' in file.relative_to(target).parts or file.suffix=='.pyc':continue
            name=file.relative_to(target).as_posix();z.write(file,name);files[name]=sha256(file)
    with zipfile.ZipFile(package) as zipped:size=sum(info.file_size for info in zipped.infolist())
    if size>=250*1024**2:raise ValueError('Lambda uncompressed package too large')
    write_json(p/'build_manifest.json',dict(package_sha256=sha256(package),zip_bytes=package.stat().st_size,
        uncompressed_bytes=size,frozen_source_sha256=sha256(archive),files=files))
    print('Built Lambda ZIP:',package.stat().st_size,'bytes; uncompressed',size,flush=True)


def refresh(c,p,s):
    """Replace only HTTP/UI files in a verified ZIP; keep scientific code/dependencies."""
    manifest=json.loads((p/'build_manifest.json').read_text(encoding='utf-8'))
    if sha256(p/'function.zip')!=manifest['package_sha256']:raise ValueError('Deployment ZIP was modified')
    destination=p/'function-refreshed.zip'
    with zipfile.ZipFile(p/'function.zip') as old, zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as new:
        for info in old.infolist():
            data=(ROOT/'aws/demo'/info.filename).read_bytes() if info.filename in ['app.py','app.js','index.html'] else old.read(info.filename)
            new.writestr(info.filename,data)
            if info.filename in ['app.py','app.js','index.html']:manifest['files'][info.filename]=hashlib.sha256(data).hexdigest()
    destination.replace(p/'function.zip')
    manifest['package_sha256']=sha256(p/'function.zip');manifest['zip_bytes']=(p/'function.zip').stat().st_size
    with zipfile.ZipFile(p/'function.zip') as zipped:manifest['uncompressed_bytes']=sum(info.file_size for info in zipped.infolist())
    write_json(p/'build_manifest.json',manifest)
    print('Refreshed HTTP/UI only in verified package',flush=True)


def deploy(c,p,s):
    # Publish a config release, not a replacement baseline or a change to the validator package.
    with zipfile.ZipFile(p/'function.zip') as z:
        if hashlib.sha256(z.read('app.py')).hexdigest()!=sha256(ROOT/'aws/demo/app.py'):
            raise ValueError('HTTP adapter changed after build; rebuild explicitly')
    run=ROOT/'data/daily/reference-v1/aws-real-pilot-20261024-v1/valid'
    from src.reference.io import load_bundle
    from src.reference.model import load_reference
    daily,_,cfg,provenance=load_bundle(run)
    baseline=load_reference(ROOT/'data/models/reference-v1/aws-real-pilot-20261024-v1/local_py312_compat/baseline.json')
    if baseline['config']!=cfg or baseline['preprocessing_version']!=provenance['preprocessing_version']:raise ValueError('Incompatible baseline')
    if sha256(ROOT/'data/models/reference-v1/aws-real-pilot-20261024-v1/local_py312_compat/baseline.json')!=c['baseline_sha256']:raise ValueError('Local baseline changed')
    bucket,key=c['baseline_uri'][5:].split('/',1)
    remote=s.client('s3').get_object(Bucket=bucket,Key=key)['Body'].read()
    if hashlib.sha256(remote).hexdigest()!=c['baseline_sha256']:raise ValueError('S3 baseline changed')
    state_path=p/'deployment.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    account=s.client('sts').get_caller_identity()['Account'];role_arn=f'arn:aws:iam::{account}:role/service-role/{c["role_name"]}'
    def save():write_json(state_path,state)
    secret=s.client('secretsmanager')
    if not state.get('secret_arn'):
        try:r=secret.describe_secret(SecretId=c['auth_secret_name'])
        except secret.exceptions.ResourceNotFoundException:
            r=secret.create_secret(Name=c['auth_secret_name'],SecretString=secrets.token_urlsafe(32),Description='Private demo login; never place in URL or logs',Tags=[dict(Key='Deployment',Value=c['deployment_id'])])
        state['secret_arn']=r['ARN'];save()
    ddb=s.client('dynamodb')
    try:ddb.describe_table(TableName=c['budget_table'])
    except ddb.exceptions.ResourceNotFoundException:
        ddb.create_table(TableName=c['budget_table'],KeySchema=[dict(AttributeName='pk',KeyType='HASH')],
            AttributeDefinitions=[dict(AttributeName='pk',AttributeType='S')],BillingMode='PAY_PER_REQUEST',Tags=[dict(Key='Deployment',Value=c['deployment_id'])])
    ddb.get_waiter('table_exists').wait(TableName=c['budget_table'],WaiterConfig=dict(Delay=2,MaxAttempts=30))
    state['budget_table']=c['budget_table'];save()
    release=Path(state['active_release_path']) if state.get('active_release_path') else p/'release_config.json'
    if not release.exists():
        manifest=json.loads((run/'bundle.manifest.json').read_text(encoding='utf-8'))
        write_json(release,dict(schema_version=1,baseline_uri=c['baseline_uri'],baseline_sha256=c['baseline_sha256'],baseline_fit_location='local',
            valid_bundle_uri=c['valid_bundle_uri'],bundle_files={**manifest['files'],'bundle.manifest.json':sha256(run/'bundle.manifest.json')},
            cache_uri=c['storage_prefix']+'/cache',llm_model=c['llm_model'],llm_enabled=c['llm_enabled'],
            llm_region=c.get('llm_region',c['region']),llm_auth_mode='runtime_iam_role',
            llm_max_output_tokens=c.get('llm_max_output_tokens',1000),
            llm_calls_per_day=c['llm_calls_per_day'],expires_at=(datetime.now(timezone.utc)+timedelta(hours=c['expires_after_hours'])).isoformat()))
    release_data=json.loads(release.read_text(encoding='utf-8'))
    config_uri=c['storage_prefix']+'/releases/'+sha256(release)+'.json'
    code_uri=c['storage_prefix']+'/code/'+sha256(p/'function.zip')+'.zip'
    upload_file(s.client('s3'),release,config_uri);upload_file(s.client('s3'),p/'function.zip',code_uri)
    def objarn(uri):return 'arn:aws:s3:::'+uri[5:]
    profile=f'arn:aws:bedrock:{c["region"]}:{account}:inference-profile/{c["llm_model"]}'
    model_arns=[v['modelArn'] for v in s.client('bedrock').get_inference_profile(inferenceProfileIdentifier=c['llm_model'])['models']]
    policy=dict(Version='2012-10-17',Statement=[
        dict(Effect='Allow',Action=['s3:GetObject'],Resource=[objarn(c['baseline_uri']),objarn(config_uri),objarn(c['valid_bundle_uri']+'/*'),objarn(c['storage_prefix']+'/cache/*')]),
        dict(Effect='Allow',Action=['s3:PutObject'],Resource=objarn(c['storage_prefix']+'/cache/*')),
        dict(Effect='Allow',Action=['secretsmanager:GetSecretValue'],Resource=state['secret_arn']),
        dict(Effect='Allow',Action=['dynamodb:UpdateItem'],Resource=f'arn:aws:dynamodb:{c["region"]}:{account}:table/{c["budget_table"]}'),
        dict(Effect='Allow',Action=['bedrock:InvokeModel'],Resource=profile),
        dict(Effect='Allow',Action=['bedrock:InvokeModel'],Resource=model_arns,Condition={'StringEquals':{'bedrock:InferenceProfileArn':profile}}),
        dict(Effect='Allow',Action=['logs:CreateLogStream','logs:PutLogEvents'],Resource=f'arn:aws:logs:{c["region"]}:{account}:log-group:/aws/lambda/{c["function_name"]}:*')])
    trust=dict(Version='2012-10-17',Statement=[dict(Effect='Allow',Principal=dict(Service='lambda.amazonaws.com'),Action='sts:AssumeRole')])
    write_json(p/'execution_role_permissions.json',policy);write_json(p/'execution_role_trust.json',trust)
    iam=s.client('iam')
    try:role=iam.get_role(RoleName=c['role_name'])['Role']
    except iam.exceptions.NoSuchEntityException:role=iam.create_role(RoleName=c['role_name'],Path='/service-role/',AssumeRolePolicyDocument=json.dumps(trust),Tags=[dict(Key='Deployment',Value=c['deployment_id'])])['Role']
    if role['Arn']!=role_arn or role['AssumeRolePolicyDocument']!=trust:raise ValueError('Different existing execution role; refuse modification')
    iam.put_role_policy(RoleName=c['role_name'],PolicyName='ReferenceDemoOnly',PolicyDocument=json.dumps(policy))
    state['role_arn']=role_arn;save()
    logs=s.client('logs');group='/aws/lambda/'+c['function_name']
    try:logs.create_log_group(logGroupName=group)
    except logs.exceptions.ResourceAlreadyExistsException:pass
    logs.put_retention_policy(logGroupName=group,retentionInDays=3)
    lam=s.client('lambda');b,k=code_uri[5:].split('/',1)
    env=dict(DEPLOYMENT_CONFIG_URI=config_uri,DEPLOYMENT_CONFIG_SHA256=sha256(release),AUTH_SECRET_ARN=state['secret_arn'],BUDGET_TABLE=c['budget_table'],
             DEMO_EXPIRES_EPOCH=str(int(datetime.fromisoformat(release_data['expires_at']).timestamp())),OPENBLAS_NUM_THREADS='1')
    try:existing=lam.get_function(FunctionName=c['function_name'])
    except lam.exceptions.ResourceNotFoundException:existing=None
    if existing:
        if existing['Configuration']['Role']!=role_arn:raise ValueError('Different existing function owner')
        actual=base64.b64encode(bytes.fromhex(sha256(p/'function.zip'))).decode()
        if existing['Configuration']['CodeSha256']!=actual:
            lam.update_function_code(FunctionName=c['function_name'],S3Bucket=b,S3Key=k)
            lam.get_waiter('function_updated_v2').wait(FunctionName=c['function_name'])
        lam.update_function_configuration(FunctionName=c['function_name'],Environment=dict(Variables=env),MemorySize=c['memory_mb'],Timeout=c['timeout_seconds'])
        lam.get_waiter('function_updated_v2').wait(FunctionName=c['function_name'])
    else:
        # IAM propagation is bounded, and no training job is involved.
        for attempt in range(6):
            try:
                lam.create_function(FunctionName=c['function_name'],Runtime='python3.12',Role=role_arn,Handler='app.handler',
                    Code=dict(S3Bucket=b,S3Key=k),Timeout=c['timeout_seconds'],MemorySize=c['memory_mb'],Architectures=['x86_64'],
                    EphemeralStorage=dict(Size=c['ephemeral_storage_mb']),Environment=dict(Variables=env),Tags={'Deployment':c['deployment_id']})
                break
            except lam.exceptions.InvalidParameterValueException as e:
                if 'cannot be assumed' not in str(e) or attempt==5:raise
                time.sleep(3)
        lam.get_waiter('function_active_v2').wait(FunctionName=c['function_name'])
    function_arn=f'arn:aws:lambda:{c["region"]}:{account}:function:{c["function_name"]}'
    state.update(function_arn=function_arn,config_uri=config_uri,config_sha256=sha256(release),code_sha256=sha256(p/'function.zip'),expires_at=release_data['expires_at']);save()
    api=s.client('apigatewayv2')
    if not state.get('api_id'):
        matches=[v for v in api.get_apis()['Items'] if v['Name']==c['api_name']]
        if matches:raise ValueError('API exists without deployment record; inspect before reusing')
        a=api.create_api(Name=c['api_name'],ProtocolType='HTTP',Target=function_arn)
        state.update(api_id=a['ApiId'],url=a['ApiEndpoint']);save()
    api.update_stage(ApiId=state['api_id'],StageName='$default',DefaultRouteSettings=dict(ThrottlingBurstLimit=c['api_burst'],ThrottlingRateLimit=c['api_requests_per_second']))
    try:lam.add_permission(FunctionName=c['function_name'],StatementId='DemoApiOnly',Action='lambda:InvokeFunction',Principal='apigateway.amazonaws.com',SourceArn=f'arn:aws:execute-api:{c["region"]}:{account}:{state["api_id"]}/*/*')
    except lam.exceptions.ResourceConflictException:pass
    print('DEPLOYED',state['url'],'(login required; no public patient API)',flush=True)


def bedrock(c,p,s):
    """Explicitly enable the verified Nova Lite release; preserve baseline/data/expiry."""
    state=json.loads((p/'deployment.json').read_text(encoding='utf-8'))
    verified=json.loads((p/'nova_lite_discovery_probe.json').read_text(encoding='utf-8'))
    if not verified['synthetic_probe']['success'] or verified['profile']['inferenceProfileId']!=c['llm_model']:
        raise ValueError('Require successful discovery/probe for selected exact profile')
    profile=s.client('bedrock').get_inference_profile(inferenceProfileIdentifier=c['llm_model'])
    if profile['inferenceProfileArn']!=verified['profile']['inferenceProfileArn'] or profile['models']!=verified['profile']['models']:
        raise ValueError('Inference profile destinations changed; review again')
    b,k=state['config_uri'][5:].split('/',1);raw=s.client('s3').get_object(Bucket=b,Key=k)['Body'].read()
    if hashlib.sha256(raw).hexdigest()!=state['config_sha256']:raise ValueError('Current release hash differs')
    previous=json.loads(raw);release=dict(previous)
    release.update(llm_provider='amazon_bedrock',llm_region=c['llm_region'],llm_model=c['llm_model'],
        llm_auth_mode='runtime_iam_role',llm_enabled=True,llm_max_output_tokens=c['llm_max_output_tokens'],llm_calls_per_day=c['llm_calls_per_day'])
    for key in ['baseline_uri','baseline_sha256','baseline_fit_location','valid_bundle_uri','bundle_files','expires_at']:
        if previous[key]!=release[key]:raise ValueError('Do not change baseline/validation/expiry during LLM enablement')
    path=p/'release_bedrock_nova_lite_v1.json'
    if path.exists() and json.loads(path.read_text(encoding='utf-8'))!=release:raise ValueError('LLM release already differs; use a new version')
    write_json(path,release);uri=c['storage_prefix']+'/releases/'+sha256(path)+'.json'
    upload_file(s.client('s3'),path,uri)
    iam=s.client('iam');policy=iam.get_role_policy(RoleName=c['role_name'],PolicyName='ReferenceDemoOnly')['PolicyDocument']
    before=p/'execution_role_permissions_BEFORE_NOVA_LITE.json'
    if not before.exists():write_json(before,policy)
    statements=[]
    for statement in policy['Statement']:
        actions=statement.get('Action',[])
        if isinstance(actions,str):actions=[actions]
        if any(a.startswith('bedrock:') for a in actions):continue
        if 's3:GetObject' in actions:
            values=statement['Resource']
            if isinstance(values,str):values=[values]
            statement=dict(statement,Resource=list(dict.fromkeys(values+['arn:aws:s3:::'+uri[5:]])))
        statements.append(statement)
    statements.extend([
        dict(Effect='Allow',Action=['bedrock:InvokeModel'],Resource=profile['inferenceProfileArn']),
        dict(Effect='Allow',Action=['bedrock:InvokeModel'],Resource=[v['modelArn'] for v in profile['models']],
             Condition={'StringEquals':{'bedrock:InferenceProfileArn':profile['inferenceProfileArn']}})])
    policy=dict(Version='2012-10-17',Statement=statements)
    write_json(p/'execution_role_permissions_NOVA_LITE.json',policy)
    iam.put_role_policy(RoleName=c['role_name'],PolicyName='ReferenceDemoOnly',PolicyDocument=json.dumps(policy))
    lam=s.client('lambda');actual=lam.get_function_configuration(FunctionName=c['function_name'])
    if actual['Role']!=state['role_arn']:raise ValueError('Unexpected runtime role')
    env=actual['Environment']['Variables'];env.update(DEPLOYMENT_CONFIG_URI=uri,DEPLOYMENT_CONFIG_SHA256=sha256(path))
    lam.update_function_configuration(FunctionName=c['function_name'],Environment=dict(Variables=env))
    lam.get_waiter('function_updated_v2').wait(FunctionName=c['function_name'])
    state.update(config_uri=uri,config_sha256=sha256(path),active_release_path=str(path),llm_model=profile['inferenceProfileId'],llm_auth_mode='runtime_iam_role')
    write_json(p/'deployment.json',state)
    write_json(p/'nova_lite_release.json',dict(profile_id=profile['inferenceProfileId'],profile_arn=profile['inferenceProfileArn'],
        destination_model_arns=[v['modelArn'] for v in profile['models']],runtime_role_arn=state['role_arn'],
        action='bedrock:InvokeModel',release_uri=uri,release_sha256=sha256(path),baseline_unchanged=True,
        runtime_probe_still_required=True,api_key_or_llm_secret_created=False))
    print('Enabled verified Nova Lite config and scoped runtime IAM; run synthetic runtime probe next',flush=True)


def update(c,p,s):
    """Update only this demo's code, retaining pinned data/config and infrastructure."""
    state=json.loads((p/'deployment.json').read_text(encoding='utf-8'))
    lam=s.client('lambda');function=lam.get_function(FunctionName=c['function_name'])
    if function['Configuration']['Role']!=state['role_arn']:raise ValueError('Unexpected function role')
    with zipfile.ZipFile(p/'function.zip') as z:
        if hashlib.sha256(z.read('app.py')).hexdigest()!=sha256(ROOT/'aws/demo/app.py'):raise ValueError('Rebuild adapter before updating')
    code_uri=c['storage_prefix']+'/code/'+sha256(p/'function.zip')+'.zip'
    upload_file(s.client('s3'),p/'function.zip',code_uri)
    b,k=code_uri[5:].split('/',1)
    lam.update_function_code(FunctionName=c['function_name'],S3Bucket=b,S3Key=k)
    lam.get_waiter('function_updated_v2').wait(FunctionName=c['function_name'])
    state['code_sha256']=sha256(p/'function.zip');write_json(p/'deployment.json',state)
    print('Updated demo code only; baseline and release config unchanged',flush=True)


def report(c,p,s):
    """Read actual AWS state and bind it to the latest completed E2E evidence."""
    state=json.loads((p/'deployment.json').read_text(encoding='utf-8'))
    e2e=json.loads((p/'e2e_report.json').read_text(encoding='utf-8'))
    release_path=Path(state.get('active_release_path',p/'release_config.json'))
    release=json.loads(release_path.read_text(encoding='utf-8'))
    function=s.client('lambda').get_function_configuration(FunctionName=c['function_name'])
    if function['CodeSha256']!=base64.b64encode(bytes.fromhex(sha256(p/'function.zip'))).decode():raise ValueError('AWS code hash differs')
    env=function['Environment']['Variables']
    if env['DEPLOYMENT_CONFIG_SHA256']!=sha256(release_path) or env['DEPLOYMENT_CONFIG_URI']!=state['config_uri']:
        raise ValueError('AWS active release differs')
    if e2e['baseline_sha256']!=release['baseline_sha256'] or e2e['runtime']['llm_model']!=release['llm_model']:
        raise ValueError('E2E evidence belongs to a different model/release')
    if e2e['explanation_generation_kind']=='llm' and not e2e['explanation_validation'].get('directional_grounding_guard'):
        raise ValueError('Cannot finalize LLM output without directional semantic guards')
    original=ROOT/'reports/reference_baseline/aws-real-pilot-20261024-v1'
    delivery=json.loads((original/'validator_delivery_manifest.json').read_text(encoding='utf-8'));unchanged={}
    for name in ['VALIDATOR_HANDOFF.md','VALIDATOR_HANDOFF.json','validator_valid_bundle.json','validator_provenance_manifest.json']:
        expected=next(v['sha256'] for v in delivery['files'] if v['uri'].endswith('/'+name))
        if sha256(original/name)!=expected:raise ValueError('Validator file changed')
        unchanged[name]=expected
    model_manifest=json.loads((original/'input_manifest.json').read_text(encoding='utf-8'))
    for name,expected in model_manifest['bundle_files'].items():
        if sha256(ROOT/'data/daily/reference-v1/aws-real-pilot-20261024-v1/train'/name)!=expected:raise ValueError('Frozen train changed')
    if sha256(original/'source.tar.gz')!=model_manifest['source_archive']['sha256']:raise ValueError('Frozen source changed')
    logs=(p/'lambda_measurement.log').read_text(encoding='utf-8')
    memory=re.search(r'Max Memory Used: (\d+) MB',logs);duration=re.search(r'\bDuration: ([\d.]+) ms',logs)
    e2e.update(aws_reported_peak_memory_mb=int(memory[1]),aws_warm_status_duration_ms=float(duration[1]),
               llm_integration_status='VERIFIED_RUNTIME_IAM_MODEL_OUTPUT' if e2e['explanation_generation_kind']=='llm' else 'EXPLICIT_TEMPLATE_FALLBACK')
    write_json(p/'e2e_report.json',e2e)
    rates={}
    for product in json.loads((p/'nova_lite_price_evidence.json').read_text(encoding='utf-8')):
        a=product['product']['attributes']
        if a['feature']!='On-demand Inference' or a['inferenceType'] not in ['Input tokens','Output tokens']:continue
        for term in product['terms']['OnDemand'].values():
            for dimension in term['priceDimensions'].values():
                if dimension['unit']!='1K tokens':raise ValueError('Unexpected model pricing unit')
                rates[a['inferenceType']]=float(dimension['pricePerUnit']['USD'])
    usage=e2e['explanation_validation'].get('usage')
    price=dict(currency='USD',per_1000_input_tokens=rates['Input tokens'],per_1000_output_tokens=rates['Output tokens'],
        credits_coverage_verified=False,assumed_2000_input_1000_output_twenty_calls_usd_per_day=20*(2*rates['Input tokens']+rates['Output tokens']),
        recorded_generation_estimated_usd=(usage['inputTokens']*rates['Input tokens']+usage['outputTokens']*rates['Output tokens'])/1000 if usage else None,
        note='List-rate estimate, not invoice; rejected generations may also incur charges. Other calls/services excluded.')
    policy=s.client('iam').get_role_policy(RoleName=c['role_name'],PolicyName='ReferenceDemoOnly')['PolicyDocument']
    stage=s.client('apigatewayv2').get_stage(ApiId=state['api_id'],StageName='$default')
    out=dict(schema_version=1,checked_utc=datetime.now(timezone.utc).isoformat(),deployment_mode='demo',data_origin='real',
        demo_url=state['url'],function={k:function.get(k) for k in ['FunctionArn','Runtime','Architectures','State','LastUpdateStatus','MemorySize','Timeout','CodeSha256','EphemeralStorage']},
        api_stage={k:stage.get(k) for k in ['StageName','AutoDeploy','DefaultRouteSettings']},runtime_role_arn=state['role_arn'],
        runtime_role_policy=policy,provider='Amazon Bedrock',region=c['region'],model_id=release['llm_model'],auth_mode='runtime_iam_role',
        llm_secret_created=False,baseline_fit_location=release['baseline_fit_location'],aws_training_started_by_deployment=False,
        config_uri=state['config_uri'],config_sha256=sha256(release_path),expires_at=state['expires_at'],
        validator_files_unchanged=unchanged,frozen_train_source_unchanged=True,e2e=e2e,model_cost_estimate=price)
    current=p/'deployment_validation.json';old=p/'deployment_validation_TEMPLATE_RELEASE.json'
    if current.exists() and not old.exists():shutil.copyfile(current,old)
    write_json(current,out)
    print(json.dumps(dict(url=state['url'],provider=out['provider'],model=release['llm_model'],
        auth='runtime_iam_role',layer_b=e2e['explanation_generation_kind'],peak_mb=int(memory[1]),warm_ms=float(duration[1]),model_price=price),indent=2),flush=True)


def publish(c,p,s):
    report(c,p,s)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');prefix=c['storage_prefix']+'/handoffs/'+stamp
    names=['DEPLOYMENT_HANDOFF.md','deployment_validation.json','e2e_report.json','analysis_DEPLOYED_REAL.json',
        'explanation_DEPLOYED_REAL.json','explanation_MODEL.json','explanation_MODEL_validation.json',
        'explanation_TEMPLATE_BEFORE_BEDROCK.json','explanation_MODEL_REJECTED_SEMANTICS_v1.json',
        'raw_model_attempt_v2.json','revalidated_recorded_model_output.json',
        'nova_lite_discovery_probe.json','nova_lite_runtime_role_probe.json','nova_lite_price_evidence.json',
        'nova_lite_release.json','playground_trace_check.json','lambda_measurement.log','local_measurement.json',
        'build_manifest.json','deployment.json','execution_role_permissions_NOVA_LITE.json','bucket_privacy.json']
    records=[]
    for name in names:
        path=p/name
        if not path.exists():continue
        upload_file(s.client('s3'),path,prefix+'/'+name)
        records.append(dict(uri=prefix+'/'+name,sha256=sha256(path)))
    for path in [ROOT/'config/reference_demo.yaml',ROOT/'aws/demo/requirements.txt',ROOT/'README_reference_deployment.md',
                 Path(json.loads((p/'deployment.json').read_text(encoding='utf-8'))['active_release_path'])]:
        upload_file(s.client('s3'),path,prefix+'/'+path.name);records.append(dict(uri=prefix+'/'+path.name,sha256=sha256(path)))
    write_json(p/'deployment_delivery_manifest.json',dict(schema_version=1,files=records,manifest_uri=prefix+'/delivery_manifest.json'))
    upload_file(s.client('s3'),p/'deployment_delivery_manifest.json',prefix+'/delivery_manifest.json')
    print('Published private deployment delivery manifest:',prefix+'/delivery_manifest.json',flush=True)


def resume(c,p,s):
    """Restore access after stop, preserving deployed code, config, data and expiry."""
    state=json.loads((p/'deployment.json').read_text(encoding='utf-8'))
    account=s.client('sts').get_caller_identity()['Account']
    lam=s.client('lambda');api=s.client('apigatewayv2')
    function=lam.get_function_configuration(FunctionName=c['function_name'])
    if function['FunctionArn']!=state['function_arn'] or function['Role']!=state['role_arn']:
        raise ValueError('Unexpected function or role; refuse resume')
    if function['State']!='Active' or function['LastUpdateStatus']!='Successful':
        raise ValueError('Function is not ready')
    expected=base64.b64encode(bytes.fromhex(state['code_sha256'])).decode()
    if function['CodeSha256']!=expected:raise ValueError('Deployed code differs from locked deployment')
    env=function['Environment']['Variables']
    if env['DEPLOYMENT_CONFIG_URI']!=state['config_uri'] or env['DEPLOYMENT_CONFIG_SHA256']!=state['config_sha256']:
        raise ValueError('Deployed release differs; refuse resume')
    b,k=state['config_uri'][5:].split('/',1)
    raw=s.client('s3').get_object(Bucket=b,Key=k)['Body'].read()
    if hashlib.sha256(raw).hexdigest()!=state['config_sha256']:raise ValueError('Release hash mismatch')
    release=json.loads(raw)
    expiry=int(datetime.fromisoformat(release['expires_at']).timestamp())
    if int(env['DEMO_EXPIRES_EPOCH'])!=expiry or expiry<=time.time():
        raise ValueError('Release expired or expiry differs; review a new release explicitly')
    found=None
    if state.get('api_id'):
        try:found=api.get_api(ApiId=state['api_id'])
        except api.exceptions.NotFoundException:pass
    if found is None:
        matches=[]
        for page in api.get_paginator('get_apis').paginate():
            matches.extend(v for v in page.get('Items',[]) if v['Name']==c['api_name'])
        if len(matches)>1:raise ValueError('Multiple demo APIs found; inspect before resume')
        if matches:found=matches[0]
        else:
            found=api.create_api(Name=c['api_name'],ProtocolType='HTTP',Target=state['function_arn'])
            # Persist immediately so a later retry reuses this API rather than creating another.
            state.update(api_id=found['ApiId'],url=found['ApiEndpoint'])
            write_json(p/'deployment.json',state)
    if found['Name']!=c['api_name'] or found['ProtocolType']!='HTTP':raise ValueError('Unexpected API')
    integrations=api.get_integrations(ApiId=found['ApiId']).get('Items',[])
    if len(integrations)!=1 or integrations[0].get('IntegrationUri')!=state['function_arn']:
        raise ValueError('Unexpected API integration; refuse resume')
    state.update(api_id=found['ApiId'],url=found['ApiEndpoint'])
    write_json(p/'deployment.json',state)
    api.update_stage(ApiId=state['api_id'],StageName='$default',DefaultRouteSettings=dict(
        ThrottlingBurstLimit=c['api_burst'],ThrottlingRateLimit=c['api_requests_per_second']))
    source=f'arn:aws:execute-api:{c["region"]}:{account}:{state["api_id"]}/*/*'
    sid='DemoApiResume'+state['api_id']
    try:
        lam.add_permission(FunctionName=c['function_name'],StatementId=sid,Action='lambda:InvokeFunction',
            Principal='apigateway.amazonaws.com',SourceArn=source)
    except lam.exceptions.ResourceConflictException:
        policy=json.loads(lam.get_policy(FunctionName=c['function_name'])['Policy'])
        statement=next((v for v in policy['Statement'] if v['Sid']==sid),{})
        if (statement.get('Principal')!={'Service':'apigateway.amazonaws.com'} or
            statement.get('Action')!='lambda:InvokeFunction' or statement.get('Resource')!=state['function_arn'] or
            statement.get('Condition',{}).get('ArnLike',{}).get('AWS:SourceArn')!=source):
            raise ValueError('Existing API permission differs; refuse resume')
    concurrency=lam.get_function_concurrency(FunctionName=c['function_name'])
    reserved=concurrency.get('ReservedConcurrentExecutions')
    if reserved not in (None,0):raise ValueError('Concurrency changed; inspect before resume')
    if reserved==0:lam.delete_function_concurrency(FunctionName=c['function_name'])
    state['resumed_utc']=datetime.now(timezone.utc).isoformat()
    write_json(p/'deployment.json',state);write_json(p/'deployment_RESUMED.json',state)
    print('RESUMED',state['url'],'(same code/config/baseline; original expiry and budgets unchanged)',flush=True)


def stop(c,p,s):
    state=json.loads((p/'deployment.json').read_text(encoding='utf-8'))
    # Stops serving immediately without touching train, baseline or validator data.
    s.client('lambda').put_function_concurrency(FunctionName=c['function_name'],ReservedConcurrentExecutions=0)
    if state.get('api_id'):s.client('apigatewayv2').delete_api(ApiId=state['api_id'])
    state['stopped_utc']=datetime.now(timezone.utc).isoformat();write_json(p/'deployment_STOPPED.json',state)
    print('Stopped demo invocation and removed API. Secret/table/logs remain for deliberate cleanup.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stage',choices=['build','refresh','deploy','update','bedrock','report','publish','stop','resume'],required=True)
    args=parser.parse_args()
    c,p,s=context()
    try:globals()[args.stage](c,p,s)
    except Exception as e:
        write_json(p/('blocked_'+args.stage+'.json'),dict(type=type(e).__name__,reason=str(e)))
        raise
