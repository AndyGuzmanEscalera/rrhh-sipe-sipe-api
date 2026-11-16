import uuid
from datetime import date, datetime
from sqlalchemy import String, Date, DateTime, Integer, Boolean, CheckConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class Parameters(Base):
    __tablename__ = 'attendance_parameters'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    morning_entry_tolerance: Mapped[int | None] = mapped_column(Integer)
    morning_exit_tolerance: Mapped[int | None] = mapped_column(Integer)
    afternoon_entry_tolerance: Mapped[int | None] = mapped_column(Integer)
    afternoon_exit_tolerance: Mapped[int | None] = mapped_column(Integer)
    minimum_return_interval: Mapped[int | None] = mapped_column(Integer)
    duplicate_window: Mapped[int | None] = mapped_column(Integer)
    exit_tolerance_semantics: Mapped[str | None] = mapped_column(String(50))
    updated_by_user_id_external: Mapped[uuid.UUID | None]
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (CheckConstraint('valid_to IS NULL OR valid_to >= valid_from'),) + tuple(
        CheckConstraint(f'{key} BETWEEN 0 AND 86400') for key in (
            'morning_entry_tolerance','morning_exit_tolerance','afternoon_entry_tolerance',
            'afternoon_exit_tolerance','minimum_return_interval','duplicate_window'))


class Policy(Base):
    """RRHH evaluation scope only; not a personnel/contract master."""
    __tablename__ = 'attendance_policies'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    contract_type_id_external: Mapped[uuid.UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(200))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    cutoff_day: Mapped[int | None] = mapped_column(Integer)
    period_semantics_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    population_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    employee_ids_external: Mapped[list] = mapped_column(JSONB, default=list)
    confirmed_by_user_id_external: Mapped[uuid.UUID | None]
    confirmation_reference: Mapped[str | None] = mapped_column(String(500))
    __table_args__ = (CheckConstraint('cutoff_day BETWEEN 1 AND 31'),
        CheckConstraint('valid_to IS NULL OR valid_to >= valid_from'),)
