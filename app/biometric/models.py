import uuid
from datetime import date as DateType, datetime, time as TimeType
from sqlalchemy import String, Integer, Boolean, DateTime, Date, Time, ForeignKey, Index, CheckConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class Device(Base):
    __tablename__ = 'biometric_devices'
    device_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    remote_device_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    serial: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    details: Mapped[dict] = mapped_column('metadata', JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class EmployeeLink(Base):
    __tablename__ = 'biometric_employee_links'
    device_id: Mapped[str] = mapped_column(ForeignKey('biometric_devices.device_id'), primary_key=True)
    biometric_user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    funcionario_id_external: Mapped[uuid.UUID] = mapped_column(index=True)
    linked_by_user_id_external: Mapped[uuid.UUID | None]
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class LinkAudit(Base):
    __tablename__ = 'biometric_link_audits'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(ForeignKey('biometric_devices.device_id'))
    biometric_user_id: Mapped[str] = mapped_column(String(100))
    previous_employee_id: Mapped[uuid.UUID | None]
    new_employee_id: Mapped[uuid.UUID]
    actor_id_external: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Record(Base):
    __tablename__ = 'biometric_records'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    record_id: Mapped[str] = mapped_column(String(64), unique=True)
    device_id: Mapped[str] = mapped_column(ForeignKey('biometric_devices.device_id'))
    biometric_user_id: Mapped[str] = mapped_column(String(100))
    funcionario_id_external: Mapped[uuid.UUID | None] = mapped_column(index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    date: Mapped[DateType] = mapped_column(Date)
    time: Mapped[TimeType] = mapped_column(Time)
    status_raw: Mapped[int | None] = mapped_column(Integer)
    punch_raw: Mapped[int | None] = mapped_column(Integer)
    uid_raw: Mapped[int | None] = mapped_column(Integer)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source: Mapped[str] = mapped_column(String(20))
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey('biometric_syncs.id', name='fk_records_import_batch'))
    raw_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("source IN ('SOURCE_API','SOURCE_FILE')"),
        CheckConstraint('date = timestamp::date AND time = timestamp::time'),
        Index('ix_records_device_date', 'device_id', 'date'),
        Index('ix_records_employee_timestamp', 'funcionario_id_external', 'timestamp'),
        Index('ix_records_import_batch', 'import_batch_id'),
    )


class Sync(Base):
    __tablename__ = 'biometric_syncs'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(ForeignKey('biometric_devices.device_id'))
    requested_from: Mapped[DateType | None] = mapped_column(Date)
    requested_to: Mapped[DateType | None] = mapped_column(Date)
    origin: Mapped[str] = mapped_column(String(20), default='SOURCE_API', server_default='SOURCE_API')
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    force: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    planned_ranges: Mapped[list] = mapped_column(JSONB, default=list)
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_duplicated: Mapped[int] = mapped_column(Integer, default=0)
    error_records: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    executed_by_user_id_external: Mapped[uuid.UUID | None]
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint('requested_from <= requested_to'),
        CheckConstraint("status IN ('queued','running','completed','failed')"),
        CheckConstraint("origin IN ('SOURCE_API','SOURCE_FILE')", name='ck_sync_origin'),
        CheckConstraint("origin <> 'SOURCE_API' OR (requested_from IS NOT NULL AND requested_to IS NOT NULL)",
                        name='ck_sync_api_range'),
        CheckConstraint('error_records >= 0', name='ck_sync_error_records'),
        Index('uq_sync_active', 'device_id', unique=True,
              postgresql_where=text("origin = 'SOURCE_API' AND status IN ('queued','running')")),
    )


class Coverage(Base):
    __tablename__ = 'biometric_sync_coverage'
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(ForeignKey('biometric_devices.device_id'))
    date: Mapped[DateType] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default='OPEN')
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    last_successful_sync_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('biometric_syncs.id'))
    last_attempt_sync_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('biometric_syncs.id'))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (Index('uq_coverage_device_date','device_id','date',unique=True),
        CheckConstraint("status IN ('OPEN','COMPLETE','FAILED')"),)
