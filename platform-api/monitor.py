"""Health monitor + maintenance scheduler.

- Health checks: pings deployed sites every 10 min, alerts via Telegram
- Docker prune: cleans unused images/cache every 24h
- Extensible: add more scheduled tasks as needed
"""
import logging
import os
import subprocess
import threading
import time

import requests

import database as db
import deployer

logger = logging.getLogger("monitor")

CHECK_INTERVAL = int(os.environ.get("MONITOR_INTERVAL", "600"))  # 10 minutes
PRUNE_INTERVAL = 86400  # 24 hours
FAILURE_THRESHOLD = 3
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def start():
    """Start background threads."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram not configured — monitor won't send alerts")

    threading.Thread(target=_health_loop, daemon=True).start()
    logger.info(f"Health monitor started (interval={CHECK_INTERVAL}s)")

    threading.Thread(target=_maintenance_loop, daemon=True).start()
    logger.info(f"Maintenance scheduler started (backup + prune every 24h)")


# ── Health checks ───────────────────────────────────────

def _health_loop():
    time.sleep(30)  # Wait for initial deploys
    while True:
        try:
            for site in db.get_all_sites():
                if site["status"] in ("staging", "production"):
                    _check_site(site)
        except Exception as e:
            logger.error(f"Health loop error: {e}")
        time.sleep(CHECK_INTERVAL)


def _check_site(site: dict):
    domain = site.get("production_domain") or site.get("staging_domain")
    if not domain:
        return

    url = f"https://{domain}" if site.get("production_domain") else f"http://{domain}"

    try:
        r = requests.get(url, timeout=10, allow_redirects=True)
        if r.status_code < 500:
            _mark_healthy(site)
        else:
            _mark_failure(site, f"HTTP {r.status_code}")
    except requests.ConnectionError:
        _mark_failure(site, "Connection refused")
    except requests.Timeout:
        _mark_failure(site, "Timeout")
    except Exception as e:
        _mark_failure(site, str(e)[:100])


def _mark_failure(site: dict, error: str):
    failures = db.increment_failures(site["id"])
    logger.warning(f"{site['name']}: failure #{failures} — {error}")

    if failures == FAILURE_THRESHOLD:
        _alert(f"🔴 <b>{site['name']}</b> is DOWN\n{error}")
        ok = deployer.restart(site["id"])
        _alert(f"🔄 Auto-restart {'ok' if ok else 'FAILED'}: <b>{site['name']}</b>")
        db.add_site_log(site["id"], f"DOWN: {error}. Auto-restart {'ok' if ok else 'failed'}", level="warning")


def _mark_healthy(site: dict):
    failures = db.get_failures(site["id"])
    if failures >= FAILURE_THRESHOLD:
        _alert(f"🟢 <b>{site['name']}</b> is back UP")
        db.add_site_log(site["id"], "Recovered")
    if failures > 0:
        db.reset_failures(site["id"])


# ── Docker prune ────────────────────────────────────────

def _maintenance_loop():
    time.sleep(300)  # Wait 5 min after startup
    while True:
        try:
            _backup()
        except Exception as e:
            logger.error(f"Backup error: {e}")
        try:
            _docker_prune()
        except Exception as e:
            logger.error(f"Prune error: {e}")
        time.sleep(PRUNE_INTERVAL)


# ── Backups ─────────────────────────────────────────────

BACKUP_DIR = "/opt/pleng/backups"
BACKUP_KEEP = 7  # Keep last 7 backups

def _backup():
    """Backup SQLite DB + site compose files. Keeps last 7 days."""
    import glob
    import shutil
    import tarfile
    from datetime import datetime

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M")
    backup_file = os.path.join(BACKUP_DIR, f"pleng-{timestamp}.tar.gz")

    db_path = os.environ.get("DATABASE_PATH", "/data/pleng.db")
    projects_dir = os.environ.get("PROJECTS_DIR", "/opt/pleng/projects")

    try:
        with tarfile.open(backup_file, "w:gz") as tar:
            # Backup SQLite DB
            if os.path.exists(db_path):
                tar.add(db_path, arcname="pleng.db")

            # Backup docker-compose files from each project (not full code — just configs)
            if os.path.exists(projects_dir):
                for name in os.listdir(projects_dir):
                    proj = os.path.join(projects_dir, name)
                    if not os.path.isdir(proj):
                        continue
                    for fname in ("docker-compose.yml", "docker-compose.pleng.yml", "Dockerfile", ".env"):
                        fpath = os.path.join(proj, fname)
                        if os.path.exists(fpath):
                            tar.add(fpath, arcname=f"projects/{name}/{fname}")

        size = os.path.getsize(backup_file)
        size_str = f"{size / 1024:.1f}KB" if size < 1048576 else f"{size / 1048576:.1f}MB"
        logger.info(f"Backup created: {backup_file} ({size_str})")

        # Clean old backups
        backups = sorted(glob.glob(os.path.join(BACKUP_DIR, "pleng-*.tar.gz")))
        while len(backups) > BACKUP_KEEP:
            old = backups.pop(0)
            os.remove(old)
            logger.info(f"Deleted old backup: {old}")

        sites_count = len(db.get_all_sites())
        _alert(f"💾 Backup done: {size_str}, {sites_count} sites")

    except Exception as e:
        logger.error(f"Backup failed: {e}")
        _alert(f"⚠️ Backup failed: {e}")


# ── Docker prune ────────────────────────────────────────

def _docker_prune():
    """Remove unused Docker images and build cache."""
    r1 = subprocess.run(
        ["docker", "image", "prune", "-f"],
        capture_output=True, text=True, timeout=120,
    )
    r2 = subprocess.run(
        ["docker", "builder", "prune", "-f", "--filter", "until=168h"],
        capture_output=True, text=True, timeout=120,
    )

    freed1 = r1.stdout.strip().split("\n")[-1] if r1.stdout else "0B"
    freed2 = r2.stdout.strip().split("\n")[-1] if r2.stdout else "0B"
    logger.info(f"Docker prune: images={freed1}, cache={freed2}")

    if "0B" not in freed1 or "0B" not in freed2:
        _alert(f"🧹 Docker cleanup: {freed1}, {freed2}")


# ── Telegram ────────────────────────────────────────────

def _alert(message: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.info(f"Alert (no Telegram): {message}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Telegram alert failed: {e}")
