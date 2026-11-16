"""Read local PostgreSQL coverage/jobs; does not call either external API."""
import json
from sqlalchemy import select,text
from fastapi.encoders import jsonable_encoder
from app.db.session import session_factory
from app.biometric.models import Sync,Coverage
from app.biometric.storage import sync_output


def main():
    with session_factory() as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        jobs=[sync_output(r) for r in db.scalars(select(Sync).order_by(Sync.created_at.desc()).limit(20))]
        coverage=list(db.scalars(select(Coverage).order_by(Coverage.date)))
        print(json.dumps(jsonable_encoder({'jobs':jobs,'coverage':coverage}),ensure_ascii=False,indent=2))


if __name__=='__main__': main()
