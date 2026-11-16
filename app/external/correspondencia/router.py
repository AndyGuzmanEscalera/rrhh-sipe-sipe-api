"""Read-only compatibility facade; masters remain in Correspondencia."""
import uuid
from fastapi import APIRouter,Depends,Request
from app.core.security import make_client,require_permission,get_actor
from app.core.permissions import BASE_READ_PERMISSIONS,can_access_module

router=APIRouter(tags=['external-masters'])


@router.get('/auth/me')
def me(actor=Depends(get_actor)):
    # This facade exposes read-only masters. Do not advertise upstream write
    # capabilities which have no route here; the internal actor remains intact.
    result=actor.model_dump(mode='json')
    available={'rrhh.read','rrhh.manage','rrhh.resync','master_data.employees.read',
               'master_data.organizational_units.read','master_data.positions.read'}
    # Functional RRHH read capabilities derive from approved Gestor access.
    # Do not mutate upstream grants or the internal actor's permissions.
    base=BASE_READ_PERMISSIONS if can_access_module(actor) else frozenset()
    result['permissions']=sorted((set(actor.permissions) & available) | base)
    return result


@router.get('/admin/employees')
def employees(request:Request,actor=Depends(require_permission('master_data.employees.read')),client=Depends(make_client)):
    allowed={'page','page_size','search','is_active','include_deleted','unit_id','position_id','available_for_user','except_user_id'}
    return client.request('admin/employees',{k:v for k,v in request.query_params.items() if k in allowed})


@router.get('/admin/employees/{employee_id}')
def employee(employee_id:uuid.UUID,actor=Depends(require_permission('master_data.employees.read')),client=Depends(make_client)):
    return client.request(f'admin/employees/{employee_id}')


@router.get('/admin/organizational-units')
def areas(request:Request,actor=Depends(require_permission('master_data.organizational_units.read')),client=Depends(make_client)):
    return client.request('admin/organizational-units',dict(request.query_params))


@router.get('/admin/organizational-units/{area_id}')
def area(area_id:uuid.UUID,actor=Depends(require_permission('master_data.organizational_units.read')),client=Depends(make_client)):
    return client.get_area(area_id)


@router.get('/admin/positions')
def positions(request:Request,actor=Depends(require_permission('master_data.positions.read')),client=Depends(make_client)):
    return client.request('admin/positions',dict(request.query_params))


@router.get('/admin/positions/{position_id}')
def position(position_id:uuid.UUID,actor=Depends(require_permission('master_data.positions.read')),client=Depends(make_client)):
    return client.get_position(position_id)
