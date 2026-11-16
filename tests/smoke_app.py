"""Loopback-only fixture application for local acceptance, never a production entrypoint."""
import os
from sqlalchemy.engine import make_url

url=make_url(os.environ['RRHH_TEST_DATABASE_URL'])
if url.host not in {'127.0.0.1','localhost'} or not url.database.startswith('rrhh_api_test_'):
    raise RuntimeError('Disposable loopback database required')

from tests.conftest import ACTOR,FakeCorrespondencia
from app.main import app
from app.core.security import get_actor,make_client
from app.external.correspondencia.schemas import Actor

app.dependency_overrides[get_actor]=lambda:Actor(id=ACTOR,is_active=True,roles=['MANAGER'],permissions=['rrhh.read','rrhh.manage'])
app.dependency_overrides[make_client]=lambda:FakeCorrespondencia()
