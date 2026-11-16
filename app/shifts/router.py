import uuid
from datetime import date
from fastapi import APIRouter,Depends
from sqlalchemy import select,or_
from app.core.security import require_permission,make_client
from app.core.errors import problem
from app.db.session import get_db
from app.shifts.models import Shift,Segment,Assignment,CalendarDay
from app.shifts.schemas import ShiftInput,AssignmentInput,CalendarInput
from app.core.clock import local_today
from pydantic import BaseModel

router=APIRouter(prefix='/rrhh',tags=['shifts'])


@router.get('/work-shifts')
def list_shifts(db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    return list(db.scalars(select(Shift).order_by(Shift.valid_from)))


@router.post('/work-shifts',status_code=201)
def create_shift(payload:ShiftInput,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage'))):
    row=Shift(**payload.model_dump(exclude={'segments','windows'}),
        windows=payload.model_dump(mode='json')['windows'])
    db.add(row); db.flush()
    for segment in payload.segments:
        db.add(Segment(shift_id=row.id,**segment.model_dump()))
    db.commit()
    return row


@router.post('/employee-shift-assignments',status_code=201)
def assign(payload:AssignmentInput,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage')),
           client=Depends(make_client)):
    client.get_employee(payload.funcionario_id_external)
    # Serialize all assignment writes; also avoids concurrent overlap races.
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(73611001)'))
    shift=db.get(Shift,payload.shift_id)
    if shift is None or not shift.is_active or payload.valid_from<shift.valid_from or (
            shift.valid_to and (payload.valid_to is None or payload.valid_to>shift.valid_to)):
        raise problem('invalid_shift','Turno inexistente, inactivo o fuera de vigencia.',422)
    overlap=db.scalar(select(Assignment.id).where(Assignment.funcionario_id_external==payload.funcionario_id_external,
        Assignment.valid_from<=(payload.valid_to or date.max),
        or_(Assignment.valid_to.is_(None),Assignment.valid_to>=payload.valid_from)).limit(1))
    if overlap:
        raise problem('assignment_overlap','Las vigencias se superponen; conserve el histórico.',409)
    row=Assignment(**payload.model_dump(),assigned_by_user_id_external=actor.id)
    db.add(row);db.commit()
    return row


@router.get('/employee-shift-assignments/{employee_id}')
def assignments(employee_id:uuid.UUID,db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    return list(db.scalars(select(Assignment).where(Assignment.funcionario_id_external==employee_id).order_by(Assignment.valid_from)))


class CloseAssignment(BaseModel):
    valid_to:date


@router.post('/employee-shift-assignments/{assignment_id}/close')
def close_assignment(assignment_id:uuid.UUID,payload:CloseAssignment,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage'))):
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(73611001)'))
    row=db.get(Assignment,assignment_id)
    if row is None: raise problem('assignment_not_found','Asignación no encontrada.',404)
    if payload.valid_to<max(local_today(),row.valid_from) or (row.valid_to and payload.valid_to>row.valid_to):
        raise problem('historical_assignment_locked','Solo puede acortar vigencia desde hoy; no se cambia el pasado.',409)
    row.valid_to=payload.valid_to
    db.commit()
    return row


@router.post('/calendar',status_code=201)
def calendar(payload:CalendarInput,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage')),
             client=Depends(make_client)):
    client.get_employee(payload.funcionario_id_external)
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(73611002)'))
    if db.scalar(select(CalendarDay.id).where(CalendarDay.funcionario_id_external==payload.funcionario_id_external,
            CalendarDay.date==payload.date)):
        raise problem('calendar_immutable','La fecha ya fue confirmada; no se sobrescribe.',409)
    row=CalendarDay(**payload.model_dump(),confirmed_by_user_id_external=actor.id)
    db.add(row);db.commit()
    return row
