import uuid
from datetime import datetime
from types import SimpleNamespace
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.attendance.schemas import NormalizedRecord
from app.biometric.models import Device, EmployeeLink, Record
from app.biometric.records import attendance_record


def ensure_device(db, device_id):
    db.execute(insert(Device).values(device_id=device_id, remote_device_id=device_id, is_active=True, details={})
        .on_conflict_do_nothing(index_elements=[Device.device_id]))


def persist_records(db, device_id, rows, source='SOURCE_API'):
    if source not in {'SOURCE_API','SOURCE_FILE'}:
        raise ValueError('Unknown source')
    links = {row.biometric_user_id: row.funcionario_id_external for row in db.scalars(
        select(EmployeeLink).where(EmployeeLink.device_id == device_id, EmployeeLink.is_active.is_(True)))}
    inserted, batch = 0, []
    for raw in rows:
        if raw.get('device_id', device_id) != device_id:
            raise ValueError('Wrong record device')
        value = NormalizedRecord.model_validate({key: raw.get(key) for key in NormalizedRecord.model_fields})
        record = attendance_record(SimpleNamespace(**value.model_dump()), device_id)
        stamp = value.timestamp
        batch.append(dict(id=uuid.uuid4(), record_id=record['record_id'], device_id=device_id,
            biometric_user_id=value.user_id, funcionario_id_external=links.get(value.user_id), timestamp=stamp,
            date=stamp.date(), time=stamp.time(), status_raw=value.status, punch_raw=value.punch,
            uid_raw=value.uid, source=source))
        if len(batch) == 1000:
            inserted += len(db.scalars(insert(Record).values(batch).on_conflict_do_nothing(
                index_elements=[Record.record_id]).returning(Record.id)).all())
            batch = []
    if batch:
        inserted += len(db.scalars(insert(Record).values(batch).on_conflict_do_nothing(
            index_elements=[Record.record_id]).returning(Record.id)).all())
    return inserted


def record_output(row):
    return dict(record_id=row.record_id, device_id=row.device_id, user_id=row.biometric_user_id,
        funcionario_id=row.funcionario_id_external, linked=row.funcionario_id_external is not None,
        timestamp=row.timestamp, date=row.date, time=row.time,
        status=row.status_raw, punch=row.punch_raw, uid=row.uid_raw, source=row.source)


def sync_output(row):
    return dict(id=row.id, device_id=row.device_id, requested_start_date=row.requested_from,
        requested_end_date=row.requested_to, status=row.status, created_at=row.created_at,
        started_at=row.started_at, finished_at=row.finished_at, downloaded_records=row.records_received,
        new_records=row.records_inserted, duplicates_ignored=row.records_duplicated,
        error_code=row.error_code, error_message=row.error_message, planned_ranges=row.planned_ranges,
        force=row.force, full_history_download=False)
