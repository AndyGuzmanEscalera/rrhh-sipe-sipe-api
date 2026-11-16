from datetime import date
import httpx
from sqlalchemy import select
from app.main import app
from app.core.security import make_client
from app.external.correspondencia.client import CorrespondenciaClient
from app.external.correspondencia.config import CorrespondenciaConfig
from app.attendance.models import Parameters
from tests.conftest import EMPLOYEE,CONTRACT


def test_no_default_contract_and_explicit_policy(client):
    assert client.get('/rrhh/attendance/contracts').json()['items']==[]
    payload={'contract_type_id_external':str(CONTRACT),'name':'Operator confirmed test scope',
        'valid_from':'2026-01-01','valid_to':'2026-12-31','cutoff_day':31,
        'period_semantics_confirmed':True,'population_confirmed':True,'employee_ids_external':[str(EMPLOYEE)]}
    assert client.post('/rrhh/attendance/policies',json=payload).status_code==422
    payload['confirmation_reference']='fixture institutional evidence only'
    assert client.post('/rrhh/attendance/policies',json=payload).status_code==201
    assert client.post('/rrhh/attendance/policies',json=payload).status_code==409
    assert client.get('/rrhh/attendance/contracts').json()['items'][0]['id']==str(CONTRACT)


def test_external_outage_report_fails_closed(client):
    api=CorrespondenciaClient(CorrespondenciaConfig('https://masters.example.test','fixture'),
        httpx.MockTransport(lambda r:httpx.Response(503,json={'detail':'secret'})))
    app.dependency_overrides[make_client]=lambda:api
    response=client.get(f'/rrhh/attendance/employees/{EMPLOYEE}/report',params={'contract_type_id':str(CONTRACT),'year':2026,'month':7})
    assert response.status_code==503 and 'secret' not in response.text


def test_invalid_query_range_is_not_internal_error(client):
    response=client.get('/rrhh/biometric/coverage?start_date=2026-07-20&end_date=2026-07-01')
    assert response.status_code==422


def test_future_sync_rejected(client):
    assert client.post('/rrhh/biometric/sync',json={'start_date':'2099-01-01','end_date':'2099-01-01'}).status_code==422


def test_close_parameter_validity_only_for_future_change(client,db,monkeypatch):
    monkeypatch.setattr('app.attendance.router.local_today',lambda:date(2026,7,30))
    values=dict(morning_entry_tolerance=300,morning_exit_tolerance=0,afternoon_entry_tolerance=300,
        afternoon_exit_tolerance=0,minimum_return_interval=600,duplicate_window=60,valid_from='2026-07-01')
    first=client.put('/rrhh/attendance/parameters',json=values).json()
    assert client.post(f"/rrhh/attendance/parameters/revisions/{first['id']}/close",json={'valid_to':'2026-07-29'}).status_code==409
    assert client.post(f"/rrhh/attendance/parameters/revisions/{first['id']}/close",json={'valid_to':'2026-07-30'}).status_code==200
    assert client.put('/rrhh/attendance/parameters',json={**values,'valid_from':'2026-07-31','morning_entry_tolerance':600}).status_code==200
    assert client.get('/rrhh/attendance/parameters?on_date=2026-07-15').json()['morning_entry_tolerance']==300
    assert client.get('/rrhh/attendance/parameters?on_date=2026-07-31').json()['morning_entry_tolerance']==600
