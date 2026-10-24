"""Deployment guards; no real patient data, paid job or API invocation."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setitem(sys.modules,'resource',SimpleNamespace(RUSAGE_SELF=0,getrusage=lambda _:SimpleNamespace(ru_maxrss=1024)))
    spec=importlib.util.spec_from_file_location('demo_app',Path(__file__).parents[1]/'aws/demo/app.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    m.TOKEN='private-demo-token'
    monkeypatch.setenv('DEMO_EXPIRES_EPOCH','9999999999')
    return m


def event(path,token=None):
    return dict(rawPath=path,requestContext={'http':{'method':'GET'}},headers={'authorization':'Bearer '+token} if token else {})


def test_patient_endpoints_require_auth_before_loading(app,monkeypatch):
    monkeypatch.setattr(app,'load_state',lambda:pytest.fail('Unauthenticated data access'))
    for path in ['/api/status','/api/analysis','/api/explanation','/api/llm-probe']:
        response=app.handler(event(path),None)
        assert response['statusCode']==401
        assert 'COPS-7' not in response['body']


def test_expired_demo_denies_authenticated_access(app,monkeypatch):
    monkeypatch.setenv('DEMO_EXPIRES_EPOCH','1')
    monkeypatch.setattr(app,'load_state',lambda:pytest.fail('Expired data access'))
    assert app.handler(event('/api/analysis','private-demo-token'),None)['statusCode']==410


def test_daily_budget_denial_cannot_reach_analysis(app,monkeypatch):
    def blocked(*a):raise OverflowError()
    monkeypatch.setattr(app,'consume',blocked)
    monkeypatch.setattr(app,'load_state',lambda:pytest.fail('Budget bypass'))
    assert app.handler(event('/api/analysis','private-demo-token'),None)['statusCode']==429


def test_content_hash_mismatch_fails_closed(app,monkeypatch):
    monkeypatch.setattr(app,'s3_bytes',lambda _:b'changed')
    with pytest.raises(ValueError,match='hash mismatch'):app.checked_bytes('s3://private/key','0'*64)


def test_public_dashboard_contains_no_patient_ids(app):
    response=app.handler(event('/'),None)
    assert response['statusCode']==200
    assert 'COPS-7' not in response['body'] and 'NHANES-81262' not in response['body']
    assert 'cache-control' in response['headers']


@pytest.mark.parametrize('location',['local','aws_sagemaker'])
def test_status_reports_configured_baseline_location(app,monkeypatch,location):
    monkeypatch.setattr(app,'consume',lambda *a:None)
    monkeypatch.setattr(app,'load_state',lambda:dict(config=dict(baseline_fit_location=location,
        baseline_sha256='0'*64,llm_model='configured-provider-model',llm_enabled=False),
        analysis=dict(baseline_version='approved-version',schema_version='1.0.0'),load_seconds=1.))
    response=app.handler(event('/api/status','private-demo-token'),None)
    assert response['statusCode']==200
    assert json.loads(response['body'])['baseline_fit_location']==location


def test_runtime_probe_uses_configured_profile_and_synthetic_input(app,monkeypatch):
    calls=[]
    class FakeBedrock:
        def converse(self,**kwargs):
            calls.append(kwargs)
            return dict(output={'message':{'content':[{'text':'HIGHER'}]}},usage={'inputTokens':10,'outputTokens':2})
    def client(service,**kwargs):
        assert service=='bedrock-runtime' and kwargs['region_name']=='ap-southeast-1'
        return FakeBedrock()
    monkeypatch.setattr(app.boto3,'client',client)
    monkeypatch.setattr(app,'consume',lambda *a:None)
    monkeypatch.setattr(app,'load_state',lambda:dict(config=dict(llm_enabled=True,llm_calls_per_day=20,
        llm_region='ap-southeast-1',llm_model='verified-profile-id'),analysis={'analysis':[]}))
    response=app.handler(event('/api/llm-probe','private-demo-token'),None)
    assert response['statusCode']==200
    assert json.loads(response['body'])['data_origin']=='synthetic'
    assert calls[0]['modelId']=='verified-profile-id' and calls[0]['inferenceConfig']['maxTokens']==40
    assert 'Synthetic only' in calls[0]['messages'][0]['content'][0]['text']


def test_model_unsupported_levels_rejected_despite_correct_evidence(app):
    evidence=dict(value=.02,cops_signed_distance=-.01,nhanes_signed_distance=-.02,previous_day_delta=.001)
    relations,_=app.text_contract(evidence)
    with pytest.raises(ValueError):
        app.validate_model_text(dict(text='ค่าที่วัดได้อยู่ในระดับกลางถึงสูง',relations=relations),evidence)


def test_model_grounded_direction_and_missing_handling(app):
    evidence=dict(value=.02,cops_signed_distance=-.01,nhanes_signed_distance=-.02,previous_day_delta=.001)
    relations,phrases=app.text_contract(evidence)
    assert app.validate_model_text(dict(text='; '.join(phrases),relations=relations),evidence)
    with pytest.raises(ValueError):
        app.validate_model_text(dict(text='; '.join(phrases)+' สูงกว่ามัธยฐาน COPS',relations=relations),evidence)
    missing=dict(value=None,cops_signed_distance=None,nhanes_signed_distance=None,previous_day_delta=None)
    relations,phrases=app.text_contract(missing)
    assert app.validate_model_text(dict(text='; '.join(phrases),relations=relations),missing)


def test_equivalent_thai_negation_from_actual_model_response(app):
    missing=dict(value=None,cops_signed_distance=None,nhanes_signed_distance=None,previous_day_delta=None)
    relations,_=app.text_contract(missing)
    text='ไม่สามารถเปรียบเทียบค่า walking_cadence_hourly_median_mean_hz กับ COPS ได้ ไม่สามารถเปรียบเทียบกับ NHANES ได้ ไม่สามารถเปรียบเทียบวันก่อนได้ และวัดไม่ได้'
    assert app.validate_model_text(dict(text=text,relations=relations),missing)==text
    with pytest.raises(ValueError):
        app.validate_model_text(dict(text=text+' สูงกว่ามัธยฐาน COPS',relations=relations),missing)
