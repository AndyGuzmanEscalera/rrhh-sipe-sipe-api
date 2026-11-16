"""Public dependency boundary for local persistence and delegated identity."""
from app.db.session import get_db
from app.core.security import get_actor,make_client,require_permission
