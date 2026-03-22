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
    """Build docker compose command with both user compose and pleng override."""
    user_compose = os.path.join(workspace, "docker-compose.yml")
    pleng_override = os.path.join(workspace, "docker-compose.pleng.yml")

    cmd = ["docker", "compose", "-f", user_compose]
    if os.path.exists(pleng_override):
        cmd.extend(["-f", pleng_override])
    cmd.extend(["-p", project] + list(args))
    return cmd


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
    """Generate docker-compose.pleng.yml with Traefik labels and absolute build paths.

    This file is merged with the user's docker-compose.yml via -f flag.
    The user's compose is NEVER modified.
    """
    user_compose_file = os.path.join(workspace, "docker-compose.yml")
    with open(user_compose_file) as f:
        user_compose = yaml.safe_load(f)

    if not user_compose or "services" not in user_compose:
        return

    # Find main service (one with ports)
    main_svc = None
    internal_port = "80"
    for svc_name, svc_config in user_compose.get("services", {}).items():
        if svc_config.get("ports"):
            main_svc = svc_name
            p = str(svc_config["ports"][0])
            internal_port = p.split(":")[-1] if ":" in p else p
            break
    if not main_svc:
        main_svc = list(user_compose["services"].keys())[0]

    router = name.replace("-", "").replace("_", "").replace(".", "")

    # Build override for main service
    override_svc = {
        "labels": [
            "traefik.enable=true",
            f"traefik.http.routers.{router}.rule=Host(`{staging_domain}`)",
            f"traefik.http.routers.{router}.entrypoints=web",
            f"traefik.http.services.{router}.loadbalancer.server.port={internal_port}",
        ],
        "networks": ["pleng_web"],
    }

    # Add production labels if promoting
    if production_domain:
        override_svc["labels"].extend([
            f"traefik.http.routers.{router}-prod.rule=Host(`{production_domain}`)",
            f"traefik.http.routers.{router}-prod.entrypoints=websecure",
            f"traefik.http.routers.{router}-prod.tls.certresolver=letsencrypt",
            f"traefik.http.routers.{router}-prod.service={router}",
        ])

    # Remove ports (Traefik handles routing) — override with empty
    override_svc["ports"] = []

    # Fix build context: make relative paths absolute
    user_build = user_compose["services"][main_svc].get("build")
    if user_build:
        if isinstance(user_build, str) and user_build in (".", "./"):
            override_svc["build"] = workspace
        elif isinstance(user_build, str) and user_build.startswith("./"):
            override_svc["build"] = os.path.join(workspace, user_build[2:])
        elif isinstance(user_build, dict):
            ctx = user_build.get("context", ".")
            if ctx in (".", "./"):
                override_svc["build"] = {"context": workspace}
            elif ctx.startswith("./"):
                override_svc["build"] = {"context": os.path.join(workspace, ctx[2:])}

    override = {
        "services": {main_svc: override_svc},
        "networks": {"pleng_web": {"external": True}},
    }

    override_file = os.path.join(workspace, "docker-compose.pleng.yml")
    with open(override_file, "w") as f:
        yaml.dump(override, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Generated pleng override for {name} at {override_file}")


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
