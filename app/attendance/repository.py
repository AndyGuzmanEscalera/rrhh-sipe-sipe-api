from datetime import date,datetime,time
from sqlalchemy import select,or_,tuple_
from pydantic import ValidationError
from app.attendance.models import Parameters,Policy
from app.attendance.evaluation_models import EmployeeHeader,EvaluationParameters,RawMark
from app.biometric.models import Record,EmployeeLink,Coverage


def applicable(model,day):
    return model.valid_from<=day,or_(model.valid_to.is_(None),model.valid_to>=day)


class AttendanceRepository:
    def __init__(self,db):
        self.db=db

    def parameters_on(self,day):
        rows=list(self.db.scalars(select(Parameters).where(*applicable(Parameters,day))))
        row=rows[0] if len(rows)==1 else None
        if row is None:
            return None,None
        try:
            return EvaluationParameters(**{key:getattr(row,key) for key in EvaluationParameters.model_fields}),row
        except ValidationError:
            return None,row

    def report_parameters(self, dates):
        rows = tuple(self.db.scalars(select(Parameters).where(
            Parameters.valid_from <= dates[-1],
            or_(Parameters.valid_to.is_(None), Parameters.valid_to >= dates[0]))))
        result = {}
        for day in dates:
            candidates = [row for row in rows if row.valid_from <= day and (row.valid_to is None or day <= row.valid_to)]
            if len(candidates) != 1:
                continue
            row = candidates[0]
            try:
                parameters = EvaluationParameters(**{key: getattr(row, key) for key in EvaluationParameters.model_fields})
            except ValidationError:
                continue
            result[day] = parameters, row.id
        return result

    def report_coverage(self, employee_id, start, end):
        devices = set(self.db.scalars(select(EmployeeLink.device_id).where(
            EmployeeLink.funcionario_id_external == employee_id, EmployeeLink.is_active.is_(True))))
        if not devices:
            return devices, {}
        complete = {}
        for device, day in self.db.execute(select(Coverage.device_id, Coverage.date).where(
                Coverage.device_id.in_(devices), Coverage.date.between(start, end), Coverage.status == 'COMPLETE')):
            complete.setdefault(day, set()).add(device)
        return devices, complete

    def policy(self,contract_id,day):
        rows=list(self.db.scalars(select(Policy).where(Policy.contract_type_id_external==contract_id,*applicable(Policy,day))))
        return rows[0] if len(rows)==1 else None

    def records(self,employee_id,period):
        # Include unenriched RAW matched through biometric_employee_links without
        # rewriting biometric_records.funcionario_id_external.
        identity=[Record.funcionario_id_external==employee_id]
        links=list(self.db.execute(select(EmployeeLink.device_id,EmployeeLink.biometric_user_id).where(
            EmployeeLink.funcionario_id_external==employee_id,EmployeeLink.is_active.is_(True))))
        if links:
            identity.append(tuple_(Record.device_id,Record.biometric_user_id).in_(links))
        rows=self.db.scalars(select(Record).where(or_(*identity),
            Record.timestamp>=datetime.combine(period.start_date,time.min),
            Record.timestamp<datetime.combine(period.end_exclusive,time.min)).order_by(Record.timestamp,Record.record_id))
        return tuple(RawMark(record_id=r.record_id,device_id=r.device_id,user_id=r.biometric_user_id,
            # DTO only: resolve via link without rewriting RAW funcionario_id_external.
            funcionario_id=r.funcionario_id_external or employee_id,timestamp=r.timestamp,status=r.status_raw,punch=r.punch_raw,
            uid=r.uid_raw,source=r.source,source_file_name=r.source_file_name,import_batch_id=r.import_batch_id) for r in rows)

    def coverage(self,employee_id,day,today):
        devices=set(self.db.scalars(select(EmployeeLink.device_id).where(
            EmployeeLink.funcionario_id_external==employee_id,EmployeeLink.is_active.is_(True))))
        if not devices:
            return None,('biometric_employee_link',)
        complete=set(self.db.scalars(select(Coverage.device_id).where(Coverage.device_id.in_(devices),
            Coverage.date==day,Coverage.status=='COMPLETE')))
        return (True,()) if day<today and devices<=complete else (None,('biometric_coverage',))


def employee_header(employee):
    return EmployeeHeader(id=employee.id,full_name=f'{employee.first_name} {employee.last_name}',
        document_number=employee.document_number,unit_id=employee.unit_id,unit_name=employee.unit_name,
        position_id=employee.position_id,position_name=employee.position_name)
