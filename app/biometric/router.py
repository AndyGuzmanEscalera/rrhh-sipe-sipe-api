import uuid
from datetime import date,datetime,time,timedelta
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel
from sqlalchemy import select,update,func,text
from app.core.config import get_settings
from app.core.clock import local_today
from app.core.security import require_permission,make_client
from app.core.errors import problem
from app.db.session import get_db,get_engine
from app.attendance.schemas import DateRange,LinkInput,FileImport
from app.biometric.models import Device,Sync,EmployeeLink,LinkAudit,Record,Coverage
from app.biometric.service import BiometricService
from app.biometric.storage import ensure_device,sync_output,record_output,persist_records
from app.biometric.worker import enqueue
from app.biometric.coverage import coverage_map
from app.biometric.sync_planner import SyncPlanner

router=APIRouter(prefix='/rrhh',tags=['biometric'])


class SyncInput(DateRange):
    force:bool=False


def checked_dates(start,end):
    try:
        return DateRange(start_date=start,end_date=end)
    except ValueError:
        raise problem('invalid_date_range','Rango inválido.',422) from None


@router.get('/biometric/status')
def status(db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    service=BiometricService()
    with service.device_lock(get_engine()):
        result=service.status()
    ensure_device(db,service.config.device_id)
    row=db.get(Device,service.config.device_id)
    row.model,row.serial=result['model'],result['serial']
    db.commit()
    return result


@router.get('/biometric/users')
def users(actor=Depends(require_permission('rrhh.read'))):
    service=BiometricService()
    with service.device_lock(get_engine()):
        result=service.users()
    return {'device_id':service.config.device_id,'users':result}


@router.post('/biometric/sync',status_code=202)
def sync(payload:SyncInput,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage'))):
    if payload.force and 'rrhh.resync' not in actor.permissions and not {'ADMIN','SUPERADMIN'}.intersection(actor.roles):
        raise problem('permission_denied','Force resync requiere un administrador o rrhh.resync.',403)
    try:
        return sync_output(enqueue(db,get_settings().biometric_device_id,payload.start_date,payload.end_date,actor.id,payload.force))
    except ValueError:
        raise problem('invalid_date_range','No se admiten fechas futuras.',422) from None


@router.get('/biometric/sync/plan')
def plan(start_date:date,end_date:date,force:bool=False,db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    device=get_settings().biometric_device_id
    try:
        ranges=SyncPlanner().plan(start_date,end_date,coverage_map(db,device,start_date,end_date),local_today(),force=force)
    except ValueError:
        raise problem('invalid_date_range','Rango inválido.',422) from None
    return {'device_id':device,'ranges':[{'start_date':r.start,'end_date':r.end} for r in ranges]}


@router.get('/biometric/syncs/latest')
def latest(db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    row=db.scalar(select(Sync).where(Sync.device_id==get_settings().biometric_device_id).order_by(Sync.created_at.desc(),Sync.id.desc()).limit(1))
    return {'sync':sync_output(row) if row else None}


@router.get('/biometric/syncs')
def jobs(limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0),db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    return {'items':[sync_output(r) for r in db.scalars(select(Sync).order_by(Sync.created_at.desc(),Sync.id.desc()).offset(offset).limit(limit))]}


@router.get('/biometric/syncs/{sync_id}')
def job(sync_id:uuid.UUID,db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    row=db.get(Sync,sync_id)
    if row is None: raise problem('sync_not_found','Trabajo no encontrado.',404)
    return sync_output(row)


@router.get('/biometric/coverage')
def coverage(start_date:date,end_date:date,db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    checked_dates(start_date,end_date)
    return list(db.scalars(select(Coverage).where(Coverage.device_id==get_settings().biometric_device_id,
        Coverage.date.between(start_date,end_date)).order_by(Coverage.date)))


@router.put('/biometric/devices/{device_id}/links/{user_id}')
def link(device_id:str,user_id:str,payload:LinkInput,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage')),
         client=Depends(make_client)):
    if not 1<=len(user_id)<=100 or db.get(Device,device_id) is None:
        raise problem('device_not_found','Dispositivo/identificador inválido.',404)
    client.get_employee(payload.funcionario_id)
    db.execute(text('SELECT pg_advisory_xact_lock(73611004)'))
    row=db.get(EmployeeLink,(device_id,user_id))
    previous=row.funcionario_id_external if row else None
    if row is None:
        row=EmployeeLink(device_id=device_id,biometric_user_id=user_id,funcionario_id_external=payload.funcionario_id)
        db.add(row)
    row.funcionario_id_external=payload.funcionario_id
    row.linked_by_user_id_external=actor.id
    row.is_active=True
    db.add(LinkAudit(device_id=device_id,biometric_user_id=user_id,previous_employee_id=previous,
        new_employee_id=payload.funcionario_id,actor_id_external=actor.id))
    changed=db.execute(update(Record).where(Record.device_id==device_id,Record.biometric_user_id==user_id)
        .values(funcionario_id_external=payload.funcionario_id)).rowcount
    db.commit()
    return {'device_id':device_id,'user_id':user_id,'funcionario_id':payload.funcionario_id,'updated_records':changed}


@router.post('/biometric/import')
def import_records(payload:FileImport,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage'))):
    if db.get(Device,payload.device_id) is None: raise problem('device_not_found','Dispositivo no registrado.',404)
    added=persist_records(db,payload.device_id,[r.model_dump() for r in payload.records],'SOURCE_FILE')
    # A file import never marks a day COMPLETE.
    db.commit()
    return {'received_records':len(payload.records),'new_records':added,'source':'SOURCE_FILE'}


@router.get('/biometric/records/summary')
def summary(device_id:str,start_date:date,end_date:date,db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    checked_dates(start_date,end_date)
    condition=(Record.device_id==device_id,Record.date.between(start_date,end_date))
    total,linked=db.execute(select(func.count(),func.count(Record.funcionario_id_external)).where(*condition)).one()
    unknown=db.execute(select(Record.biometric_user_id,func.count()).where(*condition,Record.funcionario_id_external.is_(None)).group_by(Record.biometric_user_id))
    return {'total':total,'linked':linked,'unlinked':total-linked,'unknown_users':[{'user_id':u,'records':n} for u,n in unknown]}


@router.get('/attendance/employees/{employee_id}/records')
def records(employee_id:uuid.UUID,start_date:date,end_date:date,limit:int=Query(500,ge=1,le=5000),
            offset:int=Query(0,ge=0),db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    checked_dates(start_date,end_date)
    condition=(Record.funcionario_id_external==employee_id,Record.date.between(start_date,end_date))
    total=db.scalar(select(func.count()).select_from(Record).where(*condition))
    values=db.scalars(select(Record).where(*condition).order_by(Record.timestamp,Record.record_id).offset(offset).limit(limit))
    return {'funcionario_id':employee_id,'total':total,'limit':limit,'offset':offset,'records':[record_output(r) for r in values]}
