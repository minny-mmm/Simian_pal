"""Authenticated demo serving the existing, immutable real reference baseline."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import resource
import time
import traceback

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

STATE = None
TOKEN = None
CLIENT_CONFIG = Config(connect_timeout=5, read_timeout=22, retries={'total_max_attempts': 1})
PROMPT_VERSION = 'reference-thai-grounded-v2'


def text_contract(evidence):
    def relation(value):
        return 'unavailable' if value is None else 'higher' if value>0 else 'lower' if value<0 else 'equal'
    relations=dict(cops=relation(evidence['cops_signed_distance']),nhanes=relation(evidence['nhanes_signed_distance']),
                   previous=relation(evidence['previous_day_delta']))
    phrases=[]
    for key,name in [('cops','COPS'),('nhanes','NHANES')]:
        phrases.append(dict(higher='สูงกว่ามัธยฐาน '+name,lower='ต่ำกว่ามัธยฐาน '+name,
                            equal='เท่ากับมัธยฐาน '+name,unavailable='เปรียบเทียบกับ '+name+' ไม่ได้')[relations[key]])
    phrases.append(dict(higher='สูงกว่าวันก่อน',lower='ต่ำกว่าวันก่อน',equal='เท่ากับวันก่อน',
                        unavailable='เปรียบเทียบวันก่อนไม่ได้')[relations['previous']])
    if evidence['value'] is None:phrases.append('วัดไม่ได้')
    return relations,phrases


def validate_model_text(prediction,evidence):
    relations,phrases=text_contract(evidence)
    if not isinstance(prediction,dict) or set(prediction)!={'text','relations'} or prediction['relations']!=relations:
        raise ValueError('Model relations do not match signed distances/nulls')
    text=prediction['text']
    if not isinstance(text,str) or not 5<=len(text)<=700 or re.search(r'\d',text):
        raise ValueError('Invalid model text/number guard')
    normalized=text
    for name in ['COPS','NHANES']:
        normalized=re.sub(r'ไม่สามารถเปรียบเทียบ(?:ค่า\s+[A-Za-z_]+\s+)?(?:กับ\s+)?'+name+r'\s*ได้',
                          'เปรียบเทียบกับ '+name+' ไม่ได้',normalized)
    normalized=re.sub(r'ไม่สามารถเปรียบเทียบ(?:กับ)?วันก่อนได้','เปรียบเทียบวันก่อนไม่ได้',normalized)
    if any(p not in normalized for p in phrases):
        raise ValueError('Required grounded direction/null wording missing')
    if evidence['value'] is not None and 'วัดไม่ได้' in normalized:
        raise ValueError('Observed measurement cannot be described as unavailable')
    if any(word in text for word in ['ระดับ','ปานกลาง','มาก','เล็กน้อย','ปกติ','วินิจฉัย','ความเสี่ยง','รุนแรง','โรค','อาการ','นัยสำคัญ']):
        raise ValueError('Unsupported level, magnitude or clinical claim')
    for name,key in [('COPS','cops'),('NHANES','nhanes')]:
        choices=dict(higher='สูงกว่ามัธยฐาน '+name,lower='ต่ำกว่ามัธยฐาน '+name,
                     equal='เท่ากับมัธยฐาน '+name,unavailable='เปรียบเทียบกับ '+name+' ไม่ได้')
        if any(phrase in normalized for choice,phrase in choices.items() if choice!=relations[key]):
            raise ValueError('Contradictory reference direction')
    choices=dict(higher='สูงกว่าวันก่อน',lower='ต่ำกว่าวันก่อน',equal='เท่ากับวันก่อน',unavailable='เปรียบเทียบวันก่อนไม่ได้')
    if any(phrase in normalized for choice,phrase in choices.items() if choice!=relations['previous']):
        raise ValueError('Contradictory prior-day direction')
    return text


def s3_bytes(uri):
    bucket, key = uri[5:].split('/', 1)
    response = boto3.client('s3', config=CLIENT_CONFIG).get_object(Bucket=bucket, Key=key)
    try:
        return response['Body'].read()
    finally:
        response['Body'].close()


def checked_bytes(uri, expected):
    data = s3_bytes(uri)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Deployment content hash mismatch')
    return data


def load_state():
    global STATE
    if STATE is not None:
        return STATE
    started = time.perf_counter()
    from src.reference.io import load_bundle
    from src.reference.model import load_reference
    from src.reference.output import build_analysis
    config = json.loads(checked_bytes(os.environ['DEPLOYMENT_CONFIG_URI'], os.environ['DEPLOYMENT_CONFIG_SHA256']))
    root = Path('/tmp/reference-demo')
    root.mkdir(exist_ok=True)
    model = root/'baseline.json'
    model.write_bytes(checked_bytes(config['baseline_uri'], config['baseline_sha256']))
    bundle = root/'valid'
    bundle.mkdir(exist_ok=True)
    for name, expected in config['bundle_files'].items():
        if Path(name).name != name:
            raise ValueError('Unsafe bundle filename')
        (bundle/name).write_bytes(checked_bytes(config['valid_bundle_uri']+'/'+name, expected))
    baseline = load_reference(model)
    daily, _, cfg, prov = load_bundle(bundle)
    if not daily.split.eq('valid').all() or set(daily.subject_id) != {'COPS-7', 'NHANES-81262'}:
        raise ValueError('Only the authorized validation subjects may be served')
    if cfg != baseline['config'] or prov['preprocessing_version'] != baseline['preprocessing_version']:
        raise ValueError('Baseline/validation compatibility check failed')
    if baseline['data_kind'] != 'real' or baseline['fit_split'] != 'train':
        raise ValueError('Not an approved real train baseline')
    analysis = build_analysis(daily, baseline)
    # Pseudonyms leave the server; the private source bundle is never returned.
    for row in analysis['analysis']:
        row['subject_id'] = {'COPS-7': 'VALID-A', 'NHANES-81262': 'VALID-B'}[row['subject_id']]
    from src.reference.output import validate_output
    validate_output(analysis, 'analysis')
    STATE = dict(config=config, analysis=analysis, load_seconds=time.perf_counter()-started)
    return STATE


def consume(kind, limit):
    key = datetime.now(timezone.utc).strftime('%Y-%m-%d')+':'+kind
    try:
        boto3.client('dynamodb', config=CLIENT_CONFIG).update_item(
            TableName=os.environ['BUDGET_TABLE'], Key={'pk': {'S': key}},
            UpdateExpression='ADD #n :one', ConditionExpression='attribute_not_exists(#n) OR #n < :limit',
            ExpressionAttributeNames={'#n': 'count'},
            ExpressionAttributeValues={':one': {'N': '1'}, ':limit': {'N': str(limit)}})
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise OverflowError('Daily demo request limit reached') from None
        raise


def explanation(analysis, config):
    from src.reference.common import fingerprint
    from src.reference.output import build_expected_explanation, validate_explanation
    expected = build_expected_explanation(analysis)
    cache_uri = config['cache_uri']+'/'+fingerprint(dict(analysis=analysis, prompt=PROMPT_VERSION,
        model=config['llm_model'], enabled=config['llm_enabled']))+'.json'
    try:
        cached = json.loads(s3_bytes(cache_uri))
        validate_explanation(cached['explanation'], analysis)
        return cached
    except ClientError as e:
        # Without ListBucket, S3 deliberately returns 403 for a nonexistent cache key.
        # Only cache reads may be misses; baseline/config/bundle reads still fail closed.
        if e.response['Error']['Code'] not in ['NoSuchKey', '404', 'AccessDenied', '403']:
            raise
    result = {'explanation': expected, 'validation': {'schema_and_evidence': True,
              'generation_status': 'template_fallback', 'reason': 'LLM not completed'}}
    if config['llm_enabled']:
        try:
            consume('llm', config['llm_calls_per_day'])
            # Only three deidentified numeric rows; no subject, dates, roster, raw signal or bucket URI.
            facts = []
            for e in expected['explanations']:
                relations,phrases=text_contract(e['evidence'])
                facts.append(dict(metric=e['metric'],unit=e['unit'],evidence=e['evidence'],
                                  required_relations=relations,required_phrases=phrases))
            prompt = ('เขียนคำอธิบายภาษาไทยสั้นต่อรายการตามข้อมูลเท่านั้น ทุกข้อความต้องมีทุกวลีใน required_phrases แบบตรงตัว '
                      'relations ต้องคัดลอก required_relations ของรายการนั้น ห้ามแบ่งระดับกลาง/สูง/ต่ำ ห้ามสรุปความรุนแรง โรค หรืออาการ '
                      'ห้ามแต่งตัวเลขและห้ามใส่ตัวเลขใดๆใน text ไม่ใช้คำว่า ระดับ ปกติ มาก เล็กน้อย หรือนัยสำคัญ '
                      'null คือวัดหรือเปรียบเทียบไม่ได้ ไม่ใช่ศูนย์ ไม่เพิ่มข้อสรุปนอกข้อมูล '
                      'ตอบ JSON object {"rows":[{"text":"...","relations":{"cops":"...","nhanes":"...","previous":"..."}}, ...]} '
                      'ตามลำดับรายการเท่านั้น: '
                      +json.dumps(facts, ensure_ascii=False))
            if len(prompt.encode('utf-8')) > 8000:
                raise ValueError('Prompt size cap exceeded')
            response = boto3.client('bedrock-runtime', region_name=config.get('llm_region',os.environ['AWS_REGION']),config=CLIENT_CONFIG).converse(
                modelId=config['llm_model'], messages=[{'role': 'user', 'content': [{'text': prompt}]}],
                inferenceConfig={'maxTokens': config.get('llm_max_output_tokens',1000), 'temperature': 0})
            raw = ''.join(c.get('text', '') for c in response['output']['message']['content']).strip()
            attempt=dict(schema_version=1,generation_kind='llm_raw_attempt',model_version=config['llm_model'],
                         prompt_version=PROMPT_VERSION,analysis_sha256=fingerprint(analysis),
                         output=response['output'],usage=response.get('usage'),stop_reason=response.get('stopReason'))
            bucket,key=(cache_uri+'.attempt.json')[5:].split('/',1)
            boto3.client('s3',config=CLIENT_CONFIG).put_object(Bucket=bucket,Key=key,
                Body=json.dumps(attempt,ensure_ascii=False).encode(),ContentType='application/json',ServerSideEncryption='AES256')
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw)
            predictions = json.loads(raw)['rows']
            if len(predictions)!=len(facts):raise ValueError('LLM row count differs')
            texts=[validate_model_text(prediction,e['evidence']) for prediction,e in zip(predictions,expected['explanations'])]
            document = deepcopy(expected)
            document.update(generation_kind='llm', model_version=config['llm_model'], prompt_version=PROMPT_VERSION)
            for e, text in zip(document['explanations'], texts):
                e['text'] = text+' เป็นความต่างจากกลุ่มอ้างอิง ไม่ใช่คะแนนความรุนแรงหรือโอกาสเป็นโรค'
            validate_explanation(document, analysis)
            result = dict(explanation=document, validation=dict(schema_and_evidence=True,
                          numeric_text_guard=True,clinical_phrase_guard=True,directional_grounding_guard=True,
                          undefined_level_guard=True,semantic_human_review_required=True,
                          validation_version='reference-text-guard-v2.1',
                          generation_status='llm', usage=response.get('usage'), prompt_version=PROMPT_VERSION))
        except Exception as e:
            # Preserve a truthful template label and bounded error type, no credentials/prompts in logs.
            result['validation']['reason'] = type(e).__name__
    bucket, key = cache_uri[5:].split('/',1)
    boto3.client('s3',config=CLIENT_CONFIG).put_object(Bucket=bucket,Key=key,
        Body=json.dumps(result,ensure_ascii=False).encode(),ContentType='application/json',ServerSideEncryption='AES256')
    return result


def respond(status, body, mime='application/json'):
    headers = {'content-type': mime+'; charset=utf-8', 'cache-control': 'no-store',
               'x-content-type-options': 'nosniff', 'referrer-policy': 'no-referrer',
               'content-security-policy': "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"}
    return dict(statusCode=status,headers=headers,body=body if isinstance(body,str) else json.dumps(body,ensure_ascii=False,allow_nan=False))


def handler(event, context):
    global TOKEN
    started = time.perf_counter()
    path = event.get('rawPath','/')
    method = event.get('requestContext',{}).get('http',{}).get('method','GET')
    if method == 'GET' and path in ['/', '/app.js']:
        file = Path(__file__).parent/('index.html' if path=='/' else 'app.js')
        return respond(200,file.read_text(encoding='utf-8'),'text/html' if path=='/' else 'application/javascript')
    if not path.startswith('/api/'):
        return respond(404,{'error':'not_found'})
    if method != 'GET':
        return respond(405,{'error':'method_not_allowed'})
    try:
        if time.time() > int(os.environ.get('DEMO_EXPIRES_EPOCH','0')):
            return respond(410,{'error':'demo_expired'})
        if TOKEN is None:
            TOKEN = boto3.client('secretsmanager',config=CLIENT_CONFIG).get_secret_value(SecretId=os.environ['AUTH_SECRET_ARN'])['SecretString']
        supplied = event.get('headers',{}).get('authorization','')
        if not hmac.compare_digest(supplied,'Bearer '+TOKEN):
            return respond(401,{'error':'authentication_required'})
        consume('api',100)
        state = load_state()
        analysis = deepcopy(state['analysis'])
        query = event.get('queryStringParameters') or {}
        subject = query.get('subject')
        if subject and subject not in {'VALID-A','VALID-B'}:
            return respond(400,{'error':'invalid_subject'})
        if subject:
            analysis['analysis']=[r for r in analysis['analysis'] if r['subject_id']==subject]
        if path == '/api/status':
            return respond(200,dict(deployment='aws_lambda',baseline_fit_location=state['config']['baseline_fit_location'],
                training_on_aws='NOT_RUN_BY_THIS_DEPLOYMENT',
                baseline_sha256=state['config']['baseline_sha256'],baseline_version=analysis['baseline_version'],
                schema_version=analysis['schema_version'],load_seconds=state['load_seconds'],
                memory_peak_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                llm_model=state['config']['llm_model'],llm_routing='APAC inference profile; AWS-managed cross-region routing',
                llm_enabled=state['config']['llm_enabled'],llm_auth_mode=state['config'].get('llm_auth_mode','runtime_iam_role'),
                data_origin='real',subject_aliases=['VALID-A','VALID-B']))
        if path == '/api/llm-probe':
            if not state['config']['llm_enabled']:
                return respond(409,{'error':'llm_disabled'})
            consume('llm',state['config']['llm_calls_per_day'])
            response=boto3.client('bedrock-runtime',region_name=state['config']['llm_region'],config=CLIENT_CONFIG).converse(
                modelId=state['config']['llm_model'],messages=[{'role':'user','content':[{'text':'Synthetic only: value 2, reference 1. Reply HIGHER only. No patient data.'}]}],
                inferenceConfig={'maxTokens':40,'temperature':0})
            return respond(200,dict(data_origin='synthetic',purpose='runtime_iam_role_probe',success=True,
                model_id=state['config']['llm_model'],auth_mode='runtime_iam_role',usage=response.get('usage'),output=response['output']))
        if path == '/api/analysis':
            return respond(200,analysis)
        if path == '/api/explanation':
            if not subject:
                return respond(400,{'error':'subject_required'})
            day=int(query.get('day',max(r['day'] for r in analysis['analysis'])))
            analysis['analysis']=[r for r in analysis['analysis'] if r['day']==day]
            if not analysis['analysis']:
                return respond(404,{'error':'no_observations'})
            return respond(200,dict(analysis=analysis,**explanation(analysis,state['config'])))
        return respond(404,{'error':'not_found'})
    except OverflowError:
        return respond(429,{'error':'daily_demo_limit_reached'})
    except ValueError:
        return respond(400,{'error':'invalid_or_incompatible_request'})
    except Exception as e:
        print(json.dumps({'event':'request_error','type':type(e).__name__,
                          'frames':[(Path(f.filename).name,f.lineno,f.name) for f in traceback.extract_tb(e.__traceback__)]}))
        return respond(503,{'error':'service_unavailable'})
    finally:
        print(json.dumps(dict(event='request_metrics',path=path,seconds=time.perf_counter()-started,
                             peak_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)))
