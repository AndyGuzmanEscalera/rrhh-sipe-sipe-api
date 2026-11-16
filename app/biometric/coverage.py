from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from app.biometric.models import Coverage, Record
from app.biometric.sync_planner import days_between


def coverage_map(db, device_id, start, end):
    return {row.date: row.status for row in db.scalars(select(Coverage).where(
        Coverage.device_id==device_id, Coverage.date.between(start,end)))}


def begin_attempt(db, job, interval):
    now = datetime.now(timezone.utc)
    for day in days_between(interval.start, interval.end):
        db.execute(insert(Coverage).values(device_id=job.device_id, date=day, status='OPEN',
            records_count=0, attempt_count=1, last_sync_at=now, last_attempt_sync_id=job.id)
            .on_conflict_do_update(index_elements=[Coverage.device_id,Coverage.date], set_={
                'status':'OPEN', 'attempt_count':Coverage.attempt_count+1,
                'last_sync_at':now, 'last_attempt_sync_id':job.id, 'last_error':None,'updated_at':now}))


def complete_attempt(db, job, interval, today):
    for row in db.scalars(select(Coverage).where(Coverage.device_id==job.device_id,
            Coverage.date.between(interval.start,interval.end))):
        row.status = 'COMPLETE' if row.date < today else 'OPEN'
        row.records_count = db.scalar(select(func.count()).select_from(Record).where(
            Record.device_id==job.device_id, Record.date==row.date))
        row.last_successful_sync_id = job.id
        row.last_error = None


def fail_attempt(db, job_id, code):
    for row in db.scalars(select(Coverage).where(Coverage.last_attempt_sync_id==job_id,
            Coverage.status=='OPEN')):
        row.status, row.last_error = 'FAILED', code
