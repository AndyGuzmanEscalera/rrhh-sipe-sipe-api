import uuid
from datetime import date, datetime, time
from sqlalchemy import String, Date, DateTime, Time, Boolean, Integer, ForeignKey, CheckConstraint, Index, func
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class Shift(Base):
    __tablename__ = 'work_shifts'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    # Per-event association windows, confirmed by institution. No defaults.
    windows: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_to >= valid_from'),)


class Segment(Base):
    __tablename__ = 'work_shift_segments'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_shifts.id'))
    sequence: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    ends_next_day: Mapped[bool] = mapped_column(Boolean, default=False)
    entry_kind: Mapped[str] = mapped_column(String(30))
    exit_kind: Mapped[str] = mapped_column(String(30))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_to >= valid_from'),
        CheckConstraint('sequence > 0'), Index('uq_segment_revision','shift_id','sequence','valid_from',unique=True))


class Assignment(Base):
    __tablename__ = 'employee_shift_assignments'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    funcionario_id_external: Mapped[uuid.UUID] = mapped_column(index=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('work_shifts.id'))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    assigned_by_user_id_external: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_to >= valid_from'),)


class CalendarDay(Base):
    __tablename__ = 'attendance_calendar_days'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    funcionario_id_external: Mapped[uuid.UUID] = mapped_column(index=True)
    date: Mapped[date] = mapped_column(Date)
    is_working: Mapped[bool] = mapped_column(Boolean)
    confirmed_by_user_id_external: Mapped[uuid.UUID | None]
    __table_args__ = (Index('uq_calendar_employee_day','funcionario_id_external','date',unique=True),)
