"""Policy tests use real /auth/me role-code shape and no identity overrides."""
import httpx
import pytest
from app.main import app
from app.core.security import get_actor,make_client
from app.external.correspondencia.client import CorrespondenciaClient
from app.external.correspondencia.config import CorrespondenciaConfig
from tests.conftest import ACTOR,EMPLOYEE

HEADERS={'Authorization':'Bearer caller-fixture'}


def upstream(*,roles=None,permissions=None,active=True,code=200,timeout=False,extra=None):
    def handler(request):
        assert request.headers['authorization']=='Bearer caller-fixture'
        if request.url.path=='/api/auth/me':
            if timeout: raise httpx.ReadTimeout('private-upstream',request=request)
            if code!=200: return httpx.Response(code,json=extra or {'detail':'private-upstream'})
            return httpx.Response(200,json={'id':str(ACTOR),'username':'fixture','is_active':active,
                'roles':['MANAGER'] if roles is None else roles,'permissions':permissions or [],**(extra or {})})
        if request.url.path.endswith(str(EMPLOYEE)):
            return httpx.Response(200,json={'id':str(EMPLOYEE),'first_name':'Fixture','last_name':'Only'})
        return httpx.Response(200,json={'items':[],'page':1,'total_pages':0,'page_size':20,'total':0})
    app.dependency_overrides.pop(get_actor,None)
    app.dependency_overrides[make_client]=lambda:CorrespondenciaClient(
        CorrespondenciaConfig('https://masters.test/api','caller-fixture'),httpx.MockTransport(handler))


def test_active_gestor_can_enter_without_rrhh_read(client):
    upstream()
    result=client.get('/auth/me',headers=HEADERS)
    assert result.status_code==200
    assert result.json()['roles']==['MANAGER']
    assert 'rrhh.read' in result.json()['permissions']
    assert 'rrhh.manage' not in result.json()['permissions']
    assert client.get('/rrhh/capabilities',headers=HEADERS).json()=={
        'read':True,'reports':True,'manage':False,'sync':False,'shifts':False,
        'parameters':False,'links':False,'force_resync':False}


@pytest.mark.parametrize('roles',[[],['OPERATOR'],['ADMIN'],['SUPERADMIN'],['GESTOR'],['manager']])
def test_non_gestor_rejected_even_with_admin_permissions(client,roles):
    upstream(roles=roles,permissions=['rrhh.read','rrhh.manage','rrhh.resync'])
    assert client.get('/auth/me',headers=HEADERS).status_code==403
    assert client.get('/rrhh/attendance/parameters',headers=HEADERS).status_code==403


def test_job_position_or_permissions_cannot_substitute_role(client):
    upstream(roles=[],permissions=['GESTOR'],extra={'user_type':'GESTOR','role':'GESTOR','position':'Gestor'})
    assert client.get('/auth/me',headers=HEADERS).status_code==403


def test_inactive_gestor_rejected(client):
    upstream(active=False)
    assert client.get('/auth/me',headers=HEADERS).status_code==403


def test_actual_legacy_inactive_master_contract_maps_to_403(client):
    upstream(code=401,extra={'detail':'Usuario no disponible'})
    result=client.get('/auth/me',headers=HEADERS)
    assert result.status_code==403 and result.json()['detail']['code']=='inactive_actor'


def test_invalid_token_and_missing_bearer_stay_401(client):
    upstream(code=401,extra={'detail':'Token inválido'})
    assert client.get('/auth/me',headers=HEADERS).status_code==401
    assert client.get('/auth/me').status_code==401


@pytest.mark.parametrize('code,timeout,status',[(503,False,503),(200,True,504)])
def test_correspondencia_failure_controlled(client,code,timeout,status):
    upstream(code=code,timeout=timeout)
    response=client.get('/auth/me',headers=HEADERS)
    assert response.status_code==status and 'private-upstream' not in response.text


@pytest.mark.parametrize('path',[
    '/admin/employees','/admin/organizational-units','/admin/positions',
    '/rrhh/attendance/contracts','/rrhh/attendance/parameters',
    '/rrhh/attendance/parameters/revisions','/rrhh/work-shifts',
    f'/rrhh/employee-shift-assignments/{EMPLOYEE}',
    '/rrhh/biometric/coverage?start_date=2026-07-01&end_date=2026-07-01',
    '/rrhh/biometric/syncs','/rrhh/biometric/syncs/latest',
    f'/rrhh/attendance/employees/{EMPLOYEE}/records?start_date=2026-07-01&end_date=2026-07-01',
])
def test_gestor_reads_without_explicit_read_grants(client,path):
    upstream()
    response=client.get(path,headers=HEADERS)
    assert response.status_code==200,response.text


def test_gestor_report_and_calendar_without_read_permission(client,db):
    from tests.test_history import seed
    from tests.conftest import CONTRACT
    seed(db);upstream()
    response=client.get(f'/rrhh/attendance/employees/{EMPLOYEE}/report',headers=HEADERS,
        params={'contract_type_id':str(CONTRACT),'year':2026,'month':7})
    assert response.status_code==200,response.text
    assert response.json()['ready'] and len(response.json()['calendar'])==31


def test_gestor_general_status_without_read_grant(client,monkeypatch):
    from types import SimpleNamespace
    class LocalStatus:
        config=SimpleNamespace(device_id='test-clock')
        def status(self): return {'connected':True,'device_id':'test-clock','model':'fixture','serial':'fixture'}
        def device_lock(self,engine):
            from contextlib import nullcontext
            return nullcontext()
    monkeypatch.setattr('app.biometric.router.BiometricService',LocalStatus)
    upstream()
    assert client.get('/rrhh/biometric/status',headers=HEADERS).status_code==200


def test_master_permissions_are_not_bypassed_upstream(client):
    def handler(request):
        if request.url.path.endswith('/auth/me'):
            return httpx.Response(200,json={'id':str(ACTOR),'is_active':True,'roles':['MANAGER'],'permissions':[]})
        return httpx.Response(403,json={'detail':'Permiso insuficiente'})
    app.dependency_overrides.pop(get_actor,None)
    app.dependency_overrides[make_client]=lambda:CorrespondenciaClient(
        CorrespondenciaConfig('https://masters.test','caller-fixture'),httpx.MockTransport(handler))
    assert client.get('/admin/employees',headers=HEADERS).status_code==403


@pytest.mark.parametrize('permissions,roles,status',[
    ([],['MANAGER'],403),(['rrhh.manage'],['MANAGER'],403),
    (['rrhh.resync'],['MANAGER'],403),
    (['rrhh.manage','rrhh.resync'],['MANAGER'],202),
    (['rrhh.manage'],['MANAGER','ADMIN'],202),
    (['rrhh.manage','rrhh.resync'],['ADMIN'],403),
])
def test_force_resync_requires_gestor_and_existing_admin_grants(client,permissions,roles,status):
    upstream(roles=roles,permissions=permissions)
    response=client.post('/rrhh/biometric/sync',headers=HEADERS,
        json={'start_date':'2026-07-15','end_date':'2026-07-15','force':True})
    assert response.status_code==status,response.text


@pytest.mark.parametrize('method,path,payload',[
    ('POST','/rrhh/work-shifts',{}),('PUT','/rrhh/attendance/parameters',{}),
    ('POST','/rrhh/employee-shift-assignments',{}),('POST','/rrhh/calendar',{}),
    ('PUT','/rrhh/biometric/devices/test-clock/links/007',{'funcionario_id':str(EMPLOYEE)}),
    ('POST','/rrhh/biometric/sync',{'start_date':'2026-07-15','end_date':'2026-07-15'}),
    ('POST','/rrhh/attendance/policies',{}),
])
def test_gestor_does_not_gain_administrative_access(client,method,path,payload):
    upstream()
    assert client.request(method,path,headers=HEADERS,json=payload).status_code==403
