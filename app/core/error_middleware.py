import logging

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

class SafeErrorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        try:
            return await call_next(request)
        except Exception:
            logger.exception('Unhandled request error: %s %s', request.method, request.url.path)
            return JSONResponse({'detail':{'code':'internal_error','message':'Error interno controlado.'}},status_code=500)
