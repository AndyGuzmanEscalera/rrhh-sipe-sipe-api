from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class SafeErrorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request,call_next):
        try:
            return await call_next(request)
        except Exception:
            return JSONResponse({'detail':{'code':'internal_error','message':'Error interno controlado.'}},status_code=500)
