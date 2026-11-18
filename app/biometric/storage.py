import json
import re
import uuid
from types import SimpleNamespace
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.attendance.schemas import NormalizedRecord
from app.biometric.models import Device, EmployeeLink, Record
from app.biometric.records import attendance_record


_CLOCK_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}(?::[0-9]{2})?(?:\.0+)?')


def ensure_device(db, device_id):
    db.execute(insert(Device).values(device_id=device_id, remote_device_id=device_id, is_active=True, details={})
        .on_conflict_do_nothing(index_elements=[Device.device_id]))


def normalize_record(raw, device_id):
    if not isinstance(raw, dict) or raw.get('device_id', device_id) != device_id:
        raise ValueError('Invalid RAW record or device')
    fields = {key: raw.get(key) for key in NormalizedRecord.model_fields}
    stamp = fields['timestamp']
    if not isinstance(stamp, str) or not _CLOCK_TIMESTAMP.fullmatch(stamp):
        raise ValueError('Expected a timezone-free clock timestamp')
    value = NormalizedRecord.model_validate(fields)
    if not value.user_id.strip():
        raise ValueError('Empty biometric identifier')
    # JSONB rejects NUL and malformed Unicode. Detect them before inserting any rows.
    serialized = json.dumps(raw, allow_nan=False, ensure_ascii=False)
    serialized.encode('utf-8')
    if r'\u0000' in serialized.replace('\\\\', ''):
        raise ValueError('NUL is not supported in PostgreSQL RAW data')
    return value, raw


def validate_rows(rows, device_id):
    valid, errors = [], 0
    for raw in rows:
        try:
            valid.append(normalize_record(raw, device_id))
        except (ValidationError, ValueError, TypeError):
            errors += 1
    return valid, errors


def persist_records(db, device_id, rows, source='SOURCE_API'):
    """Persist records supplied by existing internal callers."""
    return insert_raw(db, device_id, [normalize_record(raw, device_id) for raw in rows], source)


def insert_raw(db, device_id, rows, source, *, batch_id=None, source_file_name=None):
    if source not in {'SOURCE_API','SOURCE_FILE'}:
        raise ValueError('Unknown source')
    links = {row.biometric_user_id: row.funcionario_id_external for row in db.scalars(
        select(EmployeeLink).where(EmployeeLink.device_id == device_id, EmployeeLink.is_active.is_(True)))}
    batch = []
    for value, raw in rows:
        record = attendance_record(SimpleNamespace(**value.model_dump()), device_id)
        stamp = value.timestamp
        batch.append(dict(id=uuid.uuid4(), record_id=record['record_id'], device_id=device_id,
            biometric_user_id=value.user_id, funcionario_id_external=links.get(value.user_id), timestamp=stamp,
            date=stamp.date(), time=stamp.time(), status_raw=value.status, punch_raw=value.punch,
            uid_raw=value.uid, source=source, raw_payload=raw, import_batch_id=batch_id,
            source_file_name=source_file_name))
    # A shared lock order avoids deadlocks when concurrent batches overlap.
    batch.sort(key=lambda row: row['record_id'])
    inserted = 0
    for offset in range(0, len(batch), 1000):
        inserted += len(db.scalars(insert(Record).values(batch[offset:offset + 1000]).on_conflict_do_nothing(
            index_elements=[Record.record_id]).returning(Record.id)).all())
    return inserted


def record_output(row):
    return dict(record_id=row.record_id, device_id=row.device_id, user_id=row.biometric_user_id,
        funcionario_id=row.funcionario_id_external, linked=row.funcionario_id_external is not None,
        timestamp=row.timestamp, date=row.date, time=row.time,
        status=row.status_raw, punch=row.punch_raw, uid=row.uid_raw, source=row.source)


def raw_record_output(row):
    return dict(**record_output(row), source_file_name=row.source_file_name,
                import_batch_id=row.import_batch_id, imported_at=row.synced_at,
                raw_payload=row.raw_payload)


def sync_output(row):
    return dict(id=row.id, device_id=row.device_id, requested_start_date=row.requested_from,
        requested_end_date=row.requested_to, status=row.status, created_at=row.created_at,
        started_at=row.started_at, finished_at=row.finished_at, downloaded_records=row.records_received,
        new_records=row.records_inserted, duplicates_ignored=row.records_duplicated,
        received_records=row.records_received, duplicate_records=row.records_duplicated,
        error_records=row.error_records, origin=row.origin, source_file_name=row.source_file_name,
        executed_by=row.executed_by_user_id_external,
        error_code=row.error_code, error_message=row.error_message, planned_ranges=row.planned_ranges,
        force=row.force, full_history_download=False)
