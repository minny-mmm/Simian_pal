"""Small authenticated end-to-end check of the deployed real-data demo."""
import base64
from copy import deepcopy
import json
import re
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.deploy_reference_demo import context
from tools.run_reference_aws import compare_numeric
from src.reference.common import write_json
from src.reference.io import load_bundle
from src.reference.model import load_reference
from src.reference.output import build_analysis,validate_output,validate_explanation


def main():
    c,p,s=context();state=json.loads((p/'deployment.json').read_text(encoding='utf-8'))
    secret=s.client('secretsmanager').get_secret_value(SecretId=state['secret_arn'])['SecretString']
    def get(path,auth=True):
        headers={'Authorization':'Bearer '+secret} if auth else {}
        req=urllib.request.Request(state['url']+path,headers=headers)
        started=time.perf_counter()
        for attempt in range(2):
            try:
                with urllib.request.urlopen(req,timeout=40) as r:result=(r.status,r.read().decode())
                break
            except urllib.error.HTTPError as e:
                result=(e.code,e.read().decode());break
            except (TimeoutError,urllib.error.URLError,ConnectionResetError):
                if attempt:raise
                time.sleep(2)
        return *result,time.perf_counter()-started
    code,html,_=get('/',False)
    if code!=200 or 'COPS-7' in html or 'NHANES-81262' in html:raise ValueError('Public dashboard/privacy check failed')
    time.sleep(1)
    code,body,_=get('/api/analysis',False)
    if code!=401:raise ValueError('Unauthenticated API did not deny access')
    timings={}
    time.sleep(1)
    code,body,timings['status_seconds']=get('/api/status')
    if code!=200:raise RuntimeError('Status API: '+body)
    status=json.loads(body)
    runtime_probe=None
    if status.get('llm_enabled'):
        time.sleep(1)
        code,body,timings['runtime_llm_probe_seconds']=get('/api/llm-probe')
        if code!=200:raise RuntimeError('Runtime IAM synthetic probe failed: '+body)
        runtime_probe=json.loads(body)
        if not runtime_probe.get('success') or runtime_probe['data_origin']!='synthetic':
            raise ValueError('Runtime synthetic probe did not pass; do not send validation to model')
        write_json(p/'nova_lite_runtime_role_probe.json',runtime_probe)
        old=p/'explanation_DEPLOYED_REAL.json'
        if old.exists():
            previous=json.loads(old.read_text(encoding='utf-8'))
            if previous['explanation']['generation_kind']=='expected_template' and not (p/'explanation_TEMPLATE_BEFORE_BEDROCK.json').exists():
                write_json(p/'explanation_TEMPLATE_BEFORE_BEDROCK.json',previous)
    time.sleep(1)
    code,body,timings['analysis_seconds']=get('/api/analysis')
    if code!=200:raise RuntimeError('Analysis API: '+body)
    actual=json.loads(body);validate_output(actual,'analysis')
    if 'COPS-7' in body or 'NHANES-81262' in body:raise ValueError('Original participant ID escaped API')
    daily,*_=load_bundle(ROOT/'data/daily/reference-v1/aws-real-pilot-20261024-v1/valid')
    baseline=load_reference(ROOT/'data/models/reference-v1/aws-real-pilot-20261024-v1/local_py312_compat/baseline.json')
    expected=build_analysis(daily,baseline)
    for row in expected['analysis']:row['subject_id']={'COPS-7':'VALID-A','NHANES-81262':'VALID-B'}[row['subject_id']]
    maximum=compare_numeric(actual,expected,1e-12,1e-12)
    write_json(p/'analysis_DEPLOYED_REAL.json',actual)
    day=max(r['day'] for r in actual['analysis'] if r['subject_id']=='VALID-A')
    time.sleep(1)
    code,body,timings['explanation_seconds']=get('/api/explanation?subject=VALID-A&day='+str(day))
    if code!=200:raise RuntimeError('Explanation API: '+body)
    explanation=json.loads(body)
    validate_explanation(explanation['explanation'],explanation['analysis'])
    write_json(p/'explanation_DEPLOYED_REAL.json',explanation)
    if explanation['explanation']['generation_kind']=='llm':
        write_json(p/'explanation_MODEL.json',explanation['explanation'])
        write_json(p/'explanation_MODEL_validation.json',explanation['validation'])
    # Tail contains only metrics/error types from our code, never headers, secrets or patient rows.
    event=dict(rawPath='/api/status',requestContext={'http':{'method':'GET'}},headers={'authorization':'Bearer '+secret})
    invoked=s.client('lambda').invoke(FunctionName=c['function_name'],InvocationType='RequestResponse',LogType='Tail',Payload=json.dumps(event).encode())
    response=json.loads(invoked['Payload'].read())
    if response.get('statusCode')!=200:raise ValueError('Direct Lambda measurement failed')
    logs=base64.b64decode(invoked.get('LogResult','')).decode()
    (p/'lambda_measurement.log').write_text(logs,encoding='utf-8')
    memory=re.search(r'Max Memory Used: (\d+) MB',logs)
    duration=re.search(r'\bDuration: ([\d.]+) ms',logs)
    report=dict(schema_version=1,deployment_status='DEPLOYED_E2E_PASSED',demo_url=state['url'],
        baseline_fit_location='local',aws_training_status='NOT_SUBMITTED_AT_RELEASE',
        baseline_sha256=status['baseline_sha256'],data_origin='real',validation_metric_rows=len(actual['analysis']),
        api_auth_denied_without_token=True,original_ids_not_exposed=True,analysis_schema='1.0.0',
        numerical_rtol=1e-12,numerical_atol=1e-12,maximum_absolute_difference=maximum,
        explanation_generation_kind=explanation['explanation']['generation_kind'],explanation_validation=explanation['validation'],
        runtime_iam_llm_probe=runtime_probe,
        timings=timings,runtime=status,resource_config=dict(memory_mb=c['memory_mb'],timeout_seconds=c['timeout_seconds']),
        aws_reported_peak_memory_mb=int(memory[1]) if memory else None,
        aws_warm_status_duration_ms=float(duration[1]) if duration else None,
        no_fit_performed=True,test_opened=False,sagemaker_endpoint_created=False)
    write_json(p/'e2e_report.json',report)
    print(json.dumps({k:report[k] for k in ['deployment_status','demo_url','validation_metric_rows','maximum_absolute_difference','explanation_generation_kind','timings']},indent=2))


if __name__=='__main__':main()
