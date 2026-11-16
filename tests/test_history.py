import uuid
from datetime import date,timedelta,time
from sqlalchemy import select,func
from app.attendance.models import Parameters,Policy
from app.attendance.evaluation_models import EvaluationParameters
from app.attendance.service import AttendanceService
from app.biometric.models import Record,Coverage,EmployeeLink
from app.biometric.storage import ensure_device,persist_records
from app.shifts.models import Shift,Segment,Assignment,CalendarDay
from app.shifts.repository import ShiftRepository
from tests.conftest import EMPLOYEE,ACTOR,CONTRACT,FakeCorrespondencia


def parameters(start,end,tolerance):
    return Parameters(valid_from=start,valid_to=end,morning_entry_tolerance=tolerance,
        morning_exit_tolerance=0,afternoon_entry_tolerance=0,afternoon_exit_tolerance=0,
        minimum_return_interval=0,duplicate_window=60)


def seed(db):
    ensure_device(db,'test-clock')
    db.add(EmployeeLink(device_id='test-clock',biometric_user_id='007',funcionario_id_external=EMPLOYEE))
    db.add(Policy(contract_type_id_external=CONTRACT,name='Confirmed fixture scope',valid_from=date(2026,1,1),
        cutoff_day=31,period_semantics_confirmed=True,population_confirmed=True,
        employee_ids_external=[str(EMPLOYEE)],confirmation_reference='fixture-only'))
    db.add_all([parameters(date(2026,7,1),date(2026,7,15),300),parameters(date(2026,7,16),date(2026,7,31),600)])
    for first,last,hour in [(1,15,8),(16,31,9)]:
        start,end=date(2026,7,first),date(2026,7,last)
        shift=Shift(name=f'Fixture {hour}',valid_from=start,valid_to=end,windows={
            'morning_entry':[f'{hour-1:02}:00:00',f'{hour+1:02}:00:00'],
            'morning_exit':[f'{hour+3:02}:00:00',f'{hour+4:02}:30:00'],
            'afternoon_entry':['13:31:00','15:30:00'],'final_exit':['17:00:00','19:00:00']})
        db.add(shift);db.flush()
        db.add_all([Segment(shift_id=shift.id,sequence=1,start_time=time(hour),end_time=time(hour+4),
            entry_kind='morning_entry',exit_kind='morning_exit',valid_from=start,valid_to=end),
            Segment(shift_id=shift.id,sequence=2,start_time=time(14),end_time=time(18),entry_kind='afternoon_entry',
                exit_kind='final_exit',valid_from=start,valid_to=end),
            Assignment(funcionario_id_external=EMPLOYEE,shift_id=shift.id,valid_from=start,valid_to=end)])
    for day in range(1,32):
        db.add(CalendarDay(funcionario_id_external=EMPLOYEE,date=date(2026,7,day),is_working=day in {1,16}))
        db.add(Coverage(device_id='test-clock',date=date(2026,7,day),status='COMPLETE',records_count=0,attempt_count=1))
    db.flush()
    for day,hour in [(1,8),(16,9)]:
        values=[f'{hour:02}:10:00',f'{hour+4:02}:00:00','14:00:00','18:00:00']
        persist_records(db,'test-clock',[dict(user_id='007',timestamp=f'2026-07-{day:02}T{value}',status=89,punch=255,uid=index)
            for index,value in enumerate(values)])
    db.commit()


def test_historical_shifts_and_parameters_do_not_use_current_config(db):
    seed(db)
    service=AttendanceService(db,FakeCorrespondencia(),today_fn=lambda:date(2026,8,2))
    report=service.report(EMPLOYEE,CONTRACT,2026,7)
    assert report.ready
    assert report.days[0].late_seconds==300
    assert report.days[15].late_seconds==0
    assert report.days[0].parameters_revision_id!=report.days[15].parameters_revision_id
    assert report.days[0].schedule.events[0].expected_at.hour==8
    assert report.days[15].schedule.events[0].expected_at.hour==9
    assert report.parameters is None # multiple historical revisions
    before=report.model_dump(mode='json')
    db.add(parameters(date(2026,8,1),None,999));db.commit()
    assert service.report(EMPLOYEE,CONTRACT,2026,7).model_dump(mode='json')==before
    assert report.days[0].original_records[0].punch==255
    assert service.generate(CONTRACT,2026,7).employees[0].summary==report.summary


def test_missing_dependencies_remain_false_and_no_remote_biometric(db):
    service=AttendanceService(db,FakeCorrespondencia())
    report=service.report(EMPLOYEE,CONTRACT,2026,7)
    assert not report.ready and report.days==()
    assert 'employee_contract_assignment' in report.pending_configuration


def test_missing_calendar_and_parameters(db):
    seed(db)
    row=db.scalar(select(CalendarDay).where(CalendarDay.date==date(2026,7,1)))
    db.delete(row) # disposable fixture only
    db.commit()
    report=AttendanceService(db,FakeCorrespondencia()).report(EMPLOYEE,CONTRACT,2026,7)
    assert not report.ready and 'confirmed_work_calendar' in report.pending_configuration


def test_assignment_api_overlap_and_external_only(client,db):
    payload={'name':'Fixture','valid_from':'2026-07-01','valid_to':'2026-07-31',
        'windows':{'morning_entry':['07:00','09:00'],'final_exit':['11:00','13:00']},
        'segments':[{'sequence':1,'start_time':'08:00','end_time':'12:00','entry_kind':'morning_entry',
            'exit_kind':'final_exit','valid_from':'2026-07-01','valid_to':'2026-07-31'}]}
    shift=client.post('/rrhh/work-shifts',json=payload)
    assert shift.status_code==201,shift.text
    assignment={'funcionario_id_external':str(EMPLOYEE),'shift_id':shift.json()['id'],
        'valid_from':'2026-07-01','valid_to':'2026-07-31'}
    assert client.post('/rrhh/employee-shift-assignments',json=assignment).status_code==201
    assert client.post('/rrhh/employee-shift-assignments',json=assignment).status_code==409


def test_parameters_endpoint_requires_validity_and_refuses_overwrite(client):
    values=dict(morning_entry_tolerance=300,morning_exit_tolerance=0,afternoon_entry_tolerance=300,
        afternoon_exit_tolerance=0,minimum_return_interval=600,duplicate_window=60)
    assert client.put('/rrhh/attendance/parameters',json=values).status_code==422
    values.update(valid_from='2026-07-01',valid_to='2026-07-31')
    assert client.put('/rrhh/attendance/parameters',json=values).status_code==200
    assert client.put('/rrhh/attendance/parameters',json=values).status_code==409
    result=client.get('/rrhh/attendance/parameters?on_date=2026-07-05').json()
    assert result['morning_entry_tolerance']==300 and result['unit']=='seconds'


def test_report_endpoint_raw_immutable(client,db):
    seed(db)
    before=db.execute(select(Record.record_id,Record.timestamp,Record.status_raw,Record.punch_raw).order_by(Record.record_id)).all()
    response=client.get(f'/rrhh/attendance/employees/{EMPLOYEE}/report',params={'contract_type_id':str(CONTRACT),'year':2026,'month':7})
    assert response.status_code==200,response.text
    assert response.json()['summary']['total_records']==8
    assert before==db.execute(select(Record.record_id,Record.timestamp,Record.status_raw,Record.punch_raw).order_by(Record.record_id)).all()


def test_scoped_report_uses_only_persisted_marks_when_biometric_unavailable(client,db,monkeypatch):
    import httpx
    seed(db)
    original=httpx.Client.request
    def unavailable(instance,*args,**kwargs):
        if instance is client: return original(instance,*args,**kwargs)
        raise AssertionError('Report must not make external HTTP calls with fixture masters')
    monkeypatch.setattr(httpx.Client,'request',unavailable)
    response=client.get(f'/rrhh/attendance/employees/{EMPLOYEE}/report',params={
        'contract_type_id':str(CONTRACT),'year':2026,'month':7,'start_date':'2026-07-01','end_date':'2026-07-01'})
    assert response.status_code==200,response.text
    value=response.json()
    assert value['ready'] and len(value['days'])==1 and value['summary']['total_records']==4
    assert value['days'][0]['original_records'][0]['punch']==255


def test_report_range_must_be_complete_and_inside_confirmed_period(client,db):
    seed(db)
    base={'contract_type_id':str(CONTRACT),'year':2026,'month':7}
    for dates in [{'start_date':'2026-07-01'},
                  {'start_date':'2026-06-30','end_date':'2026-07-01'},
                  {'start_date':'2026-07-02','end_date':'2026-07-01'}]:
        response=client.get(f'/rrhh/attendance/employees/{EMPLOYEE}/report',params={**base,**dates})
        assert response.status_code==422
