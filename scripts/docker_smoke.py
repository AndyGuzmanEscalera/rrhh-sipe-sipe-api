"""Pipe into the isolated validation container; never run against business data.

Modes: seed, verify. Requires BIOMETRIC_DEVICE_ID=docker-fixture and a dedicated
Compose validation DB. Does not contact either external API.
"""
import sys,json
from datetime import datetime,timedelta
from sqlalchemy import select,func
from app.core.config import get_settings
from app.core.clock import local_today
from app.db.session import session_factory
from app.biometric.models import Record,Coverage,Sync
from app.biometric.storage import ensure_device,persist_records
from app.biometric.worker import enqueue

assert get_settings().biometric_device_id=='docker-fixture', 'Validation device required'
assert get_settings().biometric_api_url=='https://biometric.invalid.test', 'No real upstream allowed'
day=local_today()-timedelta(days=1)
with session_factory() as db:
    assert db.get_bind().url.host=='postgres' and db.get_bind().url.database=='rrhh_sipe'
    if sys.argv[1]=='seed':
        assert db.scalar(select(func.count()).select_from(Record))==0, 'Empty validation DB required'
        ensure_device(db,'docker-fixture')
        persist_records(db,'docker-fixture',[{'user_id':'docker-fixture-user',
            'timestamp':datetime.combine(day,datetime.min.time()).replace(hour=8).isoformat(),
            'status':89,'punch':255,'uid':1}])
        db.add(Coverage(device_id='docker-fixture',date=day,status='COMPLETE',records_count=1))
        db.commit()
        job=enqueue(db,'docker-fixture',day,day)
        print(json.dumps({'seeded_records':1,'queued':job.status}))
    else:
        row=db.scalar(select(Record))
        job=db.scalar(select(Sync))
        assert db.scalar(select(func.count()).select_from(Record))==1
        assert row.status_raw==89 and row.punch_raw==255
        assert job.status=='completed' and job.planned_ranges==[] and job.records_received==0
        assert db.scalar(select(Coverage.status))=='COMPLETE'
        print(json.dumps({'persisted_records':1,'raw_intact':True,'worker_status':job.status,
            'plan':job.planned_ranges,'external_downloads':job.records_received}))
