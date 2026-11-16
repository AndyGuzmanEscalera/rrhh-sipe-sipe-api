"""RRHH module access is separate from administrative upstream permissions.

Correspondencia BASE_ROLES maps code MANAGER to institutional name Gestor.
/auth/me exposes role codes, never role display names or job positions.
"""
GESTOR_ROLE_CODE='MANAGER'
BASE_READ_PERMISSIONS=frozenset({'rrhh.read','master_data.employees.read',
    'master_data.organizational_units.read','master_data.positions.read'})


def can_access_module(actor):
    return actor.is_active and GESTOR_ROLE_CODE in actor.roles


def capabilities(actor):
    read=can_access_module(actor)
    manage=read and 'rrhh.manage' in actor.permissions
    return dict(read=read,reports=read,manage=manage,sync=manage,shifts=manage,parameters=manage,links=manage,
        force_resync=manage and ('rrhh.resync' in actor.permissions or bool({'ADMIN','SUPERADMIN'} & set(actor.roles))))
