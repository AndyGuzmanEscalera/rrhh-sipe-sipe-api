from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from app.core.config import get_settings
from app.core.errors import problem
from app.external.correspondencia.client import CorrespondenciaClient
from app.external.correspondencia.config import CorrespondenciaConfig
from app.external.correspondencia.errors import CorrespondenciaError
from app.core.permissions import can_access_module,BASE_READ_PERMISSIONS

bearer = HTTPBearer(auto_error=False)


def make_client(request: Request):
    settings = get_settings()
    token = settings.correspondencia_api_token.get_secret_value()
    # Explicit configured technical bearer, otherwise delegated caller bearer.
    credentials = request.headers.get('authorization', '')
    if not token and credentials.lower().startswith('bearer '):
        token = credentials[7:]
    return CorrespondenciaClient(CorrespondenciaConfig(settings.correspondencia_api_url, token,
        settings.correspondencia_api_timeout))


def get_actor(credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
              client: CorrespondenciaClient = Depends(make_client)):
    if credentials is None:
        raise problem('authentication_required', 'Token Bearer requerido.', 401)
    try:
        actor = client.get_authenticated_user(credentials.credentials)
    except CorrespondenciaError as exc:
        raise problem(exc.code, 'No se pudo validar la sesión con Correspondencia.', exc.status) from None
    if not actor.is_active:
        raise problem('inactive_actor', 'Usuario inactivo.', 403)
    if not can_access_module(actor):
        raise problem('rrhh_access_denied', 'Acceso RRHH requiere el rol institucional Gestor.', 403)
    return actor


def require_permission(code):
    def authorize(actor=Depends(get_actor)):
        if not can_access_module(actor) or (code not in BASE_READ_PERMISSIONS and code not in actor.permissions):
            raise problem('permission_denied', 'Permiso insuficiente.', 403)
        return actor
    return authorize
