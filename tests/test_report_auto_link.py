"""Reporte de asistencia: vínculo existente o resolución automática por CI."""
from datetime import date
import uuid

from sqlalchemy import func, select

from app.biometric.models import Coverage, EmployeeLink, Record
from app.biometric.storage import ensure_device, persist_records
from app.core.security import make_client
from app.external.correspondencia.schemas import Employee
from app.main import app
from tests.conftest import EMPLOYEE, FakeCorrespondencia


ROUTE = f'/rrhh/attendance/employees/{EMPLOYEE}/report'


def _raw_snapshot(db):
    return [(row.record_id, row.biometric_user_id, row.funcionario_id_external, row.timestamp,
             row.status_raw, row.punch_raw, row.uid_raw, row.raw_payload)
            for row in db.scalars(select(Record).order_by(Record.record_id))]


def _seed_marks(db, user_id='007', stamp='2026-07-15T08:10:00'):
    ensure_device(db, 'test-clock')
    persist_records(db, 'test-clock', [
        dict(user_id=user_id, timestamp=stamp, status=15, punch=255, uid=42)])
    db.add(Coverage(device_id='test-clock', date=date(2026, 7, 15), status='COMPLETE',
                    records_count=1, attempt_count=1))
    db.commit()


def test_existing_link_is_reused(client, db):
    ensure_device(db, 'test-clock')
    db.add(EmployeeLink(device_id='test-clock', biometric_user_id='007',
                        funcionario_id_external=EMPLOYEE, is_active=True))
    db.commit()
    _seed_marks(db)

    response = client.get(ROUTE, params={'date_from': '2026-07-15', 'date_to': '2026-07-15'})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['summary']['total_records'] == 1
    assert db.scalar(select(func.count()).select_from(EmployeeLink)) == 1
    assert 'biometric_employee_link' not in report['pending_configuration']


def test_missing_link_reads_historical_raw_by_document_without_creating_link(client, db):
    _seed_marks(db)
    assert db.scalar(select(Record)).funcionario_id_external is None
    before = _raw_snapshot(db)
    assert db.scalar(select(func.count()).select_from(EmployeeLink)) == 0

    response = client.get(ROUTE, params={'date_from': '2026-07-15', 'date_to': '2026-07-15'})
    assert response.status_code == 200, response.text
    report = response.json()

    assert db.get(EmployeeLink, ('test-clock', '007')) is None
    assert report['summary']['total_records'] == 1
    assert report['days'][0]['original_records'][0]['user_id'] == '007'
    assert 'biometric_employee_link' not in report['pending_configuration']
    assert _raw_snapshot(db) == before
    assert db.scalar(select(Record)).funcionario_id_external is None


def test_historical_uuid_is_rebound_in_memory_by_document(client, db):
    old_employee = uuid.UUID('4508d14b-1243-4552-a4a0-42571b49420a')
    ensure_device(db, 'test-clock')
    persist_records(db, 'test-clock', [dict(user_id='007', timestamp='2026-07-15T08:10:00', status=15, punch=255, uid=42)])
    row = db.scalar(select(Record))
    row.funcionario_id_external = old_employee
    db.add(EmployeeLink(device_id='test-clock', biometric_user_id='007', funcionario_id_external=old_employee, is_active=False))
    db.add(Coverage(device_id='test-clock', date=date(2026, 7, 15), status='COMPLETE', records_count=1, attempt_count=1))
    db.commit()
    before = _raw_snapshot(db)
    response = client.get(ROUTE, params={'date_from': '2026-07-15', 'date_to': '2026-07-15'})
    assert response.status_code == 200, response.text
    assert response.json()['days'][0]['original_records'][0]['funcionario_id'] == str(EMPLOYEE)
    assert _raw_snapshot(db) == before


def test_document_lookup_is_idempotent_on_second_report(client, db):
    _seed_marks(db)
    first = client.get(ROUTE, params={'date_from': '2026-07-15', 'date_to': '2026-07-15'})
    second = client.get(ROUTE, params={'date_from': '2026-07-15', 'date_to': '2026-07-15'})
    assert first.status_code == second.status_code == 200
    assert db.scalar(select(func.count()).select_from(EmployeeLink)) == 0


def test_employee_without_document_or_marks_stays_unlinked(client, db):
    class NoDocument(FakeCorrespondencia):
        def get_employee(self, employee_id):
            return Employee(id=employee_id, first_name='Sin', last_name='CI', document_number=None)

    app.dependency_overrides[make_client] = NoDocument
    try:
        response = client.get(ROUTE, params={'date_from': '2026-07-15', 'date_to': '2026-07-15'})
    finally:
        app.dependency_overrides[make_client] = FakeCorrespondencia

    assert response.status_code == 200, response.text
    assert db.scalar(select(func.count()).select_from(EmployeeLink)) == 0
    assert 'biometric_employee_link' in response.json()['pending_configuration']


def test_make_client_prefers_request_bearer(monkeypatch):
    from starlette.requests import Request
    from app.core.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv('CORRESPONDENCIA_API_URL', 'https://correspondencia.example.test/api')
    monkeypatch.setenv('CORRESPONDENCIA_API_TOKEN', 'technical-should-not-win')
    get_settings.cache_clear()

    scope = {
        'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
        'method': 'GET', 'scheme': 'https', 'path': '/', 'raw_path': b'/',
        'query_string': b'', 'headers': [(b'authorization', b'Bearer user-delegated-token')],
        'client': ('127.0.0.1', 123), 'server': ('test', 443),
    }
    client = make_client(Request(scope))
    assert client.config.token == 'user-delegated-token'
    get_settings.cache_clear()
