"""Vinculación por CI vía API Correspondencia: match, idempotencia y ambiguas."""
import importlib.util
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.biometric.models import Device, EmployeeLink, Record
from app.biometric.storage import ensure_device
from app.modules.rrhh.document_links import link_employees_by_document


def _load_cli_module():
    path = Path(__file__).resolve().parents[1] / 'app/scripts/link_biometric_employees_by_document.py'
    spec = importlib.util.spec_from_file_location('link_biometric_employees_by_document', path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _employee(document, employee_id=None, **extra):
    return SimpleNamespace(
        id=employee_id or uuid.uuid4(),
        first_name='Test',
        last_name='Only',
        document_number=document,
        is_active=True,
        **extra,
    )


class FakeCorrespondencia:
    def __init__(self, employees):
        self.employees = list(employees)

    def list_employees(self, **filters):
        return self.employees, 1, 1


def _record(db, biometric_user_id, device_id='test-clock', stamp='2026-11-11T08:00:00'):
    ensure_device(db, device_id)
    moment = datetime.fromisoformat(stamp)
    db.add(Record(
        record_id=uuid.uuid4().hex,
        device_id=device_id,
        biometric_user_id=biometric_user_id,
        timestamp=moment,
        date=moment.date(),
        time=moment.time().replace(microsecond=0),
        source='SOURCE_API',
    ))


def test_cli_help():
    module = _load_cli_module()
    with pytest.raises(SystemExit) as exit_info:
        module.main(['--help'])
    assert exit_info.value.code == 0


def test_link_by_exact_document_number(db):
    assert hasattr(Record, 'biometric_user_id')
    assert not hasattr(Record, 'user_id')

    employee = _employee('12970173')
    _record(db, '12970173')
    _record(db, '99999999')
    db.commit()

    before = db.scalar(select(func.count()).select_from(Record))
    result = link_employees_by_document(db, FakeCorrespondencia([employee]))
    db.commit()

    assert result.biometric_ids_found == 2
    assert result.matches_by_document == 1
    assert result.links_created == 1
    assert result.links_already_existing == 0
    assert result.unmatched == 1
    assert result.unmatched_ids == ('99999999',)
    assert result.ambiguous_documents == 0

    link = db.get(EmployeeLink, ('test-clock', '12970173'))
    assert link is not None and link.funcionario_id_external == employee.id
    assert link.biometric_user_id == '12970173'
    assert db.scalar(select(func.count()).select_from(Record)) == before
    raw = db.scalar(select(Record).where(Record.biometric_user_id == '12970173'))
    assert raw.funcionario_id_external is None


def test_link_by_document_is_idempotent(db):
    employee = _employee('3818357')
    _record(db, '3818357')
    db.commit()
    client = FakeCorrespondencia([employee])

    first = link_employees_by_document(db, client)
    db.commit()
    second = link_employees_by_document(db, client)
    db.commit()

    assert first.links_created == 1
    assert second.links_created == 0
    assert second.links_already_existing == 1
    assert second.matches_by_document == 1
    assert db.scalar(select(func.count()).select_from(EmployeeLink)) == 1


def test_ambiguous_document_is_not_linked(db):
    first = _employee('007')
    second = _employee('007')
    _record(db, '007')
    db.commit()

    result = link_employees_by_document(db, FakeCorrespondencia([first, second]))
    db.commit()

    assert result.links_created == 0
    assert result.unmatched == 0
    assert result.ambiguous_documents == 1
    assert result.ambiguous_ids == ('007',)
    assert db.scalar(select(func.count()).select_from(EmployeeLink)) == 0
    assert db.get(Device, 'test-clock') is not None
