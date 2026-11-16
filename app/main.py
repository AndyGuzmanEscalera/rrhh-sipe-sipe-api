from fastapi import FastAPI,Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from app.core.config import get_settings
from app.core.cors import configure_cors
from app.core.error_middleware import SafeErrorMiddleware
from app.core.logging import configure_logging
from app.core.security import get_actor
from app.external.correspondencia.errors import CorrespondenciaError
from app.biometric.api_client.errors import BiometricApiError
from app.biometric.router import router as biometric
from app.attendance.router import router as attendance
from app.shifts.router import router as shifts
from app.reports.router import router as reports
from app.external.correspondencia.router import router as masters
from app.external.correspondencia.session_router import router as session_router

configure_logging()
app=FastAPI(title='RRHH Sipe Sipe API',version='0.1.0')
app.add_middleware(SafeErrorMiddleware)
configure_cors(app,get_settings())
for router in (biometric,attendance,shifts,reports,masters):
    app.include_router(router,dependencies=[Depends(get_actor)])
app.include_router(session_router)


@app.get('/rrhh/capabilities')
def capabilities_endpoint(actor=Depends(get_actor)):
    from app.core.permissions import capabilities
    return capabilities(actor)


from fastapi.exceptions import RequestValidationError
@app.exception_handler(RequestValidationError)
async def invalid_request(request,exc):
    # Never echo a login password in validation diagnostics.
    return JSONResponse({'detail':{'code':'invalid_request','message':'Revise los campos de la solicitud.'}},status_code=422)


@app.get('/health')
def health():
    return {'status':'ok','service':'rrhh-sipe-sipe-api'}


@app.exception_handler(CorrespondenciaError)
async def external_error(request,exc):
    return JSONResponse({'detail':{'code':exc.code,'message':'Consulta a Correspondencia no completada.'}},status_code=exc.status)


@app.exception_handler(BiometricApiError)
async def biometric_error(request,exc):
    return JSONResponse({'detail':{'code':exc.code,'message':exc.message}},status_code=503)


@app.exception_handler(IntegrityError)
async def conflict(request,exc):
    return JSONResponse({'detail':{'code':'data_conflict','message':'Restricción de integridad o concurrencia.'}},status_code=409)
