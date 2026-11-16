# Integración real y Flutter — informe del 16/11/2026

API: `C:\dev\rrhh_sipe_sipe_api`. Flutter: `C:\Andy\proyectos\rrhh_sipe_sipe`.
Esta fase termina sin commit, push, cambios de remote, despliegue, QA o producción.

## Actualización institucional de acceso — previa al commit

**En la primera versión, todos los usuarios activos de tipo/rol GESTOR de Correspondencia tienen acceso base a RRHH.**

Campo real auditado: `/auth/me.roles`, lista de códigos de roles activos producida
por RbacService.get_user_role_codes. El seed oficial BASE_ROLES en
`app/modules/identity/institutional_base_roles.py` identifica MANAGER con nombre
Gestor. Se usa ese código exacto, sin inferencias por cargo, user_type o permisos.
No se añadió un rol GESTOR ni se modificó Correspondencia.

Regla vigente: Bearer validado en Correspondencia + is_active + MANAGER en roles.
Entrada y lectura no requieren rrhh.read. No Gestor/inactivo→403; token ausente o
inválido→401; maestro caído→503/504 controlado. El 401 legacy de `/auth/me` cuyo
JSON es exactamente `{"detail":"Usuario no disponible"}` se traduce a403 porque
el código auditado lo usa para un sujeto inexistente/inactivo después de validar
el JWT. Cualquier otro 401 conserva401. No se inspecciona ni confía en el JWT local.
Un error estructurado remoto sería más estable que ese mensaje legacy; no se
cambió Correspondencia para añadirlo.

Todos los Gestor pueden consultar funcionarios, áreas/cargos, estado, marcaciones
persistidas, cobertura/trabajos, calendario/asistencia/reportes, horarios,
asignaciones y parámetros en lectura. El perfil público RRHH proyecta las cuatro
capacidades de lectura para el Flutter existente, sin cambiar los grants remotos
ni los permisos internos del actor. MANAGER ya incluye lecturas de maestros en el
seed oficial. Si se revocan en una instancia, el upstream puede negar403 y debe
resolverse la provisión institucional; la fachada no evita esa autorización.

Operaciones administrativas RRHH: requieren además rrhh.manage (sync manual,
modificaciones de horarios/parámetros, asignaciones, vínculos, importación,
calendario y políticas). Force exige también ADMIN/SUPERADMIN o rrhh.resync.
ADMIN/SUPERADMIN sin MANAGER no concede entrada ni administración RRHH.
No se crearon usuarios, passwords, JWT, refresh tokens propios ni roles/grants.
La cookie existente solo transporta el refresh opaco emitido por Correspondencia.

Flutter no necesitó cambios de producción: consume el perfil de la fachada, y
la API vuelve a autorizar cada operación. Se añadieron dos pruebas para comprobar
lecturas visibles sin administración y que Flutter no inventa permisos por rol.
Pruebas finales de este ajuste: backend172 (131 anteriores +41 casos nuevos),
Alembic upgrade/check correctos, pruebas Flutter afectadas22 y suite raíz83,
analyze sin incidencias. No se repitió el build web porque no cambió código Flutter
de producción, configuración ni assets; el release de la fase anterior sigue
documentado abajo. Escaneo de secretos repetido antes del cierre.

La primera ejecución de esta suite no llegó a los tests por el puerto del clúster
temporal; se corrigió su arranque explícito a127.0.0.1:55449. Todas las pruebas
finales pasaron. Las mediciones reales de integración de las secciones siguientes
son evidencia de la fase previa, no una nueva consulta al biométrico en este ajuste.
La cuenta fixture anterior tenía ADMIN; el helper de reproducción se adapta a los
roles existentes MANAGER+ADMIN sin crear roles nuevos. No se ejecutó ese helper ni
se escribió en la DB de Correspondencia durante el ajuste de acceso.

## Alcance de la evidencia

El usuario autorizó expresamente levantar Correspondencia local con DB descartable
y datos de prueba. Se ejecutó su aplicación real sin modificar su fuente, migración
`n1`, HTTPS `127.0.0.1:8310`, PostgreSQL `corr_integration_test_20261116`.
El funcionario, CI, área, cargo, cuenta y grants usados allí son fixtures.
No se accedió a la DB real de Correspondencia ni se eliminó su módulo RRHH anterior.
Esto comprueba el contrato HTTP real del código disponible, no datos institucionales.

La API RRHH real corrió en HTTPS `127.0.0.1:8311`, migración `0001_rrhh`, con base
`rrhh_api_test_real_integration_20261116`. Las suites destructivas usan otra base,
`rrhh_api_test_final_20261116`; el helper exige loopback y nombre `rrhh_api_test_*`.
El clúster propio está en `.local/postgres`, puerto 55449, distinto de los servicios
instalados. Los servidores de prueba fueron detenidos al terminar.

La Biometric API **sí es el servicio HTTPS real** `https://biometric.sipesipe.gob.bo`.
Solo se usaron sus GET; ninguna conexión directa al reloj, pyzk, escritura, borrado,
reinicio o cambio en ese servicio. El token existente se leyó en memoria desde
configuración privada; nunca se copió a `.env`, código, logs ni documentación.

`scripts/validate_local_integration.py` reproduce el escenario específico con bases
dedicadas, TLS verificado por CA local, sin verify=False ni redirects. Genera claves
y logs únicamente en `.local/integration/` ignorado. Requiere cryptography instalado
en `.local/tools` y las dos bases locales ya creadas; no es script de producción.
No volver a ejecutarlo automáticamente: vuelve a consultar el biométrico real y
su guard rechaza una ejecución cuyo result.json ya existe. Los fixtures de horario
y parámetros están en `tests/fixtures/`, sin información personal.

## 1–5. Correspondencia, sesión, permisos y pruebas HTTP

Contrato completo método/ruta/auth/request/response/permisos/campos consumidos en
`CONTRATOS_CORRESPONDENCIA.md`. Se inspeccionaron auth/me, empleados/listado/ID/CI,
unidades, cargos, usuarios, roles y catálogo de permisos. CI exacto y contrato
institucional no tienen endpoints específicos identificados.

Autenticación implementada: Bearer recibido → `/auth/me` del maestro en cada
petición → actor interno activo, UUID y permisos efectivos. Sin caché de identidad.
Login/refresh/logout son relay de la sesión existente: mismo JWT y refresh opaco,
cookie RRHH HttpOnly/Secure configurable por ruta pública. No cuentas, password,
sesiones ni JWT propios. El token técnico nunca autoriza acciones por el usuario.

Mapa vigente actualizado: acceso base Gestor para lectura/reportes; rrhh.manage
adicional para gestión/sync/horarios/parámetros/vínculos; force exige además
ADMIN/SUPERADMIN o rrhh.resync. Los maestros conservan su RBAC remoto.
`/rrhh/capabilities` publica ese mapa; ver actualización institucional arriba.
No se crearon permisos/roles institucionales nuevos. Los grants de prueba existen
solo en la base descartable de Correspondencia.

No se encontró M2M dedicado. Quedan configurables CORRESPONDENCIA_API_URL/TOKEN/
TIMEOUT. Sin token técnico, las lecturas delegan el Bearer del actor, que necesita
los permisos maestros. Un JWT manual que expira no se presenta como solución M2M.
Faltan contrato de emisión/alcances/renovación/revocación para una futura credencial.

| Operación HTTP real local | Estado | Latencia ms |
|---|---:|---:|
| Login por fachada | 200 | 310.45 |
| Refresh por fachada | 200 | 168.87 |
| auth/me | 200 | 787.06 |
| List employees | 200 | 624.84 |
| Employee por ID | 200 | 591.12 |
| CI exacto por cliente paginado | 200 | 729.60 |
| Área | 200 | 462.31 |
| Cargo | 200 | 871.93 |
| Usuario | 200 | 933.55 |
| Roles | 200 | 789.80 |
| Logout por fachada | 204 | 372.44 |

Ningún fallo en estas llamadas. Timeout, rechazo 401/403, respuesta inválida,
inactividad/permisos revocados, cookie inválida y ausencia de caché se probaron
con transporte controlado, sin provocar incidentes externos. Timeout maestro=30s.

## 6–10. Biométrico, sync, deduplicación y cobertura

| GET real | Estado | Latencia ms | Evidencia |
|---|---:|---:|---|
| /health | 200 | 12788.90 | arranque/primera consulta |
| /device | 200 | 1281.54 | device alcaldia-central |
| /users | 200 | 1960.94 | 546 usuarios |
| /attendance, 11/11/2026 | 200 | 28620.15 | total real actual 572 |

El valor actual es 572, no se forzó el histórico aproximado de 414.

| Trabajo real (día 11/11/2026) | Plan | Recibidos | Insertados | Duplicados | Tiempo worker |
|---|---|---:|---:|---:|---:|
| Primero, completed | solo 11/11 | 572 | 572 | 0 | 58.45 s |
| Repetición, completed | vacío | 0 | 0 | 0 | 0.013 s |
| Force, completed | solo 11/11 | 572 | 0 | 572 | 62.56 s |

POST devuelve 202 y el worker hace la descarga; no bloquea el frontend esperando
todo el histórico. La DB conserva 572 record_id únicos. Coverage del día COMPLETE.
Force vuelve a consultar el día sin duplicar ni sobrescribir evidencia.
El hash usa dispositivo/usuario/timestamp/status/punch; uid no altera identidad.
Timestamp local, códigos originales y uid permanecieron iguales tras link/reportes.
Solo el enriquecimiento UUID externo se añadió por vínculo explícito auditado.

Cobertura A: test local `test_partial`, COMPLETE 1–20 / pedir 1–30 → solo 21–30.
B: test de worker con reloj controlado, HOY→OPEN; al día siguiente debe releerse
antes de COMPLETE. C: transporte simulado timeout/unreachable/unauthorized→FAILED,
reintento exitoso→COMPLETE. Estas pruebas no descargaron días extra del reloj real.
D: force sí se comprobó además contra el servicio real, con los contadores arriba.

Limitación física conocida: el servidor biométrico lee todo el histórico del equipo
antes de filtrar/paginar cada GET. Dos páginas de 500 para 572 registros implican
dos lecturas remotas. El timeout de 60s se aplica por petición, no al trabajo total.
No hay export/snapshot remoto estable. COMPLETE es cierre operativo tras consulta
exitosa, no certificación de conservación completa del equipo. La paginación
rechaza cambios del total y los locks de RRHH coordinan su misma DB, no instancias
PostgreSQL distintas ni el sincronizador antiguo.

## 11–15. Vínculo, horario y reporte sin biométrico

Se eligió un usuario biométrico con marcas persistidas y se vinculó explícitamente
con el funcionario fixture existente en Correspondencia local. CI maestro de
prueba deliberadamente distinto: no hubo match automático. No se publicó su ID,
CI ni marcas personales. El funcionario institucional real queda para una prueba
posterior autorizada; esta sustitución fue expresamente solicitada por el usuario.

Turno local de prueba 08–12/14–18, segmentos, ventanas y vigencia 01–30/11 se crearon
por HTTP a partir de JSON, no como constantes de negocio Python. Se creó asignación
al UUID externo, parámetros explícitos de prueba (seis valores cero), calendario
del día 11 y ámbito técnico confirmado fixture; no contrato institucional inventado.
Estos valores no son reglas de producción ni datos seed de arranque.

Reporte HTTP del día 11: nombre/área/cargo verificados contra respuesta del maestro;
2 marcas RAW de ese usuario desde PostgreSQL RRHH; horario y parámetros desde sus
tablas locales; ready=true para las dependencias de esta prueba acotada.
Se añadió un rango opcional start_date/end_date al reporte, ambos requeridos juntos
y dentro del período nominal confirmado, para no fingir calendario/cobertura de un
mes completo. El contrato mensual existente sin esos parámetros se conserva.

La reevaluación de solo lectura de la evidencia persistida da ANOMALY, dos
incidencias y reconstrucción incompleta; late_seconds/early_exit_seconds=null y
totals_complete=false. No se clasificó NORMAL ni se inventaron ceros/ausencias con
solo dos marcas. Las políticas de ausencia, extras y tolerancia de salida siguen
pendientes. ready no significa aprobación institucional ni totales completos.

Prueba crítica: se reinició únicamente la instancia local RRHH apuntando el cliente
biométrico a HTTPS loopback puerto cerrado, timeout1s. `/rrhh/biometric/status`→503
confirma la caída simulada. Tres reportes→200, JSON exactamente igual al anterior.
El servicio biométrico externo siguió operativo y no se modificó.

Latencia inicial reporte: 1302.21ms. Con biométrico inaccesible:
1256.40 / 1060.94 / 782.23ms, promedio 1033.19ms. Incluye autenticación y maestros
remotos locales; no es tiempo SQL puro ni SLA productivo. Sin HTTP al biométrico,
sin sync implícito y 572 evidencias RAW intactas tras todo el escenario.

## 16–18. Auditoría y adaptación Flutter

La matriz completa y el comando web están en `INTEGRACION_API_INDEPENDIENTE.md`
del frontend. Una sola API_BASE_URL configurable: desarrollo localhost:8200,
producción define explícito. No token biométrico ni cliente directo Correspondencia
en Flutter. Login/refresh/me/logout existentes conservan el mismo Bearer; el
coordinador/interceptor de renovación y perfil institucional se reutilizan.

Parámetros añaden vigencias, conservan segundos y revisiones. El modelo permite
api_available/device_connected. La vista distingue RRHH inaccesible, maestro
inaccesible, Biometric API caída, dispositivo desconectado y sync pendiente/fallido;
coverage incompleta se explica como dependencia pendiente. Errores estructurados
tienen textos seguros; logs redactan Authorization, cookies, tokens y credenciales.

Maestros de funcionarios/áreas/cargos: lectura remota compatible. Las escrituras
administrativas, usuarios y jerarquías no se proxyan en esta entrega. `/auth/me`
público no anuncia permisos sin ruta, evitando que Flutter ofrezca operaciones
que terminarían en 404. El actor interno mantiene permisos reales para auditoría.
No se reescribieron pantallas ni se crearon maestros locales. La gestión institucional
de esos maestros sigue en Correspondencia.

Cookie pública/path, HTTPS y CORS deben acordarse con el proxy futuro. El router
ya usa `/rrhh`, `/rrhh/login`, `/rrhh/home`; base-href permanece `/` para evitar
duplicar prefijo. Build validado: `flutter build web --release --base-href=/
--dart-define=API_BASE_URL=/api`. Requiere servir assets en raíz y fallback de
rutas profundas; un montaje exclusivo de assets en `/rrhh/` exige revisión del
hosting/router antes de QA. No se cambió configuración funcional por anticipado.

## 19–22. Validaciones, Docker y secretos

Resultado de la fase de integración previa: `alembic upgrade head` OK, `alembic check` sin cambios y **131 tests passed**
(114 anteriores +17 nuevos). Una advertencia externa de deprecación
Starlette/AnyIO; cero fallos funcionales. Python3.12/PostgreSQL18 nativos locales.

Flutter en esa fase previa: pub get OK; analyze **sin incidencias**; **160 pruebas aprobadas**:
raíz81, rrhh_api21, rrhh_repository54, failures4. Incluye 11 pruebas nuevas de
errores seguros/logs, contrato de vigencia y la suite existente de sesión.
Build web release OK, 80.3s. Pub informa url_strategy discontinuado y dependencias
con versiones nuevas fuera de restricciones; no se hizo actualización masiva.

Docker Engine inicialmente no respondía; durante el cierre quedó disponible
(29.8.0) y se completó la validación autorizada. Build de API y worker OK; PostgreSQL
16-alpine en Compose local con proyecto exclusivo `rrhh-validation-20261116`,
API expuesta solo en 127.0.0.1:8200. Migración y alembic check OK. Health200,
ruta protegida sin Bearer401 y OpenAPI36 paths. El worker consumió un trabajo
fixture cuyo día ya estaba COMPLETE: completed, plan vacío, cero descargas.
Una marca sintética status89/punch255 y la cobertura permanecieron tras restart
de los tres servicios y tras down/up conservando volumen. No se montaron tokens
reales ni bases ajenas; upstreams del contenedor apuntaron a dominios inválidos.
La integración externa real es la prueba nativa HTTPS descrita arriba, no una
afirmación de integración remota dentro de Docker. Contenedores de validación
detenidos al finalizar; volumen exclusivo conservado para revisión. Los servicios
Correspondencia/Gestión que ya estaban en Docker no se modificaron.

`.env` y variantes privadas, `.local/`, claves/certificados, DB, logs y caches están
ignorados. `.env.example` contiene placeholders vacíos/REPLACE, sin secretos.
El token real fue comparado en memoria contra archivos publicables sin imprimirlo.
Refresh/Bearer de pruebas no se guardaron en fuente ni en el informe. Los artefactos
locales con evidencia personal y claves permanecen excluidos. Flutter ignora también
builds y lockfiles generados por pruebas independientes de paquetes library.

## 23–27. Git, riesgos, pendientes y conclusión

API: master, sin commits ni remotes, implementación/documentación/scripts/tests
todavía no versionados. Flutter: cambios de esta fase sin stage, más los cambios
previos conservados de los dos documentos y attendance_view.dart. Inventario exacto
final en `git status`; no se alteraron ramas ni se hicieron commits/merges/rebases.
Correspondencia conserva solo su `?? .wrangler/` preexistente; Biometric API limpio.

Pendientes antes de desplegar: M2M definitivo si se requiere; pruebas con maestros
institucionales autorizados; contrato/población/calendario/horarios/parámetros reales;
políticas de salida anticipada, ausencia, extras y nocturnos;
proxy/cookie/CORS/URL pública y rutas profundas; cutover que impida dos syncs de DB
distintas y plan de privacidad/respaldo de RAW. El reporte aún depende del maestro
para autenticación/cabecera: solo es independiente de Biometric API.

**LISTO PARA COMMIT de esta fase de código y pruebas**, sujeto a revisión del usuario.
No equivale a listo para QA/producción ni a reglas de asistencia institucionales
confirmadas. No se ejecutó commit, push, configuración de remote ni despliegue.
