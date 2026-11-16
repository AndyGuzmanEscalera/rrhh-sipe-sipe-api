import httpx
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import make_client,get_actor
from app.core.config import get_settings
from app.core.permissions import capabilities
from app.external.correspondencia.client import CorrespondenciaClient
from app.external.correspondencia.config import CorrespondenciaConfig
from app.external.correspondencia.schemas import Actor
from tests.conftest import ACTOR,EMPLOYEE


def test_single_facade_login_refresh_me_logout(monkeypatch):
    calls=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        assert 'technical-data-token' not in request.headers.get('authorization','') if request.method=='POST' else True
        if request.url.path=='/api/auth/login':
            return httpx.Response(200,json={'access_token':'upstream-access','expires_in':900,'user':{'id':str(ACTOR)}},
                headers={'set-cookie':'refresh_token=upstream-refresh-1; HttpOnly; Secure; Path=/api/auth; Max-Age=600'})
        if request.url.path=='/api/auth/refresh':
            assert 'refresh_token=upstream-refresh-1' in request.headers['cookie']
            return httpx.Response(200,json={'access_token':'upstream-rotated','expires_in':900},
                headers={'set-cookie':'refresh_token=upstream-refresh-2; HttpOnly; Secure; Path=/api/auth; Max-Age=600'})
        if request.url.path=='/api/auth/me':
            assert request.headers['authorization']=='Bearer upstream-rotated'
            return httpx.Response(200,json={'id':str(ACTOR),'username':'test','is_active':True,'roles':['MANAGER'],'permissions':['rrhh.read'],
                'employee':{'id':str(EMPLOYEE),'full_name':'Fixture','document_number':'TEST'}})
        assert 'refresh_token=upstream-refresh-2' in request.headers['cookie']
        return httpx.Response(204)
    remote=CorrespondenciaClient(CorrespondenciaConfig('https://masters.test/api','technical-data-token'),httpx.MockTransport(handler))
    app.dependency_overrides[make_client]=lambda:remote
    try:
        with TestClient(app,base_url='https://rrhh.test') as client:
            login=client.post('/auth/login',json={'username':'test','password':'fixture-only'})
            assert login.status_code==200 and login.json()['access_token']=='upstream-access'
            assert 'httponly' in login.headers['set-cookie'].lower()
            assert client.cookies.get('rrhh_refresh_token')=='upstream-refresh-1'
            refreshed=client.post('/auth/refresh');assert refreshed.json()['access_token']=='upstream-rotated'
            me=client.get('/auth/me',headers={'Authorization':'Bearer upstream-rotated'})
            assert me.json()['employee']['full_name']=='Fixture'
            assert client.post('/auth/logout').status_code==204
            assert client.cookies.get('rrhh_refresh_token') is None
    finally: app.dependency_overrides.clear()
    assert calls==[('POST','/api/auth/login'),('POST','/api/auth/refresh'),('GET','/api/auth/me'),('POST','/api/auth/logout')]


def test_revocation_checked_each_request_no_identity_cache():
    count=0
    def handler(request):
        nonlocal count
        count+=1
        return httpx.Response(200,json={'id':str(ACTOR),'is_active':count==1,'roles':['MANAGER'],'permissions':['rrhh.read']})
    remote=CorrespondenciaClient(CorrespondenciaConfig('https://masters.test',''),httpx.MockTransport(handler))
    app.dependency_overrides[make_client]=lambda:remote
    try:
        with TestClient(app) as client:
            headers={'Authorization':'Bearer fixture'}
            assert client.get('/auth/me',headers=headers).status_code==200
            assert client.get('/auth/me',headers=headers).status_code==403
    finally: app.dependency_overrides.clear()


@pytest.mark.parametrize('roles,permissions,force',[
    (['MANAGER'],[],False),(['MANAGER'],['rrhh.manage'],False),(['MANAGER','ADMIN'],['rrhh.manage'],True),
    (['SUPERADMIN'],['rrhh.manage'],False),(['MANAGER'],['rrhh.manage','rrhh.resync'],True)])
def test_capability_mapping(roles,permissions,force):
    value=capabilities(Actor(id=ACTOR,is_active=True,roles=roles,permissions=permissions))
    assert value['force_resync']==force
    assert value['reports']==('MANAGER' in roles)


def test_password_not_echoed_on_validation():
    with TestClient(app) as client:
        response=client.post('/auth/login',json={'password':'private-fixture-password'})
        assert response.status_code==422 and 'private-fixture-password' not in response.text


def test_public_profile_only_advertises_routes_implemented_by_facade():
    from app.external.correspondencia.router import me
    actor=Actor(id=ACTOR,is_active=True,roles=['MANAGER'],permissions=['rrhh.manage','master_data.employees.read','master_data.employees.manage'])
    assert set(me(actor)['permissions'])=={'rrhh.read','rrhh.manage','master_data.employees.read','master_data.organizational_units.read','master_data.positions.read'}
    assert 'master_data.employees.manage' in actor.permissions


@pytest.mark.parametrize('status,cookie',[(401,None),(200,None),(200,'refresh_token=x; Max-Age=invalid'),(200,'refresh_token=; Max-Age=600')])
def test_failed_refresh_sanitized(status,cookie):
    def handler(request):
        return httpx.Response(status,json={'access_token':'fixture','detail':'PRIVATE-UPSTREAM'},
            headers={'set-cookie':cookie} if cookie else {})
    remote=CorrespondenciaClient(CorrespondenciaConfig('https://masters.test',''),httpx.MockTransport(handler))
    app.dependency_overrides[make_client]=lambda:remote
    try:
        with TestClient(app,base_url='https://rrhh.test') as client:
            client.cookies.set('rrhh_refresh_token','fixture')
            response=client.post('/auth/refresh')
            assert response.status_code==(401 if status==401 else 503)
            assert 'PRIVATE-UPSTREAM' not in response.text
            if status==401: assert 'Max-Age=0' in response.headers['set-cookie']
    finally: app.dependency_overrides.clear()


@pytest.mark.parametrize('config',[{'app_env':'production'},{'session_cookie_samesite':'none'}])
def test_insecure_cookie_configuration_rejected(config):
    from pydantic import ValidationError
    from app.core.config import Settings
    with pytest.raises(ValidationError): Settings(session_cookie_secure=False,**config)
