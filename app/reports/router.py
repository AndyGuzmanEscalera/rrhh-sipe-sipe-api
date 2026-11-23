import uuid
from datetime import date
from fastapi import APIRouter,Depends,Query
from app.core.security import require_permission,make_client
from app.db.session import get_db
from app.core.errors import problem
from app.reports.service import ReportService
from app.attendance.evaluation_models import IndividualReport

router=APIRouter(tags=['reports'])


@router.get('/rrhh/attendance/employees/{employee_id}/report',response_model=IndividualReport)
def report(employee_id: uuid.UUID, contract_type_id: uuid.UUID | None = None,
           year: int | None = Query(default=None, ge=1901, le=9998),
           month: int | None = Query(default=None, ge=1, le=12),
           start_date: date | None = None, end_date: date | None = None,
           date_from: date | None = None, date_to: date | None = None,
           morning_start: str = '08:00', morning_end: str = '12:00', afternoon_start: str = '14:00', afternoon_end: str = '18:00',
           morning_start_tolerance: int = Query(default=5, ge=0), morning_end_tolerance: int = Query(default=5, ge=0),
           afternoon_start_tolerance: int = Query(default=5, ge=0), afternoon_end_tolerance: int = Query(default=5, ge=0),
           db=Depends(get_db), actor=Depends(require_permission('rrhh.read')), client=Depends(make_client)):
    service = ReportService(db, client)
    if date_from is not None or date_to is not None:
        if any(value is not None for value in (contract_type_id, year, month, start_date, end_date)):
            raise problem('invalid_report_range', 'Use un único modo de consulta.', 422)
        return service.report_range(employee_id, date_from, date_to, {
            'morning_start': morning_start, 'morning_end': morning_end, 'afternoon_start': afternoon_start, 'afternoon_end': afternoon_end,
            'morning_start_tolerance': morning_start_tolerance, 'morning_end_tolerance': morning_end_tolerance,
            'afternoon_start_tolerance': afternoon_start_tolerance, 'afternoon_end_tolerance': afternoon_end_tolerance})
    if contract_type_id is None or year is None or month is None:
        raise problem('invalid_report_range', 'Indique las dos fechas o el contrato, año y mes.', 422)
    return service.report(employee_id, contract_type_id, year, month, start_date, end_date)
