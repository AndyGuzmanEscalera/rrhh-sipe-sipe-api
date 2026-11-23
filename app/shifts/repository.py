from datetime import datetime
from sqlalchemy import select, or_
from app.shifts.models import Shift,Segment,Assignment,CalendarDay
from app.attendance.evaluation_models import DaySchedule,ExpectedEvent,EventKind


def active_on(row, day):
    return row.valid_from<=day and (row.valid_to is None or day<=row.valid_to)


class ShiftRepository:
    def __init__(self,db):
        self.db=db

    def schedule(self, employee_id, day):
        calendar=self.db.scalar(select(CalendarDay).where(CalendarDay.funcionario_id_external==employee_id,
            CalendarDay.date==day))
        if calendar is None:
            return None,('confirmed_work_calendar',)
        if not calendar.is_working:
            return DaySchedule(day=day,shift_id='nonworking',name='Día no laborable confirmado'),()
        assignments=list(self.db.scalars(select(Assignment).where(Assignment.funcionario_id_external==employee_id,
            Assignment.valid_from<=day,or_(Assignment.valid_to.is_(None),Assignment.valid_to>=day))))
        if len(assignments)!=1:
            return None,('employee_shift_source',)
        shift=self.db.get(Shift,assignments[0].shift_id)
        if shift is None or not active_on(shift,day):
            return None,('employee_shift_source',)
        # is_active is a catalog flag, not a retroactive historical cancellation.
        segments = tuple(self.db.scalars(select(Segment).where(Segment.shift_id==shift.id).order_by(Segment.sequence)))
        return self._schedule_for(day, shift, segments)

    def report_schedules(self, employee_id, dates):
        start, end = dates[0], dates[-1]
        calendar = {row.date: row for row in self.db.scalars(select(CalendarDay).where(
            CalendarDay.funcionario_id_external == employee_id, CalendarDay.date.between(start, end)))}
        assignments = tuple(self.db.scalars(select(Assignment).where(
            Assignment.funcionario_id_external == employee_id, Assignment.valid_from <= end,
            or_(Assignment.valid_to.is_(None), Assignment.valid_to >= start))))
        ids = {assignment.shift_id for assignment in assignments}
        shifts = {row.id: row for row in self.db.scalars(select(Shift).where(Shift.id.in_(ids)))}
        segments_by_shift = {}
        for segment in self.db.scalars(select(Segment).where(Segment.shift_id.in_(ids)).order_by(Segment.sequence)):
            segments_by_shift.setdefault(segment.shift_id, []).append(segment)
        result = {}
        for day in dates:
            entry = calendar.get(day)
            active = [row for row in assignments if active_on(row, day)]
            shift = shifts.get(active[0].shift_id) if len(active) == 1 else None
            if entry is None:
                result[day] = None, ('confirmed_work_calendar',)
            elif not entry.is_working:
                result[day] = DaySchedule(day=day, shift_id='nonworking', name='Día no laborable confirmado'), ()
            elif shift is None or not active_on(shift, day):
                result[day] = None, ('employee_shift_source',)
            else:
                result[day] = self._schedule_for(day, shift, segments_by_shift.get(shift.id, ()))
        return result, tuple(dict.fromkeys(shift.name for shift in shifts.values()
                                          if shift.valid_from <= end and (shift.valid_to is None or shift.valid_to >= start)))

    @staticmethod
    def _schedule_for(day, shift, configured_segments):
        segments = [segment for segment in configured_segments if active_on(segment, day)]
        if not segments or any(s.ends_next_day for s in segments):
            return None,('overnight_shift_policy',) if segments else ('employee_shift_source',)
        try:
            events=[]
            for segment in segments:
                for key,expected in ((segment.entry_kind,segment.start_time),(segment.exit_kind,segment.end_time)):
                    lower,upper=shift.windows[key]
                    from datetime import time
                    lower=time.fromisoformat(lower) if isinstance(lower,str) else lower
                    upper=time.fromisoformat(upper) if isinstance(upper,str) else upper
                    events.append(ExpectedEvent(kind=EventKind(key),expected_at=datetime.combine(day,expected),
                        window_start=datetime.combine(day,lower),window_end=datetime.combine(day,upper)))
            return DaySchedule(day=day,shift_id=str(shift.id),name=shift.name,events=tuple(events)),()
        except (ValueError,KeyError):
            return None,('association_windows',)
