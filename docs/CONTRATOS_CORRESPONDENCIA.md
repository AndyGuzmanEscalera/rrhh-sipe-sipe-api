# Contratos de Correspondencia — auditados y probados el 16/11/2026

Fuente: `C:\dev\correspondencia_sipe_sipe\backend\app\modules`, routers/schemas
de auth, organization e identity. No se modificó ese código ni su DB real.
La prueba HTTP utilizó esa aplicación real, migrada a `n1`, en HTTPS loopback
con base descartable y maestros de prueba, según autorización del usuario.
No equivale a comprobar una instancia productiva ni un funcionario institucional.

Las rutas son internas. `CORRESPONDENCIA_API_URL` incluye el prefijo público
cuando exista (por ejemplo `https://host/api`); el cliente preserva ese prefijo.
JSON UUID/fecha se transporta como texto; no se transforma CI a número.

## Matriz de contratos existentes

Todas las lecturas requieren `Authorization: Bearer <access JWT Correspondencia>`.
Un permiso requerido se aplica al actor remoto activo. Una cuenta ADMIN no implica
un bypass local: la API RRHH verifica permisos efectivos de `/auth/me`.

| Método / ruta | Auth / permiso adicional | Request | Response / campos consumidos |
|---|---|---|---|
| POST `/auth/login` | público | JSON username (1–50), password (1–255) | access_token, expires_in, user; Set-Cookie refresh opaco HttpOnly |
| POST `/auth/refresh` | cookie refresh remoto | sin body de negocio | access_token, expires_in; nueva cookie rotada |
| POST `/auth/logout` | cookie refresh remoto | sin body | 204; revoca refresh y borra cookie |
| GET `/auth/me` | Bearer activo | ninguno | id, username, email, employee_id, is_active, employee, roles[], permissions[] efectivos |
| GET `/admin/employees` | master_data.employees.read | page>=1, page_size 1–100; search, is_active, include_deleted, unit_id, position_id, available_for_user, except_user_id | paginado items/page/page_size/total/total_pages; campos empleado descritos abajo |
| GET `/admin/employees/{employee_id}` | master_data.employees.read | UUID path | id, first_name, last_name, document_number, email, phone, unit_id, unit_name, position_id, position_name, is_active, auditoría |
| GET `/admin/employees?search={CI}&include_deleted=true` | master_data.employees.read | CI textual, page_size=100, recorrer páginas | búsqueda parcial del maestro: el cliente exige igualdad exacta de document_number; 0→None, >1→409 employee_document_ambiguous |
| GET `/admin/organizational-units` | master_data.organizational_units.read | page, page_size, search, is_active | paginado de unidades: id/code/name/description/parent_id/parent_name/is_active y auditoría |
| GET `/admin/organizational-units/{unit_id}` | master_data.organizational_units.read | UUID path | mismo objeto unidad; RRHH usa id/name y datos de lectura Flutter |
| GET `/admin/positions` | master_data.positions.read | page, page_size, search, is_active | paginado: id/code/name/description/hierarchy_id/hierarchy_code/hierarchy_name/is_active y auditoría |
| GET `/admin/positions/{position_id}` | master_data.positions.read | UUID path | mismo objeto cargo; RRHH usa id/name y datos de lectura Flutter |
| GET `/admin/users` | master_data.users.read | page, page_size, search, is_active | paginado UserAdminResponse; no se replica en RRHH |
| GET `/admin/users/{user_id}` | master_data.users.read | UUID path | id/username/email/employee_id/employee_name/unit_name/is_active/role_codes/direct_permissions/effective_permissions y auditoría |
| GET `/admin/roles` | master_data.roles.assign | ninguno | lista id/code/name/description; inspección de roles, no fuente de identidad |
| GET `/admin/permissions` | master_data.users.manage y master_data.roles.assign; restricciones adicionales RbacService | ninguno | catálogo de permisos otorgables por actor, id/code/description; auditado en código, no necesario para evaluación RRHH |

El servicio de reportes consume del empleado únicamente id, nombres, CI, unidad,
cargo y estado, mediante DTO; los campos extra se ignoran allí. La fachada de
listados conserva el JSON maestro para los modelos Flutter existentes.
`employee` de `/auth/me` contiene id/full_name/document_number y objetos unit,
position e hierarchy opcionales, preservados en el perfil del frontend.

No hay endpoint dedicado de CI exacto ni batch de funcionarios identificado.
Se limita la búsqueda paginada a 1000 páginas y se rechazan respuestas inconsistentes.
No se usa la coincidencia de CI para crear automáticamente un vínculo biométrico.
No se encontró fuente de asignación contractual institucional: permanece pendiente.

## Sesión delegada y fachada única

Flutter → API RRHH `/auth/login` → Correspondencia `/auth/login`.
La respuesta conserva el access JWT original. No hay firma JWT RRHH, usuarios,
passwords ni sesiones propias. Las credenciales de login solo se reenvían en
memoria por HTTPS, sin token técnico, y no se registran.

El refresh opaco se devuelve en cookie `rrhh_refresh_token` HttpOnly, sin Domain,
con Secure=true y SameSite=lax por defecto. `/auth/refresh` lo envía al maestro
como su cookie configurada (`refresh_token` por defecto), conservando la rotación.
Logout remoto revoca ese refresh y la fachada borra su cookie. El access JWT sigue
las reglas/TTL del maestro: logout no promete invalidarlo inmediatamente.

Configurar SESSION_COOKIE_PATH con la ruta **pública** de auth. Por ejemplo, para
API publicada en `/api`, usar `/api/auth`; sin proxy el default es `/auth`.
Producción y SameSite=none rechazan cookies sin Secure. Development HTTP puede
usar Secure=false explícitamente; no es la configuración de despliegue.
CORS admite solo orígenes configurados explícitamente y credenciales.

Cada solicitud de negocio valida el Bearer del usuario en `/auth/me`, comprueba
is_active y el código MANAGER en roles. No hay caché de identidad; cambios de actividad/permisos se
consultan en cada petición. Un token técnico nunca sustituye al actor auditado.

El `/auth/me` público RRHH conserva roles/perfil y anuncia solo capacidades
implementadas: rrhh.read/manage/resync y lecturas employees/units/positions.
El actor interno conserva los permisos completos del maestro. La fachada solo
expone GET de maestros; no anuncia ni ejecuta CRUD de empleados/usuarios/roles.
Su administración permanece en Correspondencia, sin duplicación.

## Permisos funcionales RRHH

**En la primera versión, todos los usuarios activos de tipo/rol GESTOR de Correspondencia tienen acceso base a RRHH.**

Identificación real: `MeResponse.roles: list[str]`, llenado por
`RbacService.get_user_role_codes`, que devuelve códigos de roles activos.
`app/modules/identity/institutional_base_roles.py`, BASE_ROLES, define expresamente
`MANAGER` con nombre institucional `Gestor`. Por eso se exige `MANAGER` exacto en
`roles`; no un campo `role/user_type`, cargo, texto traducido ni permiso indirecto.
No se inventó un rol `GESTOR`, ni se aceptan ADMIN/SUPERADMIN como sustitutos.
Un administrador que deba operar RRHH también debe tener el rol Gestor.

Acceso al módulo: autenticado, activo y roles contiene MANAGER. No requiere
rrhh.read. Falta/invalidez de Bearer→401; usuario activo no Gestor→403;
is_active=false→403. El maestro actualmente rechaza antes al sujeto inactivo o
inexistente con 401 y el detalle exacto `Usuario no disponible`; solo ese contrato
legacy auditado se normaliza a inactive_actor/403. Otros 401 permanecen 401.
Si cambia el mensaje legacy, se conserva el rechazo, sin convertirlo por heurística;
un código de error estructurado remoto sería una mejora futura, no aplicada aquí.

| Capacidad | Regla actual |
|---|---|
| Entrada / lectura / reportes / calendario / parámetros y turnos de lectura | usuario activo con roles que contiene MANAGER |
| Gestión / sync manual / horarios / parámetros / vínculos / asignaciones / calendario y políticas | acceso base Y rrhh.manage |
| Force resync | acceso base Y rrhh.manage Y (ADMIN o SUPERADMIN o rrhh.resync) |
| Listados de funcionarios / áreas / cargos en fachada RRHH | acceso base; el maestro conserva su propia autorización |

La fachada `/auth/me` proyecta las capacidades de lectura derivadas de esta
política (rrhh.read y las tres lecturas de maestros) para los guards existentes
de Flutter, sin escribir grants en Correspondencia ni alterar permisos del actor.
No agrega rrhh.manage/resync. Flutter no decide acceso a partir de roles locales.
MANAGER ya tiene esas lecturas de maestros en BASE_ROLES de Correspondencia.
Si una instalación las revoca o no aplica su seed, el upstream seguirá devolviendo
403: RRHH no omite su RBAC. Debe resolverse allí la provisión de lecturas o mediante
un Bearer técnico de lectura aprobado; no se conceden permisos automáticamente.

`GET /rrhh/capabilities` refleja este mapa. No se añadieron permisos ni roles al
código de Correspondencia. ADMIN usado en la prueba es fixture de su DB descartable.
Separar sync/horarios/parámetros/vínculos en permisos más finos sería una propuesta
institucional futura; actualmente existe la capacidad agregada rrhh.manage.

## Token técnico / M2M

No se identificaron service accounts, API keys, client credentials ni emisión
dedicada M2M en los routers/auth/dependencias inspeccionados. Solo JWT de usuario
y refresh de sesión. No se fabricó un mecanismo ni se automatizó login técnico.

CORRESPONDENCIA_API_URL, CORRESPONDENCIA_API_TOKEN y
CORRESPONDENCIA_API_TIMEOUT=30 preparan el adaptador: si el token está vacío, las
lecturas usan el Bearer del actor; si se configura, solo se usa para maestros.
Una copia manual de access JWT expira y no constituye M2M definitivo. Faltan un
contrato de emisión, alcances de solo lectura, expiración, rotación y revocación
aprobados por el propietario del maestro. La identidad siempre usa Bearer usuario.

## Fallos, TLS y evidencia

HTTPS obligatorio para upstreams, TLS verificado y redirects desactivados. Timeout
controlado →504, servicio inaccesible/respuesta inválida→503, rechazo auth→401/403,
empleado inexistente→404, CI ambiguo→409. No se incluyen cuerpos privados ni headers
de upstream en errores. Los tests cubren timeout, rechazo, respuestas inválidas,
identidad inconsistente, revocación y rotación. Fallos de transporte se simulan;
no se provocaron fallos en servicios institucionales.

Prueba real local: login/refresh/me/listado/detalle/CI/área/cargo/usuario/roles HTTP200
y logout204. Latencias y límites de esa evidencia en `INTEGRACION_REAL_Y_FLUTTER.md`.
