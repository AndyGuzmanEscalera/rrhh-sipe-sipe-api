from alembic import context
from sqlalchemy import create_engine,pool
from app.core.config import get_settings
from app.db.base import Base
from app.db import models

target_metadata=Base.metadata
url=get_settings().database_url.get_secret_value()
if not url: raise RuntimeError('DATABASE_URL is required')

if context.is_offline_mode():
    context.configure(url=url,target_metadata=target_metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    engine=create_engine(url,poolclass=pool.NullPool,hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=target_metadata,compare_type=True)
        with context.begin_transaction(): context.run_migrations()
