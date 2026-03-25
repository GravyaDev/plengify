# Pleng Heartbeat

Each section defines a check level. monitor.py reads this file
and schedules checks automatically. The agent runs the commands
and reports via Telegram.

## quick | 30m

Run:
1. `pleng docker-ps`
2. `pleng system`

If everything is normal (containers running, RAM <90%, disk <85%, reasonable load),
respond ONLY with: "OK"

If something is wrong, explain what in 1-2 lines and recommend an action.
Be extremely concise. This runs every 5 minutes.

## deep | 60m

Run:
1. `pleng docker-ps`
2. `pleng docker-stats`
3. `pleng system`
4. `pleng errors --minutes 30`
5. `pleng logs-summary`

Analyze resource usage per container, recent errors, and log anomalies.
Give a 2-3 line summary. If something is unusual, detail what and why.
If everything is fine, confirm with a brief summary.

## full | 120m

Run:
1. `pleng health-report`

Review in depth: system resources, all container states,
Traefik errors, logs from all deployed sites.

Give a complete system status report.
Include trends if you notice patterns (e.g., memory rising, recurring errors).
ALWAYS respond even if everything is fine — this is the full audit.
