# API RRHH Sipe Sipe independiente

FastAPI / PostgreSQL propios. Correspondencia conserva usuarios, funcionarios, áreas, cargos y permisos maestros y se consulta exclusivamente por HTTPS. Biometric API conserva el driver ZKTeco. Este proyecto no importa código de esos repositorios, no contiene tablas maestras duplicadas y no abre conexiones al puerto 4370.

## Estado de la entrega

Implementación y validación local; sin commit, remoto, push ni deploy. Ver [IMPLEMENTACION_RRHH_INDEPENDIENTE.md](IMPLEMENTACION_RRHH_INDEPENDIENTE.md) para resultados y límites, y [docs/CONTRATOS_CORRESPONDENCIA.md](docs/CONTRATOS_CORRESPONDENCIA.md) para contratos y autenticación provisional.

```text
Flutter → API RRHH → PostgreSQL RRHH (marcas/reglas/horarios/cobertura)
                  → API Correspondencia HTTPS (maestros e identidad)
                  → Biometric API HTTPS → ZKTeco
```

## Preparar Python

Python 3.11+; validado con Python 3.12.10 y PostgreSQL 18 local. Docker usa Python 3.12 y PostgreSQL 16, con volumen exclusivo.

```powershell
cd C:\dev\rrhh_sipe_sipe_api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
```

Completar `.env` privadamente. No versionarlo. `DATABASE_URL` debe apuntar SOLO a PostgreSQL RRHH. El template para Compose usa `postgres` como host interno; la ejecución nativa requiere host/puerto local propios.

| Variable | Propósito |
|---|---|
| `DATABASE_URL` | URL SQLAlchemy `postgresql+psycopg://...` de RRHH |
| `CORRESPONDENCIA_API_URL` | Base HTTPS real, incluyendo su prefijo público `/api` si corresponde |
| `CORRESPONDENCIA_API_TOKEN` | Bearer técnico opcional para leer maestros; vacío delega el Bearer del solicitante |
| `CORRESPONDENCIA_API_TIMEOUT` | Tiempo máximo por petición, 30 segundos por defecto |
| `BIOMETRIC_API_URL` | `https://biometric.sipesipe.gob.bo` |
| `BIOMETRIC_API_TOKEN` | Secreto privado servidor a servidor |
| `BIOMETRIC_API_TIMEOUT` | Tiempo máximo por petición, 60 segundos por defecto |
| `BIOMETRIC_DEVICE_ID` | `alcaldia-central`; debe coincidir con el identificador remoto estable |
| `LOCAL_TIMEZONE` | `America/La_Paz`; decide HOY y AYER, no convierte las marcas originales |
| `CORS_ALLOW_ORIGINS` | Orígenes exactos separados por comas; vacío para mismo origen; sin `*` |
| `SCHEDULER_ENABLED` | `false` por defecto; activar solo con configuración/operación aprobadas |
| `SCHEDULER_INTERVAL_SECONDS` | Intervalo configurable; ejemplo técnico 900, no cron institucional aprobado |
| `WORKER_POLL_SECONDS` | Pausa de la cola, 5 segundos por defecto |
| `POSTGRES_PASSWORD` | Solo creación del PostgreSQL exclusivo en Compose; coincidir con la URL privada |

No usar contraseñas de usuarios para autenticar integraciones. RRHH no firma JWT ni almacena passwords, sesiones o cuentas locales. Un Bearer técnico configurado debe ser un token que los endpoints existentes realmente acepten; no hay un mecanismo especial de service tokens inventado. Cada solicitante se valida siempre mediante su propio Bearer en `/auth/me`, aunque exista token técnico de datos.

## Arranque local propio

Cuando PostgreSQL RRHH esté preparado y la URL sea la de ese entorno local:

```powershell
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8200
```

En otra terminal del mismo proyecto/entorno:

```powershell
python -m app.biometric.worker
# O un ciclo atendido:
python -m app.biometric.worker --once
```

No se ejecutan migraciones al arrancar FastAPI ni el worker. `/health` y `/openapi.json` pueden responder sin configurar servicios externos. Los endpoints de negocio necesitan autenticación y la base propia. El worker no construye el cliente biométrico si no tiene trabajos.

## Docker / Compose

```powershell
docker compose up -d --build postgres
docker compose run --rm api python -m alembic upgrade head
docker compose up -d --build api worker
```

Estos comandos son para un entorno RRHH autorizado y aislado; no se ejecutaron contra QA/producción. API se publica solo en `127.0.0.1:8200`; PostgreSQL no publica puerto. Nombre del proyecto `rrhh-independent`, volumen `rrhh_postgres_data`, sin volúmenes o redes privadas de Correspondencia. El contenedor puede salir por HTTPS a ambos servicios externos. El proxy/TLS público se prepara en otra fase.

Se validó `docker compose config --quiet` con un valor ficticio para la expansión obligatoria de password. Docker Engine no estaba iniciado, por lo que no se construyó ni ejecutó la imagen en esta entrega.

## Sincronización y cobertura

- `POST /rrhh/biometric/sync` conserva `start_date/end_date` inclusivos y añade `force` opcional. Requiere `rrhh.manage`. Force requiere además rol `ADMIN`/`SUPERADMIN` o capacidad explícita `rrhh.resync`.
- Se crea un trabajo durable y el worker calcula los huecos **al ejecutarlo**, usando cobertura por dispositivo y fecha. Un índice único permite un solo trabajo queued/running por dispositivo.
- El planner omite COMPLETE anteriores a HOY, incluye OPEN, FAILED y faltantes, y agrupa días consecutivos. Rechaza futuro, rangos invertidos o mayores de 367 días.
- Un rango correcto confirma registros y cobertura en la misma transacción. Pasado → COMPLETE; HOY → OPEN. Mañana ese OPEN debe volver a leerse para cerrar. El reloj de planificación se fija al inicio del trabajo: si cruza medianoche, conservar OPEN es intencionadamente conservador.
- Una respuesta vacía válida puede cerrar un día pasado con count 0; nunca significa ausencia del funcionario.
- Si falla un rango, conserva marcas anteriores y los rangos ya confirmados, y marca FAILED el intento incompleto. `last_attempt_sync_id` permite reconocer trabajos interrumpidos. Al recuperar un running sin propietario se marca failed y requiere reintento.
- `records_received` cuenta las filas recibidas incluso si después se rechaza su validación; inserted/duplicated corresponden a rangos aceptados. No inferir duplicados restando contadores de un trabajo fallido.
- El worker registra plan, inicio/fin, estado, contadores y errores controlados. Los detalles privados del servidor no se serializan.
- El bloqueo advisory usa la URL biométrica y coordina los procesos de **esta misma instancia PostgreSQL**. No coordina bases separadas: antes de un corte real debe desactivarse el sincronizador anterior o acordar exclusión en el servicio biométrico.

La respuesta HTTP recibida por RRHH está filtrada. El código local de Biometric API todavía lee el histórico físico completo antes de filtrar/paginar en cada petición. COMPLETE significa cierre operativo después de una consulta exitosa bajo esta política, no certificación de que el equipo conserve absolutamente todo su histórico. El cliente nuevo rechaza cambios del total durante paginación; el servidor aún no ofrece snapshot/export estable.

El scheduler es opt-in: propone AYER→HOY al vencer el intervalo y deja al planner omitir AYER si ya está COMPLETE. No duplica un trabajo activo. Los huecos anteriores se resuelven con un rango explícito. No se configuró cron final.

## RAW, vínculo y deduplicación

`record_id = SHA256(JSON compacto UTF-8 de [device_id,user_id,timestamp,status,punch])`. Timestamp local con segundos; sin uid, sin conversión UTC, sin interpretar status/punch. Un `ON CONFLICT DO NOTHING` conserva originales. Los lotes se insertan de 1000 en 1000, dentro de la transacción del rango.

Se preservan fecha/hora y códigos RAW. Un trigger PostgreSQL rechaza UPDATE de evidencia RAW y DELETE de marcaciones. La asignación `funcionario_id_external` es enriquecimiento separado: puede cambiar solo por el endpoint explícito de vínculo, que registra valor anterior, nuevo actor y fecha en `biometric_link_audits`. No hay coincidencia automática por CI; el enlace exacto se confirma con el maestro remoto. Importar archivo normalizado no declara cobertura COMPLETE.

## Horarios, parámetros y evaluación histórica

Turnos y segmentos tienen vigencias inclusivas. Segmentos declaran roles de entrada/salida y ventanas explícitas. No hay horarios productivos 08–12/14–18 por defecto: esos valores aparecen únicamente en fixtures.

Las asignaciones usan UUID externo y fechas; no existe un `current_shift_id` ni FK externa. No se aceptan superposiciones mediante API. El calendario confirma cada fecha laborable/no laborable; no se deducen fines de semana ni feriados.

Los seis parámetros permanecen en segundos y ahora tienen revisiones por vigencia. `PUT /rrhh/attendance/parameters` requiere `valid_from` y admite `valid_to`. Las revisiones no se sobrescriben ni se superponen. Para un cambio futuro de una revisión/asignación abierta, usar su endpoint `/close` con un cierre desde HOY y crear una nueva vigencia desde el día siguiente. No se permite recortar fechas pasadas. Los turnos/segmentos son creaciones de versiones nuevas, sin PUT destructivo.

Cada día del reporte incluye `parameters_revision_id` y `parameters_snapshot`. Cuando el período tiene varias revisiones, `parameters` global es null; no se presenta la configuración actual como si fuera histórica. Los segmentos y la asignación se seleccionan por la fecha evaluada.

`attendance_policies` es alcance/regla propia de RRHH, no maestro de contratos. No se cargan tipos contractuales ni población por defecto. Un administrador solo puede confirmar semántica/población con una referencia institucional explícita y UUIDs externos validados. El indicador `period_semantics_confirmed` confirma concretamente el cálculo existente de mes nominal terminado en el corte; otra semántica necesita definición antes de activarlo. No existe todavía endpoint remoto que pruebe asignación contractual: debe resolverse institucionalmente.

Sin esas decisiones, calendario, turno o parámetros, `ready=false` y `pending_configuration` explica el motivo. Cobertura no cerrada conserva el análisis disponible, pero añade dependencia pendiente. La tolerancia de salida continúa PENDING; no se habilitaron interpretaciones nuevas no confirmadas. Ausencia, extras, licencias y permisos no se inventan; NO_DATA no implica ausencia. Los turnos nocturnos se almacenan, pero no se evalúan silenciosamente: devuelven `overnight_shift_policy` pendiente.

Generar y reportar usan exclusivamente PostgreSQL para las marcas; solo consultan Correspondencia para datos maestros/identidad. No llaman a Biometric API ni encolan descargas automáticamente. La cobertura disponible puede revisarse con el planner; la sincronización debe solicitarse explícitamente o por scheduler aprobado. Los nombres/área/cargo de la cabecera reflejan el maestro remoto actual; no son snapshots de personal histórico.

## Endpoints

```text
GET  /health                       GET /openapi.json
GET  /auth/me                      identidad delegada; no cuentas locales
POST /auth/login                   relay a Correspondencia
POST /auth/refresh                 misma sesión remota con rotación
POST /auth/logout                  revocación remota y borrado de cookie
GET  /rrhh/capabilities            mapa funcional de permisos
GET  /admin/employees[/{id}]       fachada de lectura
GET  /admin/organizational-units[/{id}]
GET  /admin/positions[/{id}]
GET  /rrhh/biometric/status        GET /rrhh/biometric/users
POST /rrhh/biometric/sync          GET /rrhh/biometric/sync/plan
GET  /rrhh/biometric/syncs         GET /rrhh/biometric/syncs/latest
GET  /rrhh/biometric/syncs/{id}     GET /rrhh/biometric/coverage
PUT  /rrhh/biometric/devices/{device}/links/{user}
POST /rrhh/biometric/import        GET /rrhh/biometric/records/summary
GET  /rrhh/attendance/employees/{id}/records
GET  /rrhh/attendance/parameters   PUT /rrhh/attendance/parameters
GET  /rrhh/attendance/parameters/revisions
POST /rrhh/attendance/parameters/revisions/{id}/close
POST /rrhh/attendance/policies     GET /rrhh/attendance/contracts
POST /rrhh/attendance/generate
GET  /rrhh/attendance/employees/{id}/report
GET  /rrhh/work-shifts             POST /rrhh/work-shifts
POST /rrhh/employee-shift-assignments
GET  /rrhh/employee-shift-assignments/{employee_id}
POST /rrhh/employee-shift-assignments/{assignment_id}/close
POST /rrhh/calendar
```

Flutter ya está adaptado a esta fachada única: login/refresh/logout delegados, mismo JWT, perfil y parámetros con vigencia. Las escrituras de maestros permanecen en Correspondencia y no se anuncian en el perfil público RRHH. Ver `docs/INTEGRACION_REAL_Y_FLUTTER.md` para contratos, cookies, resultados y pendientes.

En la primera versión, todos los usuarios activos de tipo/rol GESTOR de Correspondencia tienen acceso base a RRHH. El contrato real usa `roles: ["MANAGER"]`: BASE_ROLES de Correspondencia define MANAGER con nombre Gestor. Entrada y lecturas no exigen `rrhh.read`. No se identifica al Gestor por cargo ni por permisos. Las operaciones administrativas siguen exigiendo acceso base y `rrhh.manage`; force requiere además ADMIN/SUPERADMIN o `rrhh.resync`. ADMIN sin MANAGER no concede acceso al módulo. No se modifican usuarios/roles/grants de Correspondencia. Ver `docs/CONTRATOS_CORRESPONDENCIA.md`.

## Pruebas aisladas

Crear una base vacía exclusiva para tests con nombre `rrhh_api_test_*`, usuario local propio y host loopback. El helper rechaza otra URL; sus fixtures truncan únicamente tablas de esa base descartable. Nunca ejecutar contra QA o datos de trabajo.

```powershell
$env:RRHH_TEST_DATABASE_URL='postgresql+psycopg://rrhh_test@127.0.0.1:55449/rrhh_api_test_final_20261116'
.\scripts\test_local.ps1
```

La entrega creó su propio clúster temporal en `.local/postgres`, puerto 55449, usuario de pruebas `rrhh_test`, autenticación trust solo para ese entorno loopback. No se reutilizó la DB de Correspondencia. Para repetir usando ese clúster, iniciarlo con `pg_ctl` de la instalación local; el helper no inicia ni detiene servidores ajenos.

`tests/smoke_app.py` es un entrypoint solo de pruebas con actores/maestros simulados y comprobación de base descartable. Nunca usarlo en despliegue; la imagen solo copia `app/`. La aplicación de producción siempre es `app.main:app`.

La fase posterior sí validó Biometric API real: 572 marcas del 11/11/2026 en una DB RRHH local exclusiva. Correspondencia se ejecutó con DB descartable y personas fixture autorizadas. Reporte idéntico con Biometric API caída. Evidencia y limitaciones: `docs/INTEGRACION_REAL_Y_FLUTTER.md`. No publicar `.local/` ni usar estas bases/fixtures como producción.
