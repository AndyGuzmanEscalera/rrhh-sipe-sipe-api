# Informe de implementación de la API RRHH independiente

Fecha: 16 de noviembre de 2026. Proyecto nuevo: `C:\dev\rrhh_sipe_sipe_api`.

## 1. Estructura creada

`app/core`, `db`, `external/correspondencia`, `biometric/api_client`, `attendance`, `shifts`, `reports`, `tools`; `alembic/versions`, `tests`, `scripts`, `docs`; README, configuración, Dockerfile y Compose. `app.main:app` es el entrypoint de producción. La normalización se separó del lector y no se copió el driver ZKTeco.

## 2. Tablas creadas

Doce tablas propias, además de `alembic_version`:

| Tabla | PK y referencias |
|---|---|
| biometric_devices | device_id texto, remote_device_id y metadata; sin IP ni FK externa |
| biometric_employee_links | PK device_id + biometric_user_id; UUID funcionario/actor sin FK externa |
| biometric_link_audits | UUID; actor y empleado anterior/nuevo externos; FK local a dispositivo |
| biometric_records | UUID; record_id único; FK local a dispositivo; funcionario externo nullable |
| biometric_syncs | UUID; FK local a dispositivo; actor externo nullable; índice único parcial de trabajo activo |
| biometric_sync_coverage | UUID; unique dispositivo/fecha; FKs locales a trabajos de intento/éxito |
| work_shifts | UUID; vigencia, estado, descripción y ventanas |
| work_shift_segments | UUID; FK local turno; secuencia/roles/horas/vigencia |
| employee_shift_assignments | UUID; FK local turno; funcionario y actor externos; vigencia |
| attendance_calendar_days | UUID; unique funcionario externo/fecha; condición laborable explícita |
| attendance_parameters | UUID por revisión; seis parámetros en segundos y vigencia; actor externo |
| attendance_policies | UUID por revisión; referencia contractual externa, ámbito confirmado y vigencia |

No existen tablas locales `users`, `employees`, `positions` u `organizational_units`, ni FKs hacia Correspondencia. Se validó contra el catálogo real de PostgreSQL descartable.

## 3. Migración Alembic

Historia nueva: `0001_rrhh`, `down_revision=None`, en `alembic/versions/0001_rrhh_initial_rrhh_owned_tables.py`. Incluye tablas, índices/checks y trigger de inmutabilidad RAW; no semillas de usuarios, contratos, horarios o permisos. No se copió ningún historial de Correspondencia ni se cambió su historia `h1/m1/n1`.

## 4. Resultado upgrade head

Se ejecutó desde cero en bases nuevas de un clúster temporal propio, loopback 127.0.0.1:55449. Validación final: `rrhh_api_test_final_20261116`; aceptación HTTP con la migración final: `rrhh_api_test_acceptance_20261116`. También se usaron bases descartables de desarrollo `rrhh_api_test_20261116` y `rrhh_api_test_smoke_20261116`, sin datos reales. `alembic current`: **0001_rrhh (head)**. `alembic check`: **No new upgrade operations detected**. No se aplicó a ninguna base real.

## 5. Endpoints RRHH

OpenAPI final contiene **32 paths**. Incluye estado/usuarios biométricos, sync/plan/jobs/latest/cobertura, vínculo explícito, importación normalizada, resumen/consulta de marcas; parámetros versionados y cierre futuro, políticas, catálogo contractual confirmado, generate, reporte; horarios, asignaciones y calendario; fachadas de lectura de maestros y `/auth/me`. Lista completa en README. Health y documentación no requieren token; negocio siempre autenticado.

## 6. Módulos reutilizados

Motor `evaluation.py`, DTOs `evaluation_models.py`, `periods.py`, validadores de entrada y canonicalización de `reader/records.py`. Se conservaron reglas de secuencia, tolerancias conocidas, ambigüedades, extras y sugerencias sin eliminar originales. Se portaron sus pruebas puras. Se reutilizó el cliente HTTP biométrico validado.

## 7. Módulos adaptados

Servicio/repository de asistencia usan modelos locales, DTOs de maestros HTTP y valores por fecha. Worker conserva cola durable y transacciones, incorpora planner/cobertura. Servicio biométrico conserva serialización por advisory lock, exclusivamente HTTP. Cliente ahora exige HTTPS, no sigue redirects y rechaza paginación inconsistente o cambio de total. Se añadió snapshot/revisión de parámetros por día. No se copiaron `reader/connection.py`, `monitor.py`, `config.py` ni dependencias pyzk/future.

## 8. Cliente Correspondencia

`app/external/correspondencia/{client,schemas,config,errors,router}.py`: empleado individual/paginado, CI exacto recorriendo páginas e incluyendo inactivos, área/cargo/usuario por UUID, roles y actor. Timeouts y errores controlados, TLS normal, sin redirects. Configuración `CORRESPONDENCIA_API_URL/TOKEN/TIMEOUT`; sin SQL ni imports de la aplicación maestra.

## 9. Endpoints existentes encontrados

GET `/admin/employees`, `/admin/employees/{id}`, `/admin/organizational-units[/{id}]`, `/admin/positions[/{id}]`, `/admin/users[/{id}]`, `/admin/roles`, `/auth/me`; POST login/refresh/logout ya existen en el maestro. Contratos, suficiencia y permisos se documentan en `docs/CONTRATOS_CORRESPONDENCIA.md`.

## 10. Endpoints/mecanismos que faltan

No se identificó consulta institucional de asignación contractual, token de servicio dedicado con renovación, lookup CI exacto dedicado ni batch de funcionarios por UUID. CI exacto tiene alternativa cliente segura. La configuración contractual productiva sigue pendiente; no se añadieron rutas, cuentas ni grants a Correspondencia.

## 11. Auth provisional implementada

Validación delegada del Bearer del solicitante por `/auth/me`; permisos efectivos devueltos por el maestro. Token técnico opcional solo para lectura de maestros, no para suplantar identidad del solicitante. Si está vacío, se delega el Bearer del actor para esas consultas. Sin login, passwords, JWT firmados o refresh almacenados en RRHH. Fixtures de actores existen solo en tests. Falta acordar login/cookies/renovación con Flutter y mecanismo técnico definitivo.

## 12. Horarios

Turnos y segmentos con fechas; roles de eventos y ventanas en DB. No hay horarios institucionales inventados. El ejemplo 08–12 / 14–18 se creó solo en fixtures. Nocturnos almacenables pero pendientes de política de evaluación cruzando día. El estado de catálogo no borra una vigencia histórica.

## 13. Parámetros

Seis valores originales en segundos, por revisión con `valid_from/valid_to`. Salida anticipada mantiene semántica PENDING. API rechaza superposición y reemplazo histórico; cierre de revisión solo desde HOY para cambios futuros. El PUT requiere fechas, diferencia deliberada frente a Flutter actual. Reporte por día contiene revisión/snapshot; varias revisiones no se resumen falsamente en una configuración global.

## 14. Asignaciones

Funcionario UUID externo + turno local + vigencia + actor externo. Consulta histórica por fecha, validación de rango de turno, rechazo de superposiciones bajo bloqueo transaccional. Endpoint de cierre permite terminar una asignación abierta desde HOY sin modificar sus fechas pasadas.

## 15. biometric_records

Guarda identidad, device/user externos, timestamp local y fecha/hora, códigos RAW, uid, source y tiempos de sincronización/creación. El UUID externo del funcionario es enriquecimiento nullable, sin FK. Trigger PostgreSQL bloquea modificación de campos RAW y DELETE; el vínculo explícito cambia solo el enriquecimiento y registra auditoría.

## 16. biometric_syncs

Cola PostgreSQL con queued/running/completed/failed, rangos pedidos y planificados, force, actor externo, fechas, contadores y errores sanitizados. Un trabajo activo por dispositivo y bloqueo por URL biométrica. Recuperación de running interrumpido sin repetir silenciosamente un trabajo ya iniciado. Rangos confirmados conservan su commit si otro rango posterior falla.

## 17. biometric_sync_coverage

Una fila por dispositivo/día: OPEN, COMPLETE o FAILED; cantidad almacenada, intento/éxito, contador de intentos, error y timestamps. Una importación de archivo no confirma cobertura. COMPLETE es cierre técnico según la política definida, no prueba de ausencia ni certificación del archivo físico completo del equipo.

## 18. SyncPlanner

Función pura con fecha actual inyectable: recorre días inclusivos, omite COMPLETE pasados salvo force, selecciona OPEN/FAILED/faltantes/HOY y consolida consecutivos. Valida máximo 367 días, orden y ausencia de futuro. Worker recalcula el plan al tomar la cola, sin depender de un last_synced_date global.

## 19. Día actual

Siempre reconsultable y OPEN tras éxito. Al día siguiente se vuelve a leer y solo entonces se cierra. Se usa America/La_Paz configurable. Un trabajo que atraviesa medianoche conserva de forma conservadora la clasificación tomada al iniciarse; no cierra un día leído antes de terminar.

## 20. Días COMPLETE

Se omiten en sincronización ordinaria. Un rango totalmente COMPLETE termina sin peticiones de attendance. Su cobertura se invalida a OPEN durante un force; si ese intento falla, queda FAILED para reintentar, manteniendo marcas y referencia al último éxito.

## 21. Huecos y FAILED

Prueba 01–10 COMPLETE, 11–12 FAILED, 13–20 COMPLETE y petición 01–30: descargas exactamente 11–12 y 21–30; 30 OPEN si es HOY. Los días omitidos no incrementan attempt_count. Timeout/error/validación incompleta no declara COMPLETE el rango.

## 22. Force resync

Mismo endpoint con `force=true`, requiere rrhh.manage y administrador o rrhh.resync. Usa el mismo worker/persistencia; no ruta paralela ni SQL de eliminación. Todos los días del rango se vuelven a pedir, incluidos COMPLETE.

## 23. Deduplicación

SHA-256 de JSON compacto `[device_id,user_id,timestamp,status,punch]`, UTF-8, precisión segundos, sin uid. On conflict por record_id ignora repetidos. Resync con una marca adicional inserta solo esa marca. device_id debe coincidir con el remoto; no se reinterpretan códigos desconocidos.

## 24. RAW vs EVALUATED

Motor puro recalculable sobre marcas locales inmutables. Incidencias y sugerencias son DTOs, no reemplazan registros. Reportes no llaman Biometric API. `ready=false` conserva decisiones faltantes; horarios, parámetros, calendario y alcance se resuelven por fecha. Cabeceras de personal se consultan del maestro actual y no se presentan como snapshots históricos de personal.

## 25–26. Pruebas ejecutadas y resultados

**114 aprobadas, 0 fallidas** en la suite final. Incluye motor portado; 1–7 marcas; RAW desconocido/duplicados/extras/ambigüedad; planner; sincronización nueva, caché, force, dedup y marca nueva; errores/timeout/autorización/rango incorrecto; recuperación y éxito parcial; actor/maestros HTTP simulados; CI paginado exacto; aislamiento de auth y DB; turnos/parámetros históricos; cierre futuro de parámetros; guard SQL RAW; startup/OpenAPI y health.

Se corrigieron fallos detectados en la primera ejecución del código nuevo (alias ORM de metadata y preparación de entorno de tests) antes de la validación final. No se modificó código funcional de Correspondencia para corregirlos. Queda un aviso de deprecación existente de Starlette/AnyIO del entorno instalado. Compilación Python correcta. `scripts/test_local.ps1` permite repetir upgrade/check/tests con guard de base descartable loopback.

## 27. Docker / Compose

Dockerfile Python 3.12, usuario sin privilegios, app/migraciones propias; Compose con api, worker y postgres:16-alpine, volumen exclusivo y API en loopback. Configuración Compose validada. No se construyó ni arrancó imagen: Docker Engine local no estaba disponible. No se inició Docker Desktop para evitar activar servicios ajenos automáticamente.

## 28. Startup/OpenAPI y prueba HTTP local

FastAPI inició realmente con Uvicorn en 127.0.0.1:8201 usando entrypoint **solo de tests**, actor/maestros ficticios y DB smoke. Health/OpenAPI/generate/reporte/planner respondieron 200. Fixture: 8 marcas, dos horarios/versiones históricas y 300 segundos de atraso; generate y reporte coincidieron. Planner de ese período COMPLETE devolvió rangos vacíos. Worker real `--once` inició y terminó con cola vacía, sin crear cliente biométrico. La suite comprueba el worker con trabajos reales PostgreSQL y transporte remoto simulado. OpenAPI final tiene 32 paths.

## 29. Validación real

No se ejecutó la opción de consulta real a Biometric API. No hubo tráfico hacia Correspondencia ni el dispositivo. Las únicas llamadas HTTP de aceptación fueron loopback y dobles MockTransport; no se usaron credenciales reales ni se importaron datos reales.

## 30. Secretos

No se copiaron `.env` reales ni secretos. Ejemplo contiene token vacío y password `REPLACE`; tests contienen identificadores/credenciales ficticios. El bearer no aparece en errores. `.env`, `.venv`, `.local`, caches, logs, DB y cobertura están excluidos de Git y Docker build.

## 31. Git y preservación

Repositorio inicializado localmente, sin commits, remoto ni push. Archivos nuevos sin versionar. Correspondencia se consultó solo en lectura; su `.wrangler/` previo no se alteró. Flutter conservó sus tres archivos modificados previos. Biometric API no se modificó. Los servidores y datos temporales pertenecen exclusivamente al proyecto nuevo; al finalizar se detuvieron Uvicorn y PostgreSQL temporales. Los datos de fixtures quedaron únicamente bajo `.local/`, excluidos de Git.

## 32. Porcentaje aproximado

Separación de código y persistencia implementada: no hay imports, sesiones o FKs del backend anterior. Estimación de preparación integral para la transición: **85%**, no una métrica automática. Falta integrar identidad/maestros reales con mecanismo aprobado, ajustar Flutter y ejecutar QA/corte. No se retiró todavía RRHH de Correspondencia.

## 33. Antes de conectar Flutter

Resolver login/refresh/logout y cookies/origen sin duplicar auth; confirmar permisos del Bearer delegado/técnico; agregar selección de vigencia en guardado de parámetros; decidir si el CRUD de maestros seguirá apuntando directamente a Correspondencia. Configurar API_BASE_URL nueva y validar flujo completo. El catálogo contractual permanece vacío hasta configuración institucional confirmada.

## 34. Antes de QA

Construir/ejecutar Docker cuando el daemon esté disponible, provisionar DB RRHH exclusiva y sus secretos, acordar mecanismo técnico y renovación, supervisar worker/scheduler (deshabilitado por defecto), configurar TLS/proxy/CORS y verificar conectividad real. Validar fuentes institucionales, vigencias, calendario y cobertura. Acordar ventana de corte con un único sincronizador y rollback/reconciliación. No desplegar fixtures ni `tests.smoke_app`.

## 35. Riesgos

Dependencia de disponibilidad de Correspondencia para auth/maestros; token manual expirará si se usa como solución permanente; API biométrica sin snapshot estable y con lectura física completa por página; advisory locks no coordinan PostgreSQL separados; cambios manuales SQL de vigencias fuera de los endpoints pueden violar políticas; nocturnos/ausencias/licencias/salida anticipada siguen pendientes; nombres/área/cargo del reporte son los actuales del maestro. Ninguno se ocultó forzando ready=true.

## 36. Conclusión

**LISTO PARA SIGUIENTE FASE: integración autenticada con Correspondencia de pruebas y adaptación controlada de Flutter.** No listo para desplegar directamente a QA/producción ni para retirar el módulo anterior. Mantener Correspondencia intacta hasta validar el flujo nuevo con configuración institucional y un plan de corte.
