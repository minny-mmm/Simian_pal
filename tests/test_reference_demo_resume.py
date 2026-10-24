"""Resume guards with fake AWS clients; never invokes AWS."""
import base64
import hashlib
import json
from types import SimpleNamespace

import pytest

from tools.deploy_reference_demo import resume


@pytest.fixture
def setup_resume(tmp_path):
    raw=b'{"expires_at":"2099-10-27T01:52:46+00:00"}'
    digest=hashlib.sha256(raw).hexdigest()
    state=dict(function_arn='function-arn',role_arn='role-arn',code_sha256='ab'*32,
               config_uri='s3://private/release',config_sha256=digest,api_id='deleted',url='https://old')
    (tmp_path/'deployment.json').write_text(json.dumps(state))
    calls=[]
    class Missing(Exception):pass
    class Conflict(Exception):pass
    function=dict(FunctionArn='function-arn',Role='role-arn',State='Active',LastUpdateStatus='Successful',
        CodeSha256=base64.b64encode(bytes.fromhex('ab'*32)).decode(),Environment={'Variables':dict(
            DEPLOYMENT_CONFIG_URI=state['config_uri'],DEPLOYMENT_CONFIG_SHA256=digest,DEMO_EXPIRES_EPOCH='4096749166')})
    # Derive epoch rather than depending on the host timezone.
    from datetime import datetime
    function['Environment']['Variables']['DEMO_EXPIRES_EPOCH']=str(int(datetime.fromisoformat('2099-10-27T01:52:46+00:00').timestamp()))
    def get_api(**kw):raise Missing()
    def create_api(**kw):
        calls.append('create_api')
        return dict(ApiId='new',ApiEndpoint='https://new',Name='demo',ProtocolType='HTTP')
    api=SimpleNamespace(exceptions=SimpleNamespace(NotFoundException=Missing),get_api=get_api,
        get_paginator=lambda _:SimpleNamespace(paginate=lambda:[{'Items':[]}]),create_api=create_api,
        get_integrations=lambda **kw:dict(Items=[dict(IntegrationUri='function-arn')]),
        update_stage=lambda **kw:calls.append('stage'))
    lam=SimpleNamespace(get_function_configuration=lambda **kw:function,
        exceptions=SimpleNamespace(ResourceConflictException=Conflict),
        add_permission=lambda **kw:calls.append('permission'),
        get_function_concurrency=lambda **kw:dict(ReservedConcurrentExecutions=0),
        delete_function_concurrency=lambda **kw:calls.append('enable'))
    s3=SimpleNamespace(get_object=lambda **kw:dict(Body=SimpleNamespace(read=lambda:raw)))
    clients={'lambda':lam,'apigatewayv2':api,'s3':s3,
             'sts':SimpleNamespace(get_caller_identity=lambda:dict(Account='123'))}
    session=SimpleNamespace(client=lambda name:clients[name])
    config=dict(function_name='demo',api_name='demo',region='ap-southeast-1',api_burst=5,api_requests_per_second=1)
    return config,tmp_path,session,function,calls,api


def test_resume_restores_deleted_api_and_enables_lambda_last(setup_resume):
    c,p,s,_,calls,_=setup_resume
    resume(c,p,s)
    assert calls==['create_api','stage','permission','enable']
    assert json.loads((p/'deployment.json').read_text())['url']=='https://new'
    assert (p/'deployment_RESUMED.json').exists()


def test_resume_refuses_changed_code_without_mutations(setup_resume):
    c,p,s,f,calls,_=setup_resume
    f['CodeSha256']='different'
    with pytest.raises(ValueError,match='code differs'):resume(c,p,s)
    assert calls==[]


def test_resume_refuses_changed_expiry_without_mutations(setup_resume):
    c,p,s,f,calls,_=setup_resume
    f['Environment']['Variables']['DEMO_EXPIRES_EPOCH']='1'
    with pytest.raises(ValueError,match='expired or expiry differs'):resume(c,p,s)
    assert calls==[]


def test_resume_reuses_existing_api_without_duplicate_creation(setup_resume):
    c,p,s,_,calls,api=setup_resume
    api.get_api=lambda **kw:dict(ApiId='existing',ApiEndpoint='https://existing',Name='demo',ProtocolType='HTTP')
    resume(c,p,s)
    assert calls==['stage','permission','enable']
