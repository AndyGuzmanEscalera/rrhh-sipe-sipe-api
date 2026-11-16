import uuid
from datetime import date
from fastapi import APIRouter,Depends,Query
from app.core.security import require_permission,make_client
from app.db.session import get_db
from app.reports.service import ReportService
from app.attendance.evaluation_models import IndividualReport

router=APIRouter(tags=['reports'])


@router.get('/rrhh/attendance/employees/{employee_id}/report',response_model=IndividualReport)
def report(employee_id:uuid.UUID,contract_type_id:uuid.UUID,year:int=Query(ge=1901,le=9998),
           month:int=Query(ge=1,le=12),start_date:date|None=None,end_date:date|None=None,
           db=Depends(get_db),actor=Depends(require_permission('rrhh.read')),client=Depends(make_client)):
    return ReportService(db,client).report(employee_id,contract_type_id,year,month,start_date,end_date)
