import uuid
from datetime import date,timedelta
from types import SimpleNamespace
from contextlib import contextmanager
from sqlalchemy import select,func
import pytest
from app.biometric.worker import enqueue,run_once,schedule_due
from app.biometric.models import Record,Sync,Coverage
from app.biometric.api_client.errors import BiometricApiError
from app.biometric.storage import persist_records,ensure_device
from tests.conftest import ACTOR

TODAY=date(2026,7,30)


def record(stamp='2026-07-15T08:10:00',**extra):
    return dict(user_id='007',timestamp=stamp,status=15,punch=255,uid=42,**extra)


class Service:
    def __init__(self,rows=None,error=None):
        self.config=SimpleNamespace(device_id='test-clock')
        self.rows=rows if rows is not None else [record()]
        self.error=error
        self.calls=[]
    @contextmanager
    def device_lock(self,engine): yield
    def download(self,start_date,end_date):
        self.calls.append((start_date,end_date))
        if self.error: raise self.error
        return [r for r in self.rows if start_date.isoformat()<=r['timestamp'][:10]<=end_date.isoformat()]


def execute(db,factory,service,force=False,start=date(2026,7,15),end=date(2026,7,15),today=TODAY):
    job=enqueue(db,'test-clock',start,end,ACTOR,force)
    run_once(factory,service_factory=lambda:service,today_fn=lambda:today)
    db.expire_all()
    return db.get(Sync,job.id)


def test_sync_new_dedup_and_new_record(db,factory):
    assert execute(db,factory,Service()).records_inserted==1
    cached=Service()
    assert execute(db,factory,cached).records_received==0 and not cached.calls
    again=execute(db,factory,Service(),True)
    assert (again.records_received,again.records_inserted,again.records_duplicated)==(1,0,1)
    more=execute(db,factory,Service([record(),record('2026-07-15T12:00:00')]),True)
    assert (more.records_inserted,more.records_duplicated)==(1,1)
    assert db.scalar(select(func.count()).select_from(Record))==2
    coverage=db.scalar(select(Coverage))
    assert coverage.status=='COMPLETE' and coverage.records_count==2 and coverage.attempt_count==3
    raw=db.scalar(select(Record).order_by(Record.timestamp))
    assert raw.status_raw==15 and raw.punch_raw==255 and raw.funcionario_id_external is None


def test_today_open_and_yesterday_requires_another_fetch(db,factory):
    row=record('2026-07-30T08:00:00')
    execute(db,factory,Service([row]),start=TODAY,end=TODAY)
    assert db.scalar(select(Coverage)).status=='OPEN'
    execute(db,factory,Service([row]),start=TODAY,end=TODAY,today=TODAY+timedelta(days=1))
    assert db.scalar(select(Coverage)).status=='COMPLETE'


@pytest.mark.parametrize('code',['biometric_api_timeout','biometric_api_unreachable','biometric_api_unauthorized'])
def test_remote_failure_retry(db,factory,code):
    job=execute(db,factory,Service(error=BiometricApiError(code,'private')))
    assert job.status=='failed' and job.error_code==code
    assert db.scalar(select(Coverage)).status=='FAILED'
    assert db.scalar(select(func.count()).select_from(Record))==0
    assert execute(db,factory,Service()).status=='completed'
    assert db.scalar(select(Coverage)).status=='COMPLETE'


def test_out_of_range_is_atomic(db,factory):
    service=Service()
    service.download=lambda a,b:[record(),record('2026-07-16T12:00:00')]
    job=execute(db,factory,service)
    assert job.status=='failed'
    assert db.scalar(select(func.count()).select_from(Record))==0


def test_only_one_active_job(db):
    enqueue(db,'test-clock',TODAY,TODAY)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error: enqueue(db,'test-clock',TODAY,TODAY)
    assert error.value.status_code==409


def test_interrupted_worker(db,factory):
    job=enqueue(db,'test-clock',date(2026,7,15),date(2026,7,15))
    job.status='running';db.commit()
    service=Service()
    run_once(factory,service_factory=lambda:service)
    db.expire_all()
    assert db.get(Sync,job.id).error_code=='biometric_interrupted' and not service.calls


def test_uid_does_not_affect_hash_and_file_does_not_close_coverage(db):
    ensure_device(db,'test-clock')
    assert persist_records(db,'test-clock',[record()],'SOURCE_FILE')==1
    value=record();value['uid']=99999
    assert persist_records(db,'test-clock',[value],'SOURCE_FILE')==0
    assert db.scalar(select(func.count()).select_from(Coverage))==0


def test_scheduler_disabled_by_default(db):
    assert schedule_due(db) is None


def test_force_permission_and_plan(client):
    result=client.post('/rrhh/biometric/sync',json={'start_date':'2026-07-15','end_date':'2026-07-15','force':True})
    assert result.status_code==202
    result=client.get('/rrhh/biometric/sync/plan?start_date=2026-07-15&end_date=2026-07-16')
    assert result.json()['ranges']==[{'start_date':'2026-07-15','end_date':'2026-07-16'}]
