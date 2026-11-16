from datetime import date,timedelta
from sqlalchemy import select,func
import pytest
from app.biometric.models import Coverage,Record,Sync,LinkAudit
from app.biometric.storage import ensure_device
from app.biometric.worker import enqueue,run_once,schedule_due
from app.core.config import get_settings
from app.core.security import get_actor
from app.external.correspondencia.schemas import Actor
from app.main import app
from tests.test_sync import Service,record,TODAY
from tests.conftest import EMPLOYEE,ACTOR


def test_worker_uses_only_gaps(db,factory):
    ensure_device(db,'test-clock')
    for n in range(1,21):
        db.add(Coverage(device_id='test-clock',date=date(2026,7,n),status='FAILED' if n in {11,12} else 'COMPLETE'))
    db.commit()
    job=enqueue(db,'test-clock',date(2026,7,1),TODAY)
    service=Service([])
    run_once(factory,service_factory=lambda:service,today_fn=lambda:TODAY)
    assert service.calls==[(date(2026,7,11),date(2026,7,12)),(date(2026,7,21),TODAY)]
    db.expire_all()
    assert db.get(Sync,job.id).status=='completed'
    assert db.scalar(select(Coverage.status).where(Coverage.date==TODAY))=='OPEN'
    assert db.scalar(select(Coverage.attempt_count).where(Coverage.date==date(2026,7,1)))==0


def test_partial_range_success_survives_later_failure(db,factory):
    ensure_device(db,'test-clock')
    db.add(Coverage(device_id='test-clock',date=date(2026,7,2),status='COMPLETE'));db.commit()
    job=enqueue(db,'test-clock',date(2026,7,1),date(2026,7,3))
    service=Service([])
    def download(start,end):
        if start.day==3: raise TimeoutError('synthetic')
        return []
    service.download=download
    run_once(factory,service_factory=lambda:service,today_fn=lambda:TODAY)
    db.expire_all()
    assert db.get(Sync,job.id).status=='failed'
    assert dict(db.execute(select(Coverage.date,Coverage.status)).all())=={
        date(2026,7,1):'COMPLETE',date(2026,7,2):'COMPLETE',date(2026,7,3):'FAILED'}


def test_linking_is_explicit_audited_and_raw_preserved(client,db):
    from app.biometric.storage import persist_records
    ensure_device(db,'test-clock')
    persist_records(db,'test-clock',[record()]);db.commit()
    row=db.scalar(select(Record));identity=row.record_id
    response=client.put('/rrhh/biometric/devices/test-clock/links/007',json={'funcionario_id':str(EMPLOYEE)})
    assert response.status_code==200,response.text
    db.expire_all();row=db.scalar(select(Record))
    assert row.funcionario_id_external==EMPLOYEE and row.record_id==identity and row.punch_raw==255
    assert db.scalar(select(func.count()).select_from(LinkAudit))==1


def test_force_requires_admin_or_explicit_permission(client):
    app.dependency_overrides[get_actor]=lambda:Actor(id=ACTOR,is_active=True,roles=['MANAGER'],permissions=['rrhh.manage'])
    assert client.post('/rrhh/biometric/sync',json={'start_date':'2026-07-15','end_date':'2026-07-15','force':True}).status_code==403


def test_scheduler_is_configurable_and_never_duplicates_active_job(db,monkeypatch):
    monkeypatch.setenv('SCHEDULER_ENABLED','true');get_settings.cache_clear()
    try:
        assert schedule_due(db) is not None
        assert schedule_due(db) is None
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize('count',range(1,8))
def test_one_to_seven_original_marks_retained(count):
    from tests.test_engine import DAY,EMPLOYEE as E,params,plan,marks
    from app.attendance.evaluation import evaluate_day
    values=('07:57:00','09:00:00','10:00:00','12:04:00','13:52:00','16:00:00','18:06:00')[:count]
    records=marks(*values)
    result=evaluate_day(E,DAY,plan(),params(),records)
    assert len(result.original_records)==count
    assert all(r in result.original_records for r in records)
