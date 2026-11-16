"""Single browser facade; all credentials/tokens remain owned by Correspondencia."""
from fastapi import APIRouter,Depends,Request,Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel,SecretStr,Field
from app.core.config import get_settings
from app.core.security import make_client
from app.external.correspondencia.errors import CorrespondenciaError

router=APIRouter(prefix='/auth',tags=['delegated-session'])


class LoginInput(BaseModel):
    username:str=Field(min_length=1,max_length=50)
    password:SecretStr


def clear_cookie(response,settings):
    response.delete_cookie(settings.session_cookie_name,path=settings.session_cookie_path,
        httponly=True,secure=settings.session_cookie_secure,samesite=settings.session_cookie_samesite)


def relay(operation,request,client,payload=None):
    settings=get_settings()
    refresh=request.cookies.get(settings.session_cookie_name)
    if operation=='refresh' and not refresh:
        response=JSONResponse({'detail':{'code':'authentication_required','message':'Sesión no disponible.'}},status_code=401)
        clear_cookie(response,settings)
        return response
    try:
        data,cookie=client.auth_request(operation,payload=payload,refresh_token=refresh,
            cookie_name=settings.correspondencia_refresh_cookie_name)
    except CorrespondenciaError as exc:
        response=JSONResponse({'detail':{'code':exc.code,'message':'No se pudo completar la sesión con Correspondencia.'}},status_code=exc.status)
        if exc.status in {401,403} or operation=='logout': clear_cookie(response,settings)
        return response
    if operation=='logout':
        response=Response(status_code=204);clear_cookie(response,settings)
        return response
    response=JSONResponse(data)
    response.set_cookie(settings.session_cookie_name,cookie['value'],max_age=cookie['max_age'],expires=cookie['expires'],
        path=settings.session_cookie_path,httponly=True,secure=settings.session_cookie_secure,samesite=settings.session_cookie_samesite)
    response.headers['Cache-Control']='no-store'
    return response


@router.post('/login')
def login(payload:LoginInput,request:Request,client=Depends(make_client)):
    return relay('login',request,client,{'username':payload.username,'password':payload.password.get_secret_value()})


@router.post('/refresh')
def refresh(request:Request,client=Depends(make_client)):
    return relay('refresh',request,client)


@router.post('/logout',status_code=204)
def logout(request:Request,client=Depends(make_client)):
    return relay('logout',request,client)
