import uuid
from datetime import date, datetime, timezone
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel
from sqlalchemy import select,update,func,text
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import get_settings
from app.core.clock import local_today
from app.core.security import require_permission,make_client
from app.core.errors import problem
from app.db.session import get_db,get_engine
from app.attendance.schemas import DateRange, LinkInput
from app.biometric.import_schemas import Batch, BatchHistory, FileImport, ImportSummary, Origin, RawRecords
from app.biometric.models import Device,Sync,EmployeeLink,LinkAudit,Record,Coverage
from app.biometric.service import BiometricService
from app.biometric.storage import (ensure_device, insert_raw, raw_record_output,
                                   record_output, sync_output, validate_rows)
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


@router.post('/biometric/sync', status_code=202, response_model=Batch)
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
    row=db.scalar(select(Sync).where(Sync.device_id==get_settings().biometric_device_id,
        Sync.origin=='SOURCE_API').order_by(Sync.created_at.desc(),Sync.id.desc()).limit(1))
    return {'sync':sync_output(row) if row else None}


@router.get('/biometric/syncs', response_model=BatchHistory)
def jobs(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
         origin: Origin | None = None, db=Depends(get_db), actor=Depends(require_permission('rrhh.read'))):
    query = select(Sync)
    if origin is not None:
        query = query.where(Sync.origin == origin)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Sync.created_at.desc(), Sync.id.desc()).offset(offset).limit(limit))
    return {'items': [sync_output(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


@router.get('/biometric/syncs/{sync_id}', response_model=Batch)
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


@router.post('/biometric/import', response_model=ImportSummary)
def import_records(payload:FileImport,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage'))):
    """Receive the rows parsed by the TXT reader and persist one audited batch."""
    if db.get(Device, payload.device_id) is None:
        if payload.device_id != get_settings().biometric_device_id:
            raise problem('device_not_found', 'Dispositivo no registrado.', 404)
        ensure_device(db, payload.device_id)
    valid, invalid = validate_rows(payload.records, payload.device_id)
    dates = [value.timestamp.date() for value, _ in valid]
    batch = Sync(device_id=payload.device_id, origin='SOURCE_FILE', source_file_name=payload.source_file_name,
                 requested_from=min(dates, default=None), requested_to=max(dates, default=None),
                 status='running', started_at=datetime.now(timezone.utc),
                 records_received=len(payload.records) + payload.error_records,
                 error_records=invalid + payload.error_records, executed_by_user_id_external=actor.id)
    db.add(batch)
    db.flush()
    try:
        with db.begin_nested():
            batch.records_inserted = insert_raw(db, batch.device_id, valid, batch.origin,
                                                batch_id=batch.id, source_file_name=batch.source_file_name)
            batch.records_duplicated = len(valid) - batch.records_inserted
    except SQLAlchemyError:
        batch.status = 'failed'
        batch.error_code = 'biometric_import_failed'
        batch.error_message = 'No se pudo guardar el lote de importación.'
    else:
        batch.status = 'completed' if valid else 'failed'
        if not valid:
            batch.error_code = 'biometric_no_valid_rows'
            batch.error_message = 'El archivo no contiene marcaciones válidas.'
    batch.finished_at = datetime.now(timezone.utc)
    db.commit()
    result = ImportSummary(batch_id=batch.id, device_id=batch.device_id, source=batch.origin, origin=batch.origin,
                           source_file_name=batch.source_file_name, received_records=batch.records_received,
                           new_records=batch.records_inserted, duplicate_records=batch.records_duplicated,
                           error_records=batch.error_records, status=batch.status, error_message=batch.error_message)
    if batch.error_code == 'biometric_import_failed':
        error = problem(batch.error_code, batch.error_message, 500)
        error.detail['batch_id'] = str(batch.id)
        raise error
    return result


@router.get('/biometric/devices')
def devices(db=Depends(get_db), actor=Depends(require_permission('rrhh.read'))):
    configured = get_settings().biometric_device_id
    ids = set(db.scalars(select(Device.device_id).where(Device.is_active.is_(True))))
    ids.add(configured)
    return {'items': [{'device_id': device_id} for device_id in sorted(ids, key=lambda x: (x != configured, x))]}


@router.get('/biometric/records', response_model=RawRecords)
def raw_records(from_date: date | None = None, to_date: date | None = None,
                user_id: str | None = Query(None, min_length=1, max_length=100, pattern=r'^[^\x00]+$'),
                device_id: str | None = Query(None, min_length=1, max_length=100, pattern=r'^[^\x00]+$'),
                source: Origin | None = None, batch_id: uuid.UUID | None = None,
                limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                db=Depends(get_db), actor=Depends(require_permission('rrhh.read'))):
    """Inspect RAW evidence in descending device time, with inclusive date filters."""
    if from_date and to_date and from_date > to_date:
        raise problem('invalid_date_range', 'La fecha inicial no puede ser posterior a la final.', 422)
    query = select(Record)
    if from_date is not None:
        query = query.where(Record.date >= from_date)
    if to_date is not None:
        query = query.where(Record.date <= to_date)
    for column, value in ((Record.biometric_user_id, user_id), (Record.device_id, device_id),
                          (Record.source, source), (Record.import_batch_id, batch_id)):
        if value is not None:
            query = query.where(column == value)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(Record.timestamp.desc(), Record.record_id).offset(offset).limit(limit))
    return {'records': [raw_record_output(row) for row in rows], 'total': total, 'limit': limit, 'offset': offset}


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
