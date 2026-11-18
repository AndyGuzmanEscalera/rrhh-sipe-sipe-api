from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.biometric.models import Coverage, Record
from app.biometric.storage import ensure_device, persist_records
from tests.conftest import ACTOR
from tests.test_sync import Service, execute, record


IMPORT = '/rrhh/biometric/import'
RECORDS = '/rrhh/biometric/records'
HISTORY = '/rrhh/biometric/syncs'


def test_file_import_reimport_history_and_raw_filters(client, db):
    original = '007\t2026-07-15 08:10:00\t15\t255\t42\tX'
    first = record(raw_line=original, line_number=1)
    later = record('2026-07-15T08:10:02')
    different_status = {**first, 'status': 25, 'raw_line': original.replace('\t15\t', '\t25\t')}
    different_punch = {**first, 'punch': 254, 'raw_line': original.replace('\t255\t', '\t254\t')}
    payload = {
        'device_id': 'test-clock', 'source_file_name': 'fixture.txt',
        'records': [first, later, different_status, different_punch, first],
    }
    response = client.post(IMPORT, json=payload)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert (summary['received_records'], summary['new_records'], summary['duplicate_records'],
            summary['error_records'], summary['status']) == (5, 4, 1, 0, 'completed')
    original_batch_id = summary['batch_id']

    summary = client.post(IMPORT, json=payload).json()
    assert (summary['new_records'], summary['duplicate_records'], summary['status']) == (0, 5, 'completed')
    assert summary['batch_id'] != original_batch_id
    assert db.scalar(select(func.count()).select_from(Record)) == 4
    assert db.scalar(select(func.count()).select_from(Coverage)) == 0

    history = client.get(HISTORY, params={'origin': 'SOURCE_FILE', 'limit': 1}).json()
    assert history['total'] == 2
    assert [item['id'] for item in history['items']] == [summary['batch_id']]
    assert client.get(HISTORY, params={'offset': 1, 'limit': 1}).json()['items'][0]['id'] == original_batch_id
    batch = client.get(f'{HISTORY}/{original_batch_id}').json()
    assert batch['executed_by'] == str(ACTOR)
    assert batch['source_file_name'] == 'fixture.txt'
    assert batch['origin'] == 'SOURCE_FILE'
    assert batch['started_at'] and batch['finished_at']
    assert batch['requested_start_date'] == batch['requested_end_date'] == '2026-07-15'
    assert client.get(f'{HISTORY}/latest').json() == {'sync': None}

    filters = {'from_date': '2026-07-15', 'to_date': '2026-07-15', 'device_id': 'test-clock',
               'user_id': '007', 'source': 'SOURCE_FILE', 'batch_id': original_batch_id}
    first_page = client.get(RECORDS, params={**filters, 'limit': 1}).json()
    assert first_page['total'] == 4 and len(first_page['records']) == 1
    result = client.get(RECORDS, params={**filters, 'limit': 3, 'offset': 1}).json()
    stored = next(row for row in result['records'] if (row['status'], row['punch']) == (15, 255))
    assert stored['timestamp'] == '2026-07-15T08:10:00'
    assert stored['uid'] == 42 and stored['raw_payload'] == first
    assert stored['source_file_name'] == 'fixture.txt' and stored['imported_at']
    for key, value in {'from_date': '2026-07-16', 'to_date': '2026-07-14', 'device_id': 'other',
                       'user_id': '7', 'source': 'SOURCE_API', 'batch_id': summary['batch_id']}.items():
        isolated = {key: value}
        assert client.get(RECORDS, params=isolated).json()['total'] == 0
    for method in ('PUT', 'PATCH', 'DELETE'):
        assert client.request(method, RECORDS).status_code == 405


def test_file_import_counts_invalid_rows_and_records_failed_lot(client):
    payload = {'device_id': 'test-clock', 'source_file_name': 'fixture.txt',
               'records': [None, record(), {**record(), 'punch': 'not an integer'}, record('2026-07-15')],
               'received_records': 5, 'error_records': 1}
    summary = client.post(IMPORT, json=payload).json()
    assert (summary['received_records'], summary['new_records'], summary['error_records']) == (5, 1, 4)
    assert summary['status'] == 'completed'
    summary = client.post(IMPORT, json={**payload, 'records': [], 'received_records': 1}).json()
    assert summary['status'] == 'failed' and summary['error_records'] == 1
    assert client.get(f"{HISTORY}/{summary['batch_id']}").json()['error_code'] == 'biometric_no_valid_rows'
    assert client.post(IMPORT, json={**payload, 'received_records': 2}).status_code == 422


def test_insert_failure_rolls_back_raw_and_keeps_failed_lot(client, db, monkeypatch):
    from app.biometric import router

    def fail(session, *args, **kwargs):
        persist_records(session, 'test-clock', [record()])
        session.execute(text('SELECT 1/0'))

    monkeypatch.setattr(router, 'insert_raw', fail)
    response = client.post(IMPORT, json={'device_id': 'test-clock', 'records': [record()]})
    assert response.status_code == 500
    detail = response.json()['detail']
    assert detail['code'] == 'biometric_import_failed'
    assert client.get(f"{HISTORY}/{detail['batch_id']}").json()['status'] == 'failed'
    assert db.scalar(select(func.count()).select_from(Record)) == 0


def test_api_and_file_share_identity_and_keep_first_batch(client, db, factory):
    summary = client.post(IMPORT, json={'device_id': 'test-clock', 'records': [record()]}).json()
    service = Service([record(), record('2026-07-15T12:00:00', terminal_code='raw')])
    job = execute(db, factory, service, force=True)
    assert len(service.calls) == 1
    assert (job.records_received, job.records_inserted, job.records_duplicated, job.error_records) == (2, 1, 1, 0)
    file_row, api_row = db.scalars(select(Record).order_by(Record.timestamp)).all()
    assert str(file_row.import_batch_id) == summary['batch_id']
    assert file_row.source == 'SOURCE_FILE'
    assert api_row.import_batch_id == job.id and api_row.source == 'SOURCE_API'
    assert api_row.raw_payload['terminal_code'] == 'raw'
    assert client.get(HISTORY, params={'origin': 'SOURCE_API'}).json()['total'] == 1


def test_api_persists_valid_rows_even_if_a_row_is_invalid(db, factory):
    service = Service([record(), {**record(), 'punch': 2147483648}])
    job = execute(db, factory, service)
    assert job.status == 'failed' and job.error_code == 'biometric_invalid_rows'
    assert (job.records_received, job.records_inserted, job.error_records) == (2, 1, 1)
    assert db.scalar(select(Record)).import_batch_id == job.id
    assert db.scalar(select(Coverage)).status == 'FAILED'


def test_concurrent_insert_uses_postgres_conflict_resolution(db, factory):
    ensure_device(db, 'test-clock')
    db.commit()
    gate = Barrier(2, timeout=5)
    first = record()
    second = {**first, 'punch': 254}

    def insert(rows):
        with factory.begin() as session:
            gate.wait()
            return persist_records(session, 'test-clock', rows)

    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = [pool.submit(insert, rows) for rows in ([first, second], [second, first])]
        assert sorted(future.result(timeout=10) for future in attempts) == [0, 2]
    assert db.scalar(select(func.count()).select_from(Record)) == 2


@pytest.mark.parametrize('change', [{'raw_payload': {}}, {'import_batch_id': None}, {'source_file_name': 'other'}])
def test_raw_import_metadata_is_immutable(client, db, change):
    client.post(IMPORT, json={'device_id': 'test-clock', 'source_file_name': 'fixture.txt', 'records': [record()]})
    with pytest.raises(IntegrityError):
        db.execute(update(Record).values(**change))
    db.rollback()


def test_openapi_has_import_and_read_only_raw_contract(client):
    paths = client.get('/openapi.json').json()['paths']
    assert 'post' in paths[IMPORT] and 'post' in paths['/rrhh/biometric/sync']
    assert set(paths[RECORDS]) == {'get'}
    assert 'get' in paths[HISTORY] and 'get' in paths[f'{HISTORY}/{{sync_id}}']
    response = paths[RECORDS]['get']['responses']['200']['content']['application/json']
    assert response['schema']['$ref'].endswith('/RawRecords')
