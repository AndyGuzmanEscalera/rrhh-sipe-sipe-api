import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Origin = Literal['SOURCE_API', 'SOURCE_FILE']


class FileImport(BaseModel):
    device_id: str = Field(min_length=1, max_length=100, pattern=r'^[^\x00]+$')
    records: list[Any] = Field(max_length=10000)
    source_file_name: str | None = Field(default=None, max_length=255, pattern=r'^[^\x00]*$')
    received_records: int | None = Field(default=None, ge=1, le=1000000)
    error_records: int = Field(default=0, ge=0, le=1000000)

    @model_validator(mode='after')
    def check_counts(self):
        total = len(self.records) + self.error_records
        if not 1 <= total <= 1000000 or self.received_records not in (None, total):
            raise ValueError('Recibidos debe coincidir con las filas enviadas más los errores de lectura.')
        return self


class ImportSummary(BaseModel):
    batch_id: uuid.UUID
    device_id: str
    source: Origin
    origin: Origin
    source_file_name: str | None
    received_records: int
    new_records: int
    duplicate_records: int
    error_records: int
    status: Literal['completed', 'failed']
    error_message: str | None


class Batch(BaseModel):
    id: uuid.UUID
    device_id: str
    origin: Origin
    source_file_name: str | None
    requested_start_date: date | None
    requested_end_date: date | None
    status: Literal['queued', 'running', 'completed', 'failed']
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    received_records: int
    downloaded_records: int
    new_records: int
    duplicate_records: int
    duplicates_ignored: int
    error_records: int
    executed_by: uuid.UUID | None
    error_code: str | None
    error_message: str | None
    planned_ranges: list[dict[str, str]]
    force: bool
    full_history_download: bool


class BatchHistory(BaseModel):
    items: list[Batch]
    total: int
    limit: int
    offset: int


class RawRecord(BaseModel):
    record_id: str
    device_id: str
    user_id: str
    funcionario_id: uuid.UUID | None
    linked: bool
    timestamp: datetime
    date: date
    time: time
    status: int | None
    punch: int | None
    uid: int | None
    source: Origin
    source_file_name: str | None
    import_batch_id: uuid.UUID | None
    imported_at: datetime
    raw_payload: dict[str, Any] | None


class RawRecords(BaseModel):
    records: list[RawRecord]
    total: int
    limit: int
    offset: int
