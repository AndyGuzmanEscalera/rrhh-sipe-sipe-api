from fastapi.testclient import TestClient
from sqlalchemy import inspect
from app.main import app
from app.db.base import Base


def test_startup_openapi_health_without_external_calls():
    with TestClient(app) as client:
        assert client.get('/health').json()=={'status':'ok','service':'rrhh-sipe-sipe-api'}
        response=client.get('/openapi.json')
        assert response.status_code==200
        assert '/rrhh/attendance/generate' in response.json()['paths']
        assert client.get('/rrhh/attendance/contracts').status_code==401


def test_only_owned_tables_and_fks(engine):
    forbidden={'employees','users','organizational_units','positions','auth_sessions'}
    assert not forbidden.intersection(Base.metadata.tables)
    schema=inspect(engine)
    assert schema.get_table_names() and 'alembic_version' in schema.get_table_names()
    for table in Base.metadata.tables:
        for fk in schema.get_foreign_keys(table):
            assert fk['referred_table'] in Base.metadata.tables
