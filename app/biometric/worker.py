"""Durable consumer. Scheduler is opt-in; no device calls when the queue is empty."""
import argparse
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.core.clock import local_today
from app.core.config import get_settings
from app.db.session import session_factory, get_engine
from app.biometric.models import Sync
from app.biometric.service import BiometricService
from app.biometric.api_client.errors import BiometricApiError
from app.biometric.storage import ensure_device, insert_raw, validate_rows
from app.biometric.coverage import coverage_map, begin_attempt, complete_attempt, fail_attempt
from app.biometric.sync_planner import SyncPlanner


def enqueue(db, device_id, start, end, actor_id=None, force=False):
    SyncPlanner().plan(start,end,{},local_today(),force=force)
    ensure_device(db,device_id)
    job = Sync(device_id=device_id, origin='SOURCE_API', requested_from=start, requested_to=end,
        executed_by_user_id_external=actor_id, force=force)
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if getattr(getattr(exc.orig,'diag',None),'constraint_name',None) != 'uq_sync_active':
            raise
        from app.core.errors import problem
        raise problem('biometric_sync_in_progress','Ya existe una sincronización activa.',409) from None
    return job


def run_once(factory=session_factory, bind=None, service_factory=BiometricService, today_fn=local_today):
    device_id = get_settings().biometric_device_id
    with factory() as db:
        if db.scalar(select(Sync.id).where(Sync.device_id==device_id, Sync.origin=='SOURCE_API',
                                          Sync.status.in_(['queued','running'])).limit(1)) is None:
            return False
    service = service_factory()
    with service.device_lock(bind if bind is not None else get_engine()):
        with factory() as db:
            job = db.scalar(select(Sync).where(Sync.device_id==service.config.device_id,
                Sync.origin=='SOURCE_API', Sync.status.in_(['queued','running']))
                .order_by(Sync.created_at,Sync.id).with_for_update())
            if job is None:
                return False
            job_id = job.id
            if job.status == 'running':
                fail_attempt(db,job.id,'biometric_interrupted')
                job.status,job.error_code,job.error_message='failed','biometric_interrupted','Worker anterior interrumpido.'
                job.finished_at=datetime.now(timezone.utc)
                db.commit()
                return True
            today = today_fn()
            intervals = SyncPlanner().plan(job.requested_from,job.requested_to,
                coverage_map(db,job.device_id,job.requested_from,job.requested_to),today,force=job.force)
            job.status,job.started_at='running',datetime.now(timezone.utc)
            job.planned_ranges=[{'start':r.start.isoformat(),'end':r.end.isoformat()} for r in intervals]
            db.commit()
        try:
            had_row_errors = False
            for interval in intervals:
                with factory() as db:
                    begin_attempt(db,db.get(Sync,job_id),interval)
                    db.commit()
                rows=service.download(interval.start,interval.end)
                valid, invalid = validate_rows(rows, device_id)
                outside = sum(not interval.start <= record.timestamp.date() <= interval.end
                              for record, _ in valid)
                with factory() as db:
                    received_job=db.get(Sync,job_id)
                    received_job.records_received+=len(rows)
                    received_job.error_records += invalid + outside
                    db.commit()
                if outside:
                    raise ValueError('Remote record outside requested range')
                with factory() as db:
                    job=db.get(Sync,job_id)
                    added = insert_raw(db, job.device_id, valid, job.origin, batch_id=job.id)
                    job.records_inserted+=added
                    job.records_duplicated += len(valid) - added
                    if invalid:
                        had_row_errors = True
                        fail_attempt(db, job.id, 'biometric_invalid_rows', interval=interval)
                    else:
                        complete_attempt(db,job,interval,today)
                    db.commit()
            with factory() as db:
                job=db.get(Sync,job_id)
                job.status = 'failed' if had_row_errors else 'completed'
                if had_row_errors:
                    job.error_code = 'biometric_invalid_rows'
                    job.error_message = 'Se guardaron las marcaciones válidas. Revise los errores y la cobertura del rango.'
                job.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as exc:
            code=exc.code if isinstance(exc,BiometricApiError) else 'biometric_sync_failed'
            with factory() as db:
                fail_attempt(db,job_id,code)
                job=db.get(Sync,job_id)
                job.status,job.error_code,job.error_message='failed',code,'No se completó el rango; revise cobertura por día.'
                job.finished_at=datetime.now(timezone.utc)
                db.commit()
        return True


def schedule_due(db, now=None):
    settings=get_settings()
    if not settings.scheduler_enabled:
        return None
    now=now or datetime.now(timezone.utc)
    last=db.scalar(select(Sync).where(Sync.device_id==settings.biometric_device_id,
        Sync.origin=='SOURCE_API').order_by(Sync.created_at.desc()).limit(1))
    if last and (last.status in {'queued','running'} or (now-last.created_at).total_seconds()<settings.scheduler_interval_seconds):
        return None
    today=local_today()
    return enqueue(db,settings.biometric_device_id,today-timedelta(days=1),today)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    while True:
        try:
            with session_factory() as db:
                schedule_due(db)
            run_once()
        except Exception:
            print('RRHH worker: operación no completada; revise configuración/cola.',flush=True)
            if args.once:
                raise SystemExit(1)
        if args.once:
            return
        time.sleep(get_settings().worker_poll_seconds)


if __name__=='__main__':
    main()
