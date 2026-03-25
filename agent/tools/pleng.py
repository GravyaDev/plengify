#!/usr/bin/env python3
"""pleng — CLI tool for managing deploys from inside the agent container.

Usage:
    pleng sites                         List all sites
    pleng deploy <path> --name <name>   Deploy a docker-compose project
    pleng deploy-git <url> --name <n>   Deploy from a git repo
    pleng logs <name> [--lines N]       Show Docker logs
    pleng status <name>                 Show container status
    pleng stop <name>                   Stop a site
    pleng restart <name>                Restart a site
    pleng remove <name>                 Remove a site
    pleng promote <name> --domain <d>   Promote to production with custom domain
    pleng system                        System stats (CPU, RAM, disk, load)
    pleng docker-ps                     All Docker containers
    pleng docker-stats                  CPU/RAM per container
    pleng errors [--minutes 60]         Recent Traefik 5xx errors
    pleng logs-summary                  Recent errors from all sites
    pleng health-report                 Full system health report
    pleng chat                          Interactive chat (for terminal use)
"""
import json
import os
import sys

import requests

API = os.environ.get("PLATFORM_API_URL", "http://platform-api:8000")
API_KEY = os.environ.get("PLENG_API_KEY", "")


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if API_KEY:
        h["X-API-Key"] = API_KEY
    return h


def _get(path: str) -> dict:
    try:
        r = requests.get(f"{API}{path}", headers=_headers(), timeout=30)
        if r.status_code >= 400:
            try:
                return r.json()
            except Exception:
                return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return r.json()
    except requests.ConnectionError:
        return {"error": "Cannot connect to platform-api. Is it running?"}
    except Exception as e:
        return {"error": str(e)}


def _post(path: str, data: dict = None) -> dict:
    try:
        r = requests.post(f"{API}{path}", json=data or {}, headers=_headers(), timeout=300)
        if r.status_code >= 400:
            try:
                return r.json()
            except Exception:
                return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return r.json()
    except requests.ConnectionError:
        return {"error": "Cannot connect to platform-api. Is it running?"}
    except Exception as e:
        return {"error": str(e)}


def cmd_sites():
    sites = _get("/api/sites")
    if not sites:
        print("No sites deployed.")
        return
    for s in sites:
        status = s["status"]
        domain = s.get("production_domain") or s.get("staging_domain") or ""
        url = f"https://{domain}" if s.get("production_domain") else f"http://{domain}" if domain else ""
        print(f"  {status:<12} {s['name']:<20} {url}")


def cmd_deploy(args: list[str]):
    name = _flag(args, "--name") or _flag(args, "-n")
    path = args[0] if args and not args[0].startswith("-") else "."

    if not name:
        print("Error: --name required")
        sys.exit(1)

    # Resolve to absolute path
    abs_path = os.path.abspath(path)

    # If it's a directory, look for docker-compose.yml
    compose_path = abs_path
    if os.path.isdir(abs_path):
        compose_file = os.path.join(abs_path, "docker-compose.yml")
        if os.path.exists(compose_file):
            compose_path = compose_file
        else:
            compose_path = abs_path  # deploy_compose handles directories too

    result = _post("/api/deploy/compose", {"name": name, "compose_path": compose_path})
    _print_result(result)


def cmd_deploy_git(args: list[str]):
    url = args[0] if args and not args[0].startswith("-") else ""
    name = _flag(args, "--name") or _flag(args, "-n")
    branch = _flag(args, "--branch") or "main"

    if not url or not name:
        print("Error: pleng deploy-git <url> --name <name>")
        sys.exit(1)

    result = _post("/api/deploy/git", {"name": name, "repo_url": url, "branch": branch})
    _print_result(result)


def cmd_logs(args: list[str]):
    name = args[0] if args else ""
    lines = int(_flag(args, "--lines") or "100")
    if not name:
        print("Error: pleng logs <name>")
        sys.exit(1)
    data = _get(f"/api/sites/{name}/logs?lines={lines}")
    print(data.get("logs", "No logs"))


def cmd_status(args: list[str]):
    name = args[0] if args else ""
    if not name:
        print("Error: pleng status <name>")
        sys.exit(1)
    site = _get(f"/api/sites/{name}")
    if "error" in site or "detail" in site:
        print(f"Not found: {name}")
        return
    print(f"Name:       {site['name']}")
    print(f"Status:     {site['status']}")
    print(f"Staging:    http://{site.get('staging_domain', 'N/A')}")
    print(f"Production: https://{site.get('production_domain', 'N/A')}")
    print(f"Created:    {site.get('created_at', '')}")
    print(f"Mode:       {site.get('deploy_mode', '')}")

    containers = _get(f"/api/sites/{name}/containers")
    if containers:
        print(f"\nContainers:")
        for c in containers:
            print(f"  {c.get('Name', '?')}: {c.get('State', '?')}")


def cmd_redeploy(args: list[str]):
    name = args[0] if args else ""
    if not name:
        print("Error: pleng redeploy <name>"); sys.exit(1)
    result = _post(f"/api/sites/{name}/redeploy")
    _print_result(result)


def cmd_stop(args: list[str]):
    name = args[0] if args else ""
    if not name:
        print("Error: pleng stop <name>"); sys.exit(1)
    result = _post(f"/api/sites/{name}/stop")
    print("Stopped" if result.get("ok") else f"Failed: {result}")


def cmd_restart(args: list[str]):
    name = args[0] if args else ""
    if not name:
        print("Error: pleng restart <name>"); sys.exit(1)
    result = _post(f"/api/sites/{name}/restart")
    print("Restarted" if result.get("ok") else f"Failed: {result}")


def cmd_remove(args: list[str]):
    name = args[0] if args else ""
    if not name:
        print("Error: pleng remove <name>"); sys.exit(1)
    result = _post(f"/api/sites/{name}/remove")
    if result.get("ok"):
        if result.get("kept_files"):
            print(f"Removed (production — files kept, containers stopped)")
        else:
            print("Removed (staging — everything deleted)")
    else:
        print(f"Failed: {result}")


def cmd_destroy(args: list[str]):
    name = args[0] if args else ""
    confirm = _flag(args, "--confirm")
    if not name:
        print("Error: pleng destroy <name> --confirm yes"); sys.exit(1)
    if confirm != "yes":
        print("Error: pleng destroy requires --confirm yes (permanently deletes everything)")
        sys.exit(1)
    result = _post(f"/api/sites/{name}/destroy")
    print("Destroyed permanently" if result.get("ok") else f"Failed: {result}")


def cmd_promote(args: list[str]):
    name = args[0] if args else ""
    domain = _flag(args, "--domain") or _flag(args, "-d")
    if not name or not domain:
        print("Error: pleng promote <name> --domain <domain>"); sys.exit(1)
    result = _post(f"/api/sites/{name}/promote", {"domain": domain})
    _print_result(result)


def cmd_system():
    """System stats: disk, memory, load, uptime."""
    data = _get("/internal/system-stats")
    if "error" in data:
        print(f"Error: {data['error']}"); return

    disk = data.get("disk", {})
    mem = data.get("memory", {})
    load = data.get("load", {})

    print("=== System Stats ===")
    print(f"\nDisk:    {disk.get('used', '?')} / {disk.get('total', '?')} ({disk.get('percent', '?')})")
    print(f"Memory:  {mem.get('used_mb', '?')}MB / {mem.get('total_mb', '?')}MB (available: {mem.get('available_mb', '?')}MB)")
    print(f"Load:    {load.get('1m', '?')} / {load.get('5m', '?')} / {load.get('15m', '?')}")
    print(f"Uptime:  {data.get('uptime', '?')}")


def cmd_docker_ps():
    """List all Docker containers on the host."""
    data = _get("/internal/docker-ps")
    if isinstance(data, dict) and "error" in data:
        print(f"Error: {data['error']}"); return
    if not data:
        print("No containers running."); return

    print(f"{'NAME':<35} {'STATE':<12} {'STATUS':<25} {'IMAGE'}")
    print("-" * 100)
    for c in data:
        print(f"{c.get('Names', '?'):<35} {c.get('State', '?'):<12} {c.get('Status', '?'):<25} {c.get('Image', '?')}")


def cmd_docker_stats():
    """CPU and RAM per running container."""
    data = _get("/internal/docker-stats")
    if isinstance(data, dict) and "error" in data:
        print(f"Error: {data['error']}"); return
    if not data:
        print("No containers running."); return

    print(f"{'NAME':<35} {'CPU':<10} {'MEM USAGE':<25} {'MEM %'}")
    print("-" * 80)
    for c in data:
        print(f"{c.get('Name', '?'):<35} {c.get('CPUPerc', '?'):<10} {c.get('MemUsage', '?'):<25} {c.get('MemPerc', '?')}")


def cmd_errors(args: list[str]):
    """Recent Traefik 5xx errors."""
    minutes = int(_flag(args, "--minutes") or "60")
    data = _get(f"/internal/traefik-errors?minutes={minutes}")
    if isinstance(data, dict) and "error" in data:
        print(f"Error: {data['error']}"); return

    print(f"=== Traefik Errors (last {minutes} min) ===")
    print(f"Total requests: {data.get('total_requests', 0)}")
    print(f"5xx errors:     {data.get('errors_5xx', 0)}")
    print(f"Error rate:     {data.get('error_rate', '0%')}")

    by_domain = data.get("by_domain", {})
    if by_domain:
        print(f"\nBy domain:")
        for domain, count in sorted(by_domain.items(), key=lambda x: -x[1]):
            print(f"  {domain}: {count}")

    recent = data.get("recent_errors", [])
    if recent:
        print(f"\nRecent errors (last {len(recent)}):")
        for e in recent[-10:]:
            print(f"  [{e.get('time', '?')}] {e.get('status')} {e.get('domain', '?')}{e.get('path', '')}")


def cmd_logs_summary():
    """Recent errors from all deployed sites."""
    data = _get("/internal/logs-summary")
    if isinstance(data, dict) and "error" in data:
        print(f"Error: {data['error']}"); return
    if not data:
        print("No errors found in any site logs."); return

    for site_name, errors in data.items():
        print(f"\n=== {site_name} ({len(errors)} errors) ===")
        for line in errors[-10:]:
            print(f"  {line}")


def cmd_health_report():
    """Full system health report — calls all observability endpoints."""
    print("=" * 60)
    print("  PLENG HEALTH REPORT")
    print("=" * 60)

    # System stats
    print("\n--- System Resources ---")
    cmd_system()

    # Docker containers
    print("\n--- Docker Containers ---")
    cmd_docker_ps()

    # Docker resource usage
    print("\n--- Container Resources ---")
    cmd_docker_stats()

    # Sites
    print("\n--- Deployed Sites ---")
    cmd_sites()

    # Traefik errors
    print("\n--- Traefik Errors (last 60 min) ---")
    cmd_errors(["--minutes", "60"])

    # Site log errors
    print("\n--- Site Log Errors ---")
    cmd_logs_summary()

    print("\n" + "=" * 60)
    print("  END OF REPORT")
    print("=" * 60)


def cmd_chat():
    """Interactive chat mode for terminal use."""
    print("Pleng Agent — type 'exit' to quit, '/new' to reset session\n")
    session = "terminal"
    agent_url = "http://localhost:8000"  # self

    while True:
        try:
            msg = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        if not msg:
            continue
        if msg.lower() == "exit":
            break
        if msg == "/new":
            requests.post(f"{agent_url}/chat/reset", json={"session_id": session}, timeout=5)
            print("Session reset.\n")
            continue

        try:
            r = requests.post(f"{agent_url}/chat",
                              json={"message": msg, "session_id": session}, timeout=600)
            data = r.json()
            print(f"\nPleng: {data.get('response', 'No response')}\n")
        except Exception as e:
            print(f"\nError: {e}\n")


def _flag(args: list[str], flag: str) -> str | None:
    try:
        idx = args.index(flag)
        return args[idx + 1] if idx + 1 < len(args) else None
    except ValueError:
        return None


def _print_result(result: dict):
    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"Site:   {result.get('name', '?')}")
        print(f"Status: {result.get('status', '?')}")
        if result.get("url"):
            print(f"URL:    {result['url']}")
        if result.get("domain"):
            print(f"Domain: {result['domain']}")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]
    rest = args[1:]

    commands = {
        "sites": lambda: cmd_sites(),
        "deploy": lambda: cmd_deploy(rest),
        "deploy-git": lambda: cmd_deploy_git(rest),
        "redeploy": lambda: cmd_redeploy(rest),
        "logs": lambda: cmd_logs(rest),
        "status": lambda: cmd_status(rest),
        "stop": lambda: cmd_stop(rest),
        "restart": lambda: cmd_restart(rest),
        "remove": lambda: cmd_remove(rest),
        "destroy": lambda: cmd_destroy(rest),
        "promote": lambda: cmd_promote(rest),
        "system": lambda: cmd_system(),
        "docker-ps": lambda: cmd_docker_ps(),
        "docker-stats": lambda: cmd_docker_stats(),
        "errors": lambda: cmd_errors(rest),
        "logs-summary": lambda: cmd_logs_summary(),
        "health-report": lambda: cmd_health_report(),
        "chat": lambda: cmd_chat(),
    }

    if cmd in commands:
        commands[cmd]()
    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
