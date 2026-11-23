"""Orchestrates the unchanged pure evaluator over local, date-effective inputs."""
import uuid
from datetime import date,datetime,time,timedelta
from app.core.clock import local_today
from app.core.errors import problem
from app.attendance.repository import AttendanceRepository,employee_header
from app.attendance.periods import period_for
from app.attendance.evaluation import evaluate_day,period_summary,calendar_days
from app.attendance.evaluation_models import EvaluationPeriod,IndividualReport,ShiftHeader,GenerationResult,EmployeeEvaluationSummary,DayStatus,EvaluationParameters,DaySchedule,ExpectedEvent,EventKind
from app.shifts.repository import ShiftRepository


class AttendanceService:
    def __init__(self,db,client,today_fn=local_today):
        self.db,self.client,self.today_fn=db,client,today_fn
        self.repository=AttendanceRepository(db)
        self.shifts=ShiftRepository(db)

    def context(self,contract_id,year,month):
        # Policy is effective in the selected nominal month; it must also cover
        # the whole resulting period. A transition within it stays unconfirmed.
        policy=self.repository.policy(contract_id,date(year,month,1))
        pending=[]
        period=None
        if policy is None or not policy.period_semantics_confirmed or policy.cutoff_day is None:
            pending.append('confirmed_contract_period_policy')
        else:
            start,end=period_for(year,month,policy.cutoff_day)
            period=EvaluationPeriod(start_date=start,end_exclusive=end)
            if policy.valid_from>start or (policy.valid_to and policy.valid_to<end-timedelta(days=1)):
                pending.append('confirmed_contract_period_semantics')
        if policy is None or not policy.population_confirmed:
            pending.append('employee_contract_assignment')
        return policy,period,pending

    def report(self,employee_id,contract_id,year,month,start_date=None,end_date=None):
        employee=self.client.get_employee(employee_id)
        header=employee_header(employee)
        policy,period,pending=self.context(contract_id,year,month)
        if start_date is not None or end_date is not None:
            if (start_date is None or end_date is None or period is None or
                    not period.start_date<=start_date<=end_date<period.end_exclusive):
                raise problem('invalid_report_range','El rango debe estar dentro del período confirmado.',422)
            period=EvaluationPeriod(start_date=start_date,end_exclusive=end_date+timedelta(days=1))
        if policy:
            header=header.model_copy(update={'contract_type_id':contract_id,'contract_type_name':policy.name})
        if policy and policy.population_confirmed and str(employee_id) not in policy.employee_ids_external:
            raise problem('employee_contract_mismatch','Funcionario fuera del ámbito confirmado.',422)
        days=[]; revisions=[]
        # Missing contract population/semantics never triggers evaluation on guessed dates.
        if period and not pending:
            marks=self.repository.records(employee_id,period)
            by_day={}
            for mark in marks:
                by_day.setdefault(mark.timestamp.date(),[]).append(mark)
            day=period.start_date
            while day<period.end_exclusive:
                parameters,revision=self.repository.parameters_on(day)
                if revision: revisions.append(revision)
                schedule,issues=self.shifts.schedule(employee_id,day)
                coverage,coverage_issues=self.repository.coverage(employee_id,day,self.today_fn())
                result=evaluate_day(employee_id,day,schedule,parameters,by_day.get(day,()),coverage)
                # Keep all institutionally pending decisions, including exit policy.
                all_issues=tuple(dict.fromkeys((*result.pending_configuration,*issues,*coverage_issues)))
                result=result.model_copy(update={'pending_configuration':all_issues,
                    'parameters_snapshot':parameters,'parameters_revision_id':revision.id if revision else None})
                pending.extend(all_issues)
                days.append(result)
                day+=timedelta(days=1)
        unique_revisions={r.id:r for r in revisions}
        # A single top-level parameter snapshot cannot represent several revisions.
        top_parameters=self.repository.parameters_on(period.start_date)[0] if period and len(unique_revisions)==1 else None
        report=IndividualReport(ready=bool(days) and not pending,employee=header,period=period,
            period_confirmed=period is not None and 'confirmed_contract_period_semantics' not in pending,
            parameters=top_parameters,parameters_updated_at=next(iter(unique_revisions.values())).updated_at if len(unique_revisions)==1 else None,
            shifts=tuple(ShiftHeader(id=key,name=name) for key,name in dict.fromkeys(
                (d.schedule.shift_id,d.schedule.name) for d in days if d.schedule)),
            biometric_coverage='confirmed' if days and all(d.coverage is True for d in days) else 'unknown',
            pending_configuration=tuple(dict.fromkeys(pending)),days=tuple(days),calendar=calendar_days(days),
            summary=period_summary(days) if days else None)
        return report

    def report_range(self, employee_id, start, end, evaluation=None):
        if not start or not end or not start <= end < date.max or (end - start).days > 366:
            raise problem('invalid_report_range', 'Seleccione un rango válido de hasta 367 días.', 422)
        header = employee_header(self.client.get_employee(employee_id))
        period = EvaluationPeriod(start_date=start, end_exclusive=end + timedelta(days=1))
        dates = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        marks = {}
        for record in self.repository.records(employee_id, period):
            marks.setdefault(record.timestamp.date(), []).append(record)
        schedules, configured_shifts = self.shifts.report_schedules(employee_id, dates)
        parameters = self.repository.report_parameters(dates)
        if evaluation:
            try:
                clocks = [time.fromisoformat(evaluation[key]) for key in ('morning_start','morning_end','afternoon_start','afternoon_end')]
                if any(value.second or value.microsecond for value in clocks) or any(a >= b for a,b in zip(clocks, clocks[1:])): raise ValueError
            except (TypeError, ValueError):
                raise problem('invalid_report_schedule', 'La jornada debe usar horas HH:mm en orden válido.', 422)
            values = EvaluationParameters(morning_entry_tolerance=evaluation['morning_start_tolerance'] * 60, morning_exit_tolerance=evaluation['morning_end_tolerance'] * 60, afternoon_entry_tolerance=evaluation['afternoon_start_tolerance'] * 60, afternoon_exit_tolerance=evaluation['afternoon_end_tolerance'] * 60, minimum_return_interval=0, duplicate_window=0)
            kinds = (EventKind.MORNING_ENTRY, EventKind.MORNING_EXIT, EventKind.AFTERNOON_ENTRY, EventKind.FINAL_EXIT)
            for day in dates:
                events = tuple(ExpectedEvent(kind=kind, expected_at=datetime.combine(day, clock), window_start=datetime.combine(day, clock)-timedelta(hours=4), window_end=datetime.combine(day, clock)+timedelta(hours=4)) for kind,clock in zip(kinds,clocks))
                schedules[day] = (DaySchedule(day=day, shift_id='temporary-report', name='Jornada temporal de consulta', events=events), ())
                parameters[day] = (values, None)
        devices, complete = self.repository.report_coverage(employee_id, start, end)
        today = self.today_fn()
        days = []
        pending = {}
        for day in dates:
            schedule, issues = schedules[day]
            values, revision = parameters.get(day, (None, None))
            covered = bool(devices) and day < today and devices <= complete.get(day, set())
            result = evaluate_day(employee_id, day, schedule, values, marks.get(day, ()), covered)
            requirements = () if covered else ('biometric_coverage' if devices else 'biometric_employee_link',)
            all_issues = tuple(dict.fromkeys((*result.pending_configuration, *issues, *requirements)))
            pending.update(dict.fromkeys(all_issues))
            days.append(result.model_copy(update={'pending_configuration': all_issues,
                'parameters_snapshot': values, 'parameters_revision_id': revision}))
        return IndividualReport(ready=not pending, employee=header, period=period, period_confirmed=True,
            configured_shift_names=configured_shifts,
            shifts=tuple(ShiftHeader(id=key, name=name) for key, name in dict.fromkeys(
                (day.schedule.shift_id, day.schedule.name) for day in days if day.schedule and day.schedule.events)),
            biometric_coverage='confirmed' if all(day.coverage is True for day in days) else 'unknown',
            pending_configuration=tuple(pending), days=tuple(days), summary=period_summary(days))

    def generate(self,contract_id,year,month):
        policy,period,pending=self.context(contract_id,year,month)
        results=[]
        if policy and policy.population_confirmed and not pending:
            for employee_id in policy.employee_ids_external:
                report=self.report(uuid.UUID(employee_id),contract_id,year,month)
                status=(DayStatus.NO_DATA if report.summary is None or report.summary.days_evaluated==0 else
                    DayStatus.ANOMALY if report.summary.anomaly_days else
                    DayStatus.OBSERVATION if report.summary.observation_days else DayStatus.NORMAL)
                results.append(EmployeeEvaluationSummary(employee=report.employee,ready=report.ready,
                    summary=report.summary,pending_configuration=report.pending_configuration,shifts=report.shifts,status=status))
                pending.extend(report.pending_configuration)
        return GenerationResult(ready=period is not None and not pending and all(r.ready for r in results),
            contract_type_id=contract_id,year=year,month=month,period=period,
            period_confirmed=period is not None and 'confirmed_contract_period_semantics' not in pending,
            pending_configuration=tuple(dict.fromkeys(pending)),employees=tuple(results),
            total_employees=len(policy.employee_ids_external) if policy and policy.population_confirmed else None,
            message='Evaluación desde PostgreSQL RRHH; revise dependencias y políticas pendientes.')
