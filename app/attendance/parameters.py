from datetime import date
from typing import Literal
from pydantic import BaseModel,model_validator
from sqlalchemy import select,or_,text
from app.attendance.schemas import ParametersInput
from app.attendance.models import Parameters
from app.core.errors import problem


class ParametersRevision(ParametersInput):
    valid_from:date
    valid_to:date|None=None
    exit_tolerance_semantics:Literal['PENDING']|None=None

    @model_validator(mode='after')
    def valid_dates(self):
        if self.valid_to and self.valid_to<self.valid_from:
            raise ValueError('Invalid validity')
        return self


def parameters_output(row):
    keys=ParametersInput.model_fields
    if row is None:
        return {**{key:None for key in keys},'configured':False,'unit':'seconds','updated_at':None}
    return {**{key:getattr(row,key) for key in keys},'configured':all(getattr(row,key) is not None for key in keys),
        'unit':'seconds','updated_at':row.updated_at,'id':row.id,'valid_from':row.valid_from,'valid_to':row.valid_to,
        'exit_tolerance_semantics':row.exit_tolerance_semantics}


def add_revision(db,payload,actor_id):
    db.execute(text('SELECT pg_advisory_xact_lock(73611003)'))
    if db.scalar(select(Parameters.id).where(Parameters.valid_from<=(payload.valid_to or date.max),
            or_(Parameters.valid_to.is_(None),Parameters.valid_to>=payload.valid_from)).limit(1)):
        raise problem('parameters_overlap','No se sobrescriben parámetros vigentes/históricos; use vigencias no superpuestas.',409)
    row=Parameters(**payload.model_dump(),updated_by_user_id_external=actor_id)
    db.add(row);db.commit()
    return row
