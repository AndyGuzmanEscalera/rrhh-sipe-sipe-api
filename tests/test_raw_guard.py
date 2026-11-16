import pytest
from sqlalchemy import select,update,delete
from sqlalchemy.exc import IntegrityError
from app.biometric.models import Record
from app.biometric.storage import ensure_device,persist_records
from tests.test_sync import record


@pytest.mark.parametrize('operation',['update','delete'])
def test_raw_evidence_guard_at_database_level(db,operation):
    ensure_device(db,'test-clock');persist_records(db,'test-clock',[record()]);db.commit()
    statement=update(Record).values(punch_raw=1) if operation=='update' else delete(Record)
    with pytest.raises(IntegrityError):
        db.execute(statement);db.commit()
    db.rollback()
    assert db.scalar(select(Record)).punch_raw==255
