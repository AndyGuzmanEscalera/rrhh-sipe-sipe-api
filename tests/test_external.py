import uuid
import httpx
import pytest
from app.external.correspondencia.client import CorrespondenciaClient
from app.external.correspondencia.config import CorrespondenciaConfig
from app.external.correspondencia.errors import CorrespondenciaError
from app.core.config import Settings
from app.core.security import get_actor
from app.external.correspondencia.schemas import Actor
from tests.conftest import EMPLOYEE,ACTOR


def api(handler):
    return CorrespondenciaClient(CorrespondenciaConfig('https://correspondencia.example.test/api','test-token'),httpx.MockTransport(handler))


def employee(**extra):
    return dict(id=str(EMPLOYEE),first_name='Test',last_name='Only',document_number='007',**extra)


def test_employee_and_base_path():
    def handler(request):
        assert request.url.path==f'/api/admin/employees/{EMPLOYEE}'
        assert request.headers['authorization']=='Bearer test-token'
        return httpx.Response(200,json=employee())
    assert api(handler).get_employee(EMPLOYEE).document_number=='007'


def test_exact_ci_with_pagination_and_inactive():
    calls=[]
    def handler(request):
        calls.append(dict(request.url.params))
        page=int(request.url.params['page'])
        assert request.url.params['include_deleted']=='true'
        return httpx.Response(200,json={'page':page,'total_pages':2,'items':[
            {**employee(),'id':str(uuid.uuid4()),'document_number':'0007'} if page==1 else employee(is_active=False)]})
    found=api(handler).find_employee_by_document('007')
    assert found.id==EMPLOYEE and not found.is_active and len(calls)==2


def test_missing_ci():
    assert api(lambda r:httpx.Response(200,json={'page':1,'total_pages':0,'items':[]})).find_employee_by_document('007') is None


@pytest.mark.parametrize('method,path',[('get_area','admin/organizational-units'),('get_position','admin/positions'),('get_user','admin/users')])
def test_other_masters(method,path):
    def handler(request):
        assert request.url.path==f'/api/{path}/{EMPLOYEE}'
        return httpx.Response(200,json={'id':str(EMPLOYEE),'name':'Test'})
    assert getattr(api(handler),method)(EMPLOYEE)['id']==str(EMPLOYEE)


def test_actor_always_uses_incoming_token():
    def handler(request):
        assert request.headers['authorization']=='Bearer caller-token'
        return httpx.Response(200,json={'id':str(ACTOR),'username':'fixture','is_active':True,'roles':['MANAGER'],'permissions':['rrhh.read']})
    assert api(handler).get_authenticated_user('caller-token').id==ACTOR


@pytest.mark.parametrize('status,expected',[(401,401),(403,403),(404,404),(500,503),(302,503)])
def test_external_errors(status,expected):
    with pytest.raises(CorrespondenciaError) as error:
        api(lambda r:httpx.Response(status,json={'detail':'private internal data'})).get_employee(EMPLOYEE)
    assert error.value.status==expected and 'private' not in str(error.value)


def test_timeout():
    def handler(request): raise httpx.ReadTimeout('private',request=request)
    with pytest.raises(CorrespondenciaError,match='timeout'): api(handler).get_employee(EMPLOYEE)


def test_https_and_secrets_repr():
    with pytest.raises(ValueError): Settings(_env_file=None,correspondencia_api_url='http://server')
    assert 'private-key' not in repr(Settings(_env_file=None,correspondencia_api_token='private-key'))


def test_api_authorization(client):
    from app.main import app
    app.dependency_overrides[get_actor]=lambda:Actor(id=ACTOR,is_active=True,roles=['MANAGER'],permissions=['rrhh.read'])
    assert client.post('/rrhh/biometric/sync',json={'start_date':'2026-07-01','end_date':'2026-07-02'}).status_code==403
    app.dependency_overrides.pop(get_actor)
    assert client.get('/rrhh/attendance/contracts').status_code==401
