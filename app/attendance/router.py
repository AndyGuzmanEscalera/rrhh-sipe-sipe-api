import uuid
from datetime import date
from fastapi import APIRouter,Depends
from pydantic import BaseModel,Field,model_validator
from sqlalchemy import select,or_,text
from app.core.clock import local_today
from app.core.security import require_permission,make_client
from app.core.errors import problem
from app.db.session import get_db
from app.attendance.models import Policy,Parameters
from app.attendance.schemas import ParametersInput,GenerateInput
from app.attendance.parameters import ParametersRevision,parameters_output,add_revision
from app.attendance.repository import AttendanceRepository
from app.attendance.service import AttendanceService
from app.attendance.evaluation_models import GenerationResult,ContractCatalog,ContractOption

router=APIRouter(prefix='/rrhh/attendance',tags=['attendance'])


@router.get('/parameters')
def parameters(on_date:date|None=None,db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    _,row=AttendanceRepository(db).parameters_on(on_date or local_today())
    return parameters_output(row)


@router.put('/parameters')
def save_parameters(payload:ParametersRevision,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage'))):
    # Validity is mandatory: an unversioned PUT would rewrite historical reports.
    return parameters_output(add_revision(db,payload,actor.id))


@router.get('/parameters/revisions')
def parameter_revisions(db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    return [parameters_output(r) for r in db.scalars(select(Parameters).order_by(Parameters.valid_from))]


class CloseRevision(BaseModel):
    valid_to:date


@router.post('/parameters/revisions/{revision_id}/close')
def close_parameters(revision_id:uuid.UUID,payload:CloseRevision,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage'))):
    db.execute(text('SELECT pg_advisory_xact_lock(73611003)'))
    row=db.get(Parameters,revision_id)
    if row is None: raise problem('parameters_not_found','Revisión no encontrada.',404)
    if payload.valid_to<max(local_today(),row.valid_from) or (row.valid_to and payload.valid_to>row.valid_to):
        raise problem('historical_revision_locked','Solo puede acortar vigencia desde hoy; no se cambia el pasado.',409)
    row.valid_to=payload.valid_to
    db.commit()
    return parameters_output(row)


class PolicyInput(BaseModel):
    contract_type_id_external:uuid.UUID
    name:str=Field(min_length=1,max_length=200)
    valid_from:date
    valid_to:date|None=None
    cutoff_day:int|None=Field(default=None,ge=1,le=31)
    period_semantics_confirmed:bool=False
    population_confirmed:bool=False
    employee_ids_external:list[uuid.UUID]=[]
    confirmation_reference:str|None=None

    @model_validator(mode='after')
    def coherent(self):
        if self.valid_to and self.valid_to<self.valid_from: raise ValueError('Invalid validity')
        if len(set(self.employee_ids_external))!=len(self.employee_ids_external): raise ValueError('Repeated employee')
        if (self.period_semantics_confirmed or self.population_confirmed) and not self.confirmation_reference:
            raise ValueError('Institutional confirmation reference required')
        return self


@router.post('/policies',status_code=201)
def policy(payload:PolicyInput,db=Depends(get_db),actor=Depends(require_permission('rrhh.manage')),client=Depends(make_client)):
    for employee_id in payload.employee_ids_external: client.get_employee(employee_id)
    db.execute(text('SELECT pg_advisory_xact_lock(73611005)'))
    if db.scalar(select(Policy.id).where(Policy.contract_type_id_external==payload.contract_type_id_external,
            Policy.valid_from<=(payload.valid_to or date.max),or_(Policy.valid_to.is_(None),Policy.valid_to>=payload.valid_from)).limit(1)):
        raise problem('policy_overlap','Vigencias superpuestas.',409)
    row=Policy(**payload.model_dump(exclude={'employee_ids_external'}),
        employee_ids_external=[str(x) for x in payload.employee_ids_external],confirmed_by_user_id_external=actor.id)
    db.add(row);db.commit()
    return row


@router.get('/contracts',response_model=ContractCatalog)
def contracts(db=Depends(get_db),actor=Depends(require_permission('rrhh.read'))):
    rows=list(db.scalars(select(Policy).where(Policy.period_semantics_confirmed.is_(True),Policy.population_confirmed.is_(True))))
    items={r.contract_type_id_external:ContractOption(id=r.contract_type_id_external,name=r.name) for r in rows}
    return ContractCatalog(items=tuple(items.values()),pending_configuration=() if items else ('employee_contract_assignment','confirmed_contract_period_policy'))


@router.post('/generate',response_model=GenerationResult)
def generate(payload:GenerateInput,db=Depends(get_db),actor=Depends(require_permission('rrhh.read')),client=Depends(make_client)):
    return AttendanceService(db,client).generate(payload.contract_type_id,payload.year,payload.month)
