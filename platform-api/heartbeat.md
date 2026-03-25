# Pleng Heartbeat

Cada sección define un nivel de check. monitor.py lee este archivo
y programa los checks automáticamente. El agente ejecuta los comandos
y reporta por Telegram.

## quick | 30m

Ejecuta:
1. `pleng docker-ps`
2. `pleng system`

Si TODO está normal (containers running, RAM <90%, disco <85%, load razonable),
responde SOLO con: "OK"

Si algo está mal, explica qué en 1-2 líneas y recomienda acción.
Sé extremadamente conciso. Esto se ejecuta cada 5 minutos.
Responde en español.

## deep | 60m

Ejecuta:
1. `pleng docker-ps`
2. `pleng docker-stats`
3. `pleng system`
4. `pleng errors --minutes 30`
5. `pleng logs-summary`

Analiza uso de recursos por container, errores recientes, y anomalías en logs.
Da un resumen de 2-3 líneas. Si hay algo inusual, detalla qué y por qué.
Si todo está bien, confirma con un resumen breve.
Responde en español.

## full | 120m

Ejecuta:
1. `pleng health-report`

Revisa a fondo: recursos del sistema, estado de todos los containers,
errores de Traefik, logs de todos los sites desplegados.

Da un informe completo del estado del sistema.
Incluye tendencias si notas patrones (ej: memoria subiendo, errores recurrentes).
Responde SIEMPRE aunque todo esté bien — esto es la auditoría completa.
Responde en español.
