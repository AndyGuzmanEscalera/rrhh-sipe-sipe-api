import uuid
from datetime import date, time
from pydantic import BaseModel, Field, model_validator
from app.attendance.evaluation_models import EventKind


class SegmentInput(BaseModel):
    sequence: int = Field(gt=0)
    start_time: time
    end_time: time
    ends_next_day: bool = False
    entry_kind: EventKind
    exit_kind: EventKind
    valid_from: date
    valid_to: date | None = None


class ShiftInput(BaseModel):
    name: str = Field(min_length=1,max_length=200)
    description: str | None = None
    valid_from: date
    valid_to: date | None = None
    windows: dict[EventKind,tuple[time,time]]
    segments: list[SegmentInput] = Field(min_length=1,max_length=2)

    @model_validator(mode='after')
    def coherent(self):
        if self.valid_to and self.valid_to < self.valid_from:
            raise ValueError('Invalid shift validity')
        if len({s.sequence for s in self.segments}) != len(self.segments):
            raise ValueError('Repeated segment sequence')
        for s in self.segments:
            if s.valid_from < self.valid_from or (s.valid_to and s.valid_to<s.valid_from):
                raise ValueError('Invalid segment validity')
            if self.valid_to and (s.valid_to is None or s.valid_to>self.valid_to):
                raise ValueError('Segment exceeds shift validity')
            if not s.entry_kind.is_entry or s.exit_kind.is_entry:
                raise ValueError('Explicit entry/exit event roles required')
            for key,expected in ((s.entry_kind,s.start_time),(s.exit_kind,s.end_time)):
                if key not in self.windows or not self.windows[key][0]<=expected<=self.windows[key][1]:
                    raise ValueError('Window must include expected time')
        return self


class AssignmentInput(BaseModel):
    funcionario_id_external: uuid.UUID
    shift_id: uuid.UUID
    valid_from: date
    valid_to: date | None = None

    @model_validator(mode='after')
    def coherent(self):
        if self.valid_to and self.valid_to<self.valid_from:
            raise ValueError('Invalid validity')
        return self


class CalendarInput(BaseModel):
    funcionario_id_external: uuid.UUID
    date: date
    is_working: bool
