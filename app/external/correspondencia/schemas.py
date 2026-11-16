import uuid
from pydantic import BaseModel, ConfigDict


class Actor(BaseModel):
    id: uuid.UUID
    username: str = ''
    is_active: bool
    roles: list[str] = []
    permissions: list[str] = []
    email: str | None = None
    employee_id: uuid.UUID | None = None
    employee: dict | None = None
    model_config = ConfigDict(extra='ignore')


class Employee(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    document_number: str | None = None
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    position_id: uuid.UUID | None = None
    position_name: str | None = None
    is_active: bool = True
    model_config = ConfigDict(extra='ignore')
