from functools import lru_cache
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.config import get_settings


@lru_cache
def get_engine():
    url = get_settings().database_url.get_secret_value()
    if not url:
        from app.core.errors import problem
        raise problem('database_not_configured','Configure DATABASE_URL de RRHH.')
    return create_engine(url, pool_pre_ping=True, hide_parameters=True)


def session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)()


def get_db():
    with session_factory() as db:
        yield db


# Conventional local session entrypoint; never imports shared application state.
SessionLocal = session_factory
