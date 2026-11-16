import os
import uuid
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

os.environ['BIOMETRIC_DEVICE_ID']='test-clock'
os.environ['BIOMETRIC_API_TOKEN']='test-only-not-production'
os.environ['BIOMETRIC_API_URL']='https://biometric.example.test'
os.environ['CORRESPONDENCIA_API_URL']='https://correspondencia.example.test/api'
os.environ['SCHEDULER_ENABLED']='false'
os.environ['LOCAL_TIMEZONE']='America/La_Paz'
os.environ['DATABASE_URL']=os.environ.get('RRHH_TEST_DATABASE_URL','')

from app.main import app
from app.db.base import Base
from app.db import models
from app.db.session import get_db
from app.core.security import get_actor,make_client
from app.external.correspondencia.schemas import Actor,Employee

EMPLOYEE=uuid.UUID('10000000-0000-4000-8000-000000000001')
ACTOR=uuid.UUID('90000000-0000-4000-8000-000000000001')
CONTRACT=uuid.UUID('20000000-0000-4000-8000-000000000001')


class FakeCorrespondencia:
    def get_employee(self,employee_id):
        return Employee(id=employee_id,first_name='Synthetic',last_name='Employee',document_number='007',unit_name='Test area',position_name='Test role')


@pytest.fixture(scope='session')
def engine():
    url=os.environ.get('RRHH_TEST_DATABASE_URL','')
    parsed=make_url(url)
    if not parsed.database.startswith('rrhh_api_test_') or parsed.host not in {'127.0.0.1','localhost'}:
        raise RuntimeError('Explicit disposable loopback RRHH_TEST_DATABASE_URL required')
    engine=create_engine(url,hide_parameters=True)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine):
    return sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)


@pytest.fixture
def db(engine,factory):
    with engine.begin() as connection:
        connection.execute(text('TRUNCATE '+','.join('"'+t+'"' for t in Base.metadata.tables)+' CASCADE'))
    with factory() as db:
        yield db


@pytest.fixture
def client(db):
    def session(): yield db
    app.dependency_overrides[get_db]=session
    app.dependency_overrides[make_client]=lambda:FakeCorrespondencia()
    app.dependency_overrides[get_actor]=lambda:Actor(id=ACTOR,username='fixture',is_active=True,roles=['MANAGER'],
        permissions=['rrhh.read','rrhh.manage','rrhh.resync'])
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
