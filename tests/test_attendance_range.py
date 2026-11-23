from datetime import date

import pytest
from sqlalchemy import select

from app.biometric.models import Record, Sync
from app.biometric.service import BiometricService
from app.biometric.storage import persist_records
from tests.conftest import EMPLOYEE
from tests.test_history import seed


ROUTE = f'/rrhh/attendance/employees/{EMPLOYEE}/report'


def test_range_reads_only_persisted_raw_and_reuses_evaluator(client, db, monkeypatch):
    seed(db)
    def forbidden(*args, **kwargs):
        pytest.fail('Report accessed the biometric device')
    monkeypatch.setattr(BiometricService, '__init__', forbidden)
    persist_records(db, 'test-clock', [
        dict(user_id='007', timestamp=stamp, status=89, punch=255, uid=12)
        for stamp in ('2026-07-01T08:11:00', '2026-07-01T08:12:00', '2026-08-01T23:59:59', '2026-08-02T00:00:00')
    ])
    db.commit()
    def snapshot():
        return [(row.record_id, row.timestamp, row.status_raw, row.punch_raw, row.uid_raw, row.raw_payload)
                for row in db.scalars(select(Record).order_by(Record.record_id))]
    before = snapshot()
    response = client.get(ROUTE, params={'date_from': '2026-07-01', 'date_to': '2026-08-01'})
    assert response.status_code == 200, response.text
    report = response.json()
    assert len(report['days']) == 32
    assert report['days'][0]['date'] == '2026-07-01'
    assert len(report['days'][0]['original_records']) == 6
    assert report['days'][0]['schedule']['name'] == 'Fixture 8'
    assert report['days'][15]['late_seconds'] == 0
    assert report['days'][-1]['original_records'][0]['timestamp'] == '2026-08-01T23:59:59'
    assert report['days'][-1]['original_records'][0]['uid'] == 12
    assert report['summary']['total_records'] == 11
    second = client.get(ROUTE, params={'date_from': '2026-08-02', 'date_to': '2026-08-02'}).json()
    assert len(second['days']) == 1 and second['summary']['total_records'] == 1
    assert snapshot() == before
    assert db.scalars(select(Sync)).all() == []


def test_empty_current_month_without_link(client):
    today = date.today()
    month = today.replace(day=1)
    response = client.get(ROUTE, params={'date_from': month, 'date_to': today})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['employee']['full_name'] == 'Synthetic Employee'
    assert report['days'][0]['date'] == month.isoformat()
    assert len(report['days']) == today.day
    assert report['summary']['total_records'] == 0
    assert all(row['status'] == 'NO_DATA' for row in report['days'])
    assert 'biometric_employee_link' in report['pending_configuration']


@pytest.mark.parametrize('params', [
    {'date_from': '2026-11-01'},
    {'date_from': '2026-11-02', 'date_to': '2026-11-01'},
    {'date_from': '2024-11-01', 'date_to': '2026-11-01'},
    {'date_from': '2026-11-01', 'date_to': '2026-11-02', 'year': 2026},
])
def test_invalid_range(client, params):
    assert client.get(ROUTE, params=params).status_code == 422


def test_report_openapi_documents_inclusive_range(client):
    key = '/rrhh/attendance/employees/{employee_id}/report'
    operation = client.get('/openapi.json').json()['paths'][key]['get']
    assert {'date_from', 'date_to'} <= {parameter['name'] for parameter in operation['parameters']}
