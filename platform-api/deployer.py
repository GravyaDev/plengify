"""Docker deploy engine. Manages containers via docker compose CLI.

Every project lives in PROJECTS_DIR/{name or site_id}/ with its own docker-compose.yml.
Pleng generates a separate docker-compose.pleng.yml with Traefik labels and build context fixes.
The user's docker-compose.yml is NEVER modified.
"""
import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import datetime

import yaml

import database as db

logger = logging.getLogger(__name__)

PROJECTS_DIR = os.environ.get("PROJECTS_DIR", "/opt/pleng/projects")
PUBLIC_IP = os.environ.get("PUBLIC_IP", "127.0.0.1")
NETWORK = "pleng_web"


def staging_domain(name: str) -> str:
    h = hashlib.md5(name.encode()).hexdigest()[:4]
    return f"{h}.{PUBLIC_IP}.sslip.io"


# ── Deploy modes ────────────────────────────────────────

def deploy_compose(site_id: str, name: str, compose_source: str) -> dict:
    """Deploy from an existing docker-compose.yml path or directory."""
    if os.path.isdir(compose_source) and os.path.exists(os.path.join(compose_source, "docker-compose.yml")):
        workspace = compose_source
        db.update_site(site_id, project_path=workspace)
    elif os.path.isfile(compose_source):
        workspace = _prepare_workspace(site_id)
        shutil.copy2(compose_source, os.path.join(workspace, "docker-compose.yml"))
    else:
        raise FileNotFoundError(f"Source not found or no docker-compose.yml: {compose_source}")

    return _deploy(site_id, name, workspace)


def deploy_git(site_id: str, name: str, repo_url: str, branch: str = "main") -> dict:
    workspace = _prepare_workspace(site_id)

    token = os.environ.get("GITHUB_TOKEN", "")
    clone_url = repo_url
    if token and "github.com" in repo_url:
        clone_url = repo_url.replace("https://github.com/", f"https://x-access-token:{token}@github.com/")

    result = subprocess.run(
        ["git", "clone", "--depth", "1", "-b", branch, clone_url, workspace],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr[:300]}")

    db.add_site_log(site_id, f"Cloned {repo_url}")
    db.update_site(site_id, github_url=repo_url)

    if not os.path.exists(os.path.join(workspace, "docker-compose.yml")):
        generated = _auto_generate_compose(workspace)
        if generated:
            with open(os.path.join(workspace, "docker-compose.yml"), "w") as f:
                f.write(generated)
            db.add_site_log(site_id, "Auto-generated docker-compose.yml")
        else:
            raise FileNotFoundError("No docker-compose.yml and could not auto-detect project type")

    return _deploy(site_id, name, workspace)


# ── Site operations ─────────────────────────────────────

def redeploy(site_id: str) -> dict:
    site = db.get_site(site_id)
    if not site:
        return {"error": "Site not found"}

    name = site["name"]
    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)

    if not os.path.exists(os.path.join(workspace, "docker-compose.yml")):
        return {"error": f"No docker-compose.yml in {workspace}"}

    domain = site.get("staging_domain") or staging_domain(name)
    _generate_pleng_override(workspace, name, domain)

    db.add_site_log(site_id, "Redeploying...")
    project = f"pleng-{name}"

    result = subprocess.run(
        _compose_cmd(project, workspace, "up", "-d", "--build", "--force-recreate"),
        capture_output=True, text=True, timeout=300,
    )

    if result.returncode != 0:
        error = result.stderr[:500]
        db.add_site_log(site_id, f"Redeploy failed: {error}", level="error")
        return {"error": error}

    _connect_network(project)
    db.update_site(site_id, deployed_at=datetime.utcnow().isoformat())
    db.add_site_log(site_id, "Redeployed")

    url = f"https://{domain}" if site.get("production_domain") else f"http://{domain}"
    return {"site_id": site_id, "name": name, "status": site["status"], "url": url}


def stop(site_id: str) -> bool:
    site = db.get_site(site_id)
    if not site:
        return False
    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)
    r = subprocess.run(_compose_cmd(f"pleng-{site['name']}", workspace, "stop"),
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        db.update_site(site_id, status="stopped")
        db.add_site_log(site_id, "Stopped")
        return True
    return False


def restart(site_id: str) -> bool:
    site = db.get_site(site_id)
    if not site:
        return False
    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)
    r = subprocess.run(_compose_cmd(f"pleng-{site['name']}", workspace, "restart"),
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        db.update_site(site_id, status="staging" if not site.get("production_domain") else "production")
        db.add_site_log(site_id, "Restarted")
        return True
    return False


def remove(site_id: str) -> bool:
    site = db.get_site(site_id)
    if not site:
        return False
    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)
    is_production = site.get("status") == "production"

    subprocess.run(_compose_cmd(f"pleng-{site['name']}", workspace, "down", "-v", "--remove-orphans"),
                   capture_output=True, text=True, timeout=60)

    if is_production:
        db.update_site(site_id, status="removed")
        db.add_site_log(site_id, "Production site removed (files kept)")
    else:
        db.delete_site(site_id)
        # Remove pleng override but keep user files? No — staging deletes all
        if os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)
    return True


def destroy(site_id: str) -> bool:
    site = db.get_site(site_id)
    if not site:
        return False
    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)
    subprocess.run(_compose_cmd(f"pleng-{site['name']}", workspace, "down", "-v", "--remove-orphans"),
                   capture_output=True, text=True, timeout=60)
    db.delete_site(site_id)
    if os.path.exists(workspace):
        shutil.rmtree(workspace, ignore_errors=True)
    return True


def docker_logs(site_id: str, lines: int = 100) -> str:
    site = db.get_site(site_id)
    if not site:
        return "Site not found"
    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)
    r = subprocess.run(_compose_cmd(f"pleng-{site['name']}", workspace, "logs", "--tail", str(lines)),
                       capture_output=True, text=True, timeout=30)
    return r.stdout or r.stderr or "No logs"


def container_status(site_id: str) -> list[dict]:
    site = db.get_site(site_id)
    if not site:
        return []
    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)
    r = subprocess.run(_compose_cmd(f"pleng-{site['name']}", workspace, "ps", "--format", "json"),
                       capture_output=True, text=True, timeout=15)
    containers = []
    for line in (r.stdout or "").strip().split("\n"):
        if line.strip():
            try:
                containers.append(json.loads(line))
            except Exception:
                pass
    return containers


def promote(site_id: str, domain: str) -> dict:
    site = db.get_site(site_id)
    if not site:
        raise ValueError("Site not found")

    workspace = site.get("project_path") or os.path.join(PROJECTS_DIR, site_id)
    name = site["name"]
    staging = site.get("staging_domain") or staging_domain(name)

    # Regenerate override with production labels
    _generate_pleng_override(workspace, name, staging, production_domain=domain)

    project = f"pleng-{name}"
    result = subprocess.run(_compose_cmd(project, workspace, "up", "-d", "--force-recreate"),
                            capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        error = result.stderr[:300]
        db.add_site_log(site_id, f"Promote failed: {error}", level="error")
        raise RuntimeError(f"Promote failed: {error}")

    _connect_network(project)
    db.update_site(site_id, production_domain=domain, status="production")
    db.add_site_log(site_id, f"Promoted: https://{domain}")

    return {"site_id": site_id, "domain": domain, "url": f"https://{domain}", "status": "production"}


# ── Internal ────────────────────────────────────────────

def _prepare_workspace(site_id: str) -> str:
    workspace = os.path.join(PROJECTS_DIR, site_id)
    os.makedirs(workspace, exist_ok=True)
    return workspace


def _compose_cmd(project: str, workspace: str, *args) -> list[str]:
    """Build docker compose command using the pleng-generated compose (not user's)."""
    pleng_compose = os.path.join(workspace, "docker-compose.pleng.yml")
    # Use pleng compose if it exists, otherwise fall back to user's
    compose_file = pleng_compose if os.path.exists(pleng_compose) else os.path.join(workspace, "docker-compose.yml")
    return ["docker", "compose", "-f", compose_file, "-p", project] + list(args)


def _deploy(site_id: str, name: str, workspace: str) -> dict:
    compose_file = os.path.join(workspace, "docker-compose.yml")
    if not os.path.exists(compose_file):
        raise FileNotFoundError("No docker-compose.yml in workspace")

    domain = staging_domain(name)

    # Generate pleng override (traefik labels + absolute build paths)
    # User's docker-compose.yml is NEVER touched
    _generate_pleng_override(workspace, name, domain)

    db.add_site_log(site_id, "Building and starting containers...")
    project = f"pleng-{name}"

    result = subprocess.run(
        _compose_cmd(project, workspace, "up", "-d", "--build"),
        capture_output=True, text=True, timeout=300,
    )

    if result.returncode != 0:
        error = result.stderr[:500]
        db.update_site(site_id, status="error")
        db.add_site_log(site_id, f"Deploy failed: {error}", level="error")
        return {"site_id": site_id, "name": name, "status": "error", "error": error}

    _connect_network(project)

    url = f"http://{domain}"
    db.update_site(
        site_id,
        status="staging",
        staging_domain=domain,
        project_path=workspace,
        deployed_at=datetime.utcnow().isoformat(),
    )
    db.add_site_log(site_id, f"Live at {url}")

    return {"site_id": site_id, "name": name, "status": "staging", "url": url, "domain": domain}


def _generate_pleng_override(workspace: str, name: str, staging_domain: str,
                              production_domain: str = None):
    """Generate docker-compose.pleng.yml — a complete compose based on the user's.

    Reads the user's docker-compose.yml, applies Pleng modifications:
    - Removes host port bindings (Traefik handles routing)
    - Adds Traefik labels
    - Converts relative build paths to absolute
    - Adds pleng_web network

    The user's docker-compose.yml is NEVER modified.
    """
    user_compose_file = os.path.join(workspace, "docker-compose.yml")
    with open(user_compose_file) as f:
        compose = yaml.safe_load(f)

    if not compose or "services" not in compose:
        return

    # Find main service (one with ports)
    main_svc = None
    internal_port = "80"
    for svc_name, svc_config in compose.get("services", {}).items():
        if svc_config.get("ports"):
            main_svc = svc_name
            p = str(svc_config["ports"][0])
            internal_port = p.split(":")[-1] if ":" in p else p
            break
    if not main_svc:
        main_svc = list(compose["services"].keys())[0]

    router = name.replace("-", "").replace("_", "").replace(".", "")

    # Remove host port bindings from main service
    compose["services"][main_svc].pop("ports", None)

    # Add Traefik labels
    labels = [
        "traefik.enable=true",
        f"traefik.http.routers.{router}.rule=Host(`{staging_domain}`)",
        f"traefik.http.routers.{router}.entrypoints=web",
        f"traefik.http.services.{router}.loadbalancer.server.port={internal_port}",
    ]
    if production_domain:
        labels.extend([
            f"traefik.http.routers.{router}-prod.rule=Host(`{production_domain}`)",
            f"traefik.http.routers.{router}-prod.entrypoints=websecure",
            f"traefik.http.routers.{router}-prod.tls.certresolver=letsencrypt",
            f"traefik.http.routers.{router}-prod.service={router}",
        ])
    compose["services"][main_svc]["labels"] = labels

    # Add pleng_web network to main service
    svc_networks = compose["services"][main_svc].get("networks", [])
    if isinstance(svc_networks, list) and "pleng_web" not in svc_networks:
        svc_networks.append("pleng_web")
    elif isinstance(svc_networks, dict) and "pleng_web" not in svc_networks:
        svc_networks["pleng_web"] = {}
    compose["services"][main_svc]["networks"] = svc_networks

    # Fix build contexts: relative → absolute
    for svc_name, svc in compose.get("services", {}).items():
        build = svc.get("build")
        if build is None:
            continue
        if isinstance(build, str) and build in (".", "./"):
            svc["build"] = workspace
        elif isinstance(build, str) and build.startswith("./"):
            svc["build"] = os.path.join(workspace, build[2:])
        elif isinstance(build, dict):
            ctx = build.get("context", ".")
            if ctx in (".", "./"):
                build["context"] = workspace
            elif ctx.startswith("./"):
                build["context"] = os.path.join(workspace, ctx[2:])

    # Add pleng_web network definition
    if "networks" not in compose:
        compose["networks"] = {}
    compose["networks"]["pleng_web"] = {"external": True}

    pleng_file = os.path.join(workspace, "docker-compose.pleng.yml")
    with open(pleng_file, "w") as f:
        yaml.dump(compose, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Generated pleng compose for {name}")


def _connect_network(project: str):
    try:
        r = subprocess.run(["docker", "compose", "-p", project, "ps", "-q"],
                           capture_output=True, text=True, timeout=10)
        for cid in r.stdout.strip().split("\n"):
            if cid.strip():
                subprocess.run(["docker", "network", "connect", NETWORK, cid.strip()],
                               capture_output=True, text=True, timeout=10)
    except Exception as e:
        logger.warning(f"Network connect: {e}")


def _auto_generate_compose(workspace: str) -> str | None:
    if os.path.exists(os.path.join(workspace, "Dockerfile")):
        return "services:\n  web:\n    build: .\n    ports:\n      - '80:80'\n    restart: unless-stopped\n"
    if os.path.exists(os.path.join(workspace, "package.json")):
        return "services:\n  web:\n    build: .\n    ports:\n      - '80:3000'\n    restart: unless-stopped\n"
    if os.path.exists(os.path.join(workspace, "requirements.txt")):
        return "services:\n  web:\n    build: .\n    ports:\n      - '80:8000'\n    restart: unless-stopped\n"
    return None
