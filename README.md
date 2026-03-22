<p align="center">
  <h1 align="center">Pleng</h1>
  <p align="center"><strong>Your AI Platform Engineer.</strong></p>
  <p align="center">
    An AI agent that lives on your server, deploys your apps,<br/>
    monitors your infra, and talks to you via Telegram.
  </p>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> · <a href="#what-pleng-does">What it does</a> · <a href="#how-to-talk-to-it">How to talk to it</a> · <a href="#architecture">Architecture</a> · <a href="#roadmap">Roadmap</a>
</p>

---

## What if you had a platform engineer that never sleeps?

You tell it "deploy this". It clones the repo, writes the Dockerfile if needed, spins up the containers, puts Traefik in front, assigns a public URL with SSL, and tells you when it's done.

You tell it "why is my API slow". It reads the Docker logs, finds the OOMKilled error, and suggests increasing the memory limit.

You tell it "build me a landing page for my SaaS". It writes the code, creates the Docker config, deploys it, and gives you the URL.

That's Pleng. A platform engineer that lives on your VPS, understands natural language, and operates your infrastructure autonomously.

### Why this doesn't exist yet

- **Coolify / Dokploy** (50K+ stars) — deploy apps, but they're passive dashboards. You click buttons. No agent, no intelligence, no natural language.
- **Claude Code / Cursor** — write code, but can't deploy it. The "last mile" is still manual.
- **Pulumi Neo / StackGen** — AI DevOps, but cloud-only, $10K+/month, enterprise.
- **OpenClaw / Hermes** — AI personal agents, but they don't know how to manage Docker containers.

Nobody combined **self-hosted + AI agent + infrastructure management** in one package. Pleng does.

## Quickstart

```bash
git clone https://github.com/your-org/pleng
cd pleng
cp .env.example .env
# Edit .env with your keys (see below)
docker compose up -d
```

That's it. 6 containers start up. You now have:

| What | Where |
|---|---|
| **Dashboard** | `http://panel.YOUR-IP.sslip.io` |
| **Telegram bot** | `@your_bot` (listening) |
| **Terminal** | `make chat` on the VPS |
| **skill.md** | `http://panel.YOUR-IP.sslip.io/skill.md` |
| **API key** | `docker compose logs platform-api` (printed on startup) |

### Required env vars

```bash
ANTHROPIC_API_KEY=sk-ant-...       # For the AI agent (Claude Code)
TELEGRAM_BOT_TOKEN=123456:ABC...   # From @BotFather
TELEGRAM_CHAT_ID=123456789         # Your chat ID
PUBLIC_IP=89.141.205.249           # Your VPS public IP
```

## What Pleng does

You talk to it. It operates your server.

```
You: "deploy github.com/user/my-api"
Pleng: Cloning... detecting stack... deploying...
       Live at http://a3f2.89.141.205.249.sslip.io

You: "build me a booking API with Postgres"
Pleng: [writes code, Dockerfile, docker-compose.yml, deploys]
       Live at http://fe01.89.141.205.249.sslip.io

You: "why is my web down?"
Pleng: [reads Docker logs] Container OOMKilled. RAM limit too low.
       Want me to bump it to 512MB?

You: "put bookings.mydomain.com on it"
Pleng: [updates Traefik, Let's Encrypt SSL]
       Live at https://bookings.mydomain.com

You: "show me the logs for bookings"
Pleng: [last 50 lines of Docker logs]

You: "stop the demo and delete the staging"
Pleng: Stopped and removed.
```

### The lifecycle

```
 ┌─────────┐    promote     ┌────────────┐
 │ STAGING │ ─────────────▶ │ PRODUCTION │
 │  free   │  custom domain │   HTTPS    │
 │ sslip.io│  + Let's       │            │
 └─────────┘  Encrypt       └────────────┘
      │                           │
      ▼                           ▼
   stop / remove              stop / remove
```

1. Everything starts as **staging** with a free `http://{hash}.{IP}.sslip.io` URL. No domain needed.
2. You can have 10 projects in staging with zero effort.
3. The ones you like, **promote** to production with a custom domain → automatic HTTPS.
4. The ones you don't, stop or remove.
5. No git, no CI/CD, no pipelines. Just talking.

### Three ways to deploy

| Mode | You say | What happens |
|---|---|---|
| **Git repo** | "deploy github.com/user/repo" | Clones, detects stack, deploys |
| **Docker Compose** | "deploy this compose" (sends file) | Reads it, starts containers |
| **AI Generate** | "build me a color tool" | Claude Code writes everything, then deploys |

All three produce the same result: containers running behind Traefik with a staging URL.

## How to talk to it

Same engineer, four ways to reach it:

| Interface | When to use it |
|---|---|
| **Telegram** | From anywhere. Quick commands, status checks, deploys on the go. |
| **Terminal** | On the VPS. Full Claude Code experience. Iterate for hours. |
| **Dashboard** | In the browser. Read-only view of sites, logs, status. |
| **skill.md** | From any external AI agent. Your Claude Code at home deploys to your server. |

### Telegram
```
You: "restart bookings"             → Pleng: "Done."
You: "logs for my-api"              → Pleng: [last 50 lines]
You: "build me a fitness landing"   → Pleng: [writes code, deploys, returns URL]
```

### Terminal
```bash
make chat    # on your VPS

You: deploy github.com/user/repo --name my-app
Pleng: Cloning... deploying... Live at http://a3f2.1.2.3.4.sslip.io
```

### Dashboard
`http://panel.YOUR-IP.sslip.io` — password-protected. See all sites, their status, URLs, Docker logs, build history. **Read-only** — all operations go through the agent.

### External agents (skill.md)
```bash
# From Claude Code on your Mac:
"read http://panel.myserver.com/skill.md and deploy my project"
```

The skill.md is auto-generated with the correct API URL and documents all endpoints. Any AI tool that can do HTTP can deploy to your Pleng. Auth via API key.

External agents **deploy existing code** (git repo or tar.gz upload). They don't generate projects — that's what the built-in agent does via Telegram/terminal.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                      Your VPS                        │
│                                                      │
│  ┌──────────┐  ┌─────────────┐  ┌───────────────┐  │
│  │ Traefik  │  │ Platform    │  │    Agent       │  │
│  │ (proxy)  │  │ API         │  │ (Claude Code)  │  │
│  │ SSL/HTTP │  │ Docker sock │  │ pleng CLI      │  │
│  │ sslip.io │  │ SQLite      │  │ /projects vol  │  │
│  └──────────┘  └─────────────┘  └───────────────┘  │
│       ▲              ▲                  │            │
│       │         ┌────┘──────────────────┘            │
│       │         │  HTTP (internal network)            │
│  ┌──────────┐  ┌─────────────┐  ┌───────────────┐  │
│  │ Telegram │  │ Analytics   │  │  Dashboard    │  │
│  │ Bot      │  │ (tracking)  │  │  (React)      │  │
│  └──────────┘  └─────────────┘  └───────────────┘  │
│                                                      │
│  + your deployed apps (each in its own containers)   │
└─────────────────────────────────────────────────────┘
```

**6 containers. One `docker compose up`.**

| Container | Tech | Role |
|---|---|---|
| **traefik** | Traefik v3 | Reverse proxy. sslip.io staging, Let's Encrypt production |
| **platform-api** | FastAPI + SQLite | Orchestrates Docker. REST API. State. Auth. skill.md |
| **agent** | Claude Code + Flask | AI brain. Writes code + calls `pleng` CLI to deploy |
| **telegram-bot** | python-telegram-bot | Thin bridge: Telegram ↔ agent |
| **analytics** | FastAPI + SQLite | Pageview tracking. <1KB script. API-first |
| **dashboard** | React + nginx | Read-only web panel. Static files only |

### Key design decisions

- **Agent is isolated.** Shared `/projects` volume but NO Docker socket. Calls platform-api over HTTP. If the agent breaks, your infra keeps running.
- **sslip.io for staging.** `anything.YOUR-IP.sslip.io` resolves to your IP. No DNS config. Free. Instant.
- **Platform-api owns Docker.** Single point of control. All deploy/stop/restart goes through its REST API.
- **SQLite, not Postgres/MongoDB.** Zero extra containers. One file. Good enough for single-VPS scale.
- **API key auto-generated.** Created on first boot, printed to logs. Internal services fetch it automatically. External access requires the key.
- **Dashboard is read-only.** No deploy buttons. All operations via agent (Telegram/terminal). The dashboard is for monitoring, not control.

### Auth model

```
External (internet)  →  needs X-API-Key header
Internal (containers) →  no auth (Docker internal network)
Dashboard login      →  password (WEB_UI_PASSWORD) → returns API key
skill.md             →  public (documents the API, tells agents to use the key)
```

## Configuration

### Required

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | For Claude Code (the AI agent) |
| `TELEGRAM_BOT_TOKEN` | From @BotFather |
| `TELEGRAM_CHAT_ID` | Your Telegram chat ID |
| `PUBLIC_IP` | Your VPS public IP (`curl ifconfig.me` to find it) |

### Optional

| Variable | Default | Description |
|---|---|---|
| `BASE_DOMAIN` | — | Custom domain for the panel (enables HTTPS for panel) |
| `ACME_EMAIL` | admin@example.com | Email for Let's Encrypt certificates |
| `MODEL_NAME` | claude-sonnet-4-20250514 | Claude model for the agent |
| `GITHUB_TOKEN` | — | For deploying from private repos |
| `WEB_UI_PASSWORD` | admin | Dashboard login password |

## The `pleng` CLI

Inside the agent container, Claude Code has a `pleng` CLI tool that talks to platform-api:

```bash
pleng sites                              # List all sites
pleng deploy /projects/app --name app    # Deploy from path
pleng deploy-git https://github.com/... --name app  # Deploy from git
pleng logs my-app                        # Docker logs
pleng status my-app                      # Container status
pleng stop my-app                        # Stop
pleng restart my-app                     # Restart
pleng remove my-app                      # Remove (containers + files)
pleng promote my-app --domain x.com      # Staging → production + SSL
pleng chat                               # Interactive terminal mode
```

Claude Code decides which commands to run based on what you ask in natural language.

## External agent integration (skill.md)

Any AI agent that can read HTTP and make API calls can deploy to your Pleng:

```bash
# From your local Claude Code:
curl http://panel.YOUR-IP.sslip.io/skill.md
```

The skill.md documents all endpoints:

```
POST /api/deploy/git      — deploy from git repo
POST /api/deploy/upload   — deploy by uploading a tar.gz
GET  /api/sites           — list sites
GET  /api/sites/{id}/logs — Docker logs
POST /api/sites/{id}/stop — stop
POST /api/sites/{id}/promote — staging → production
```

All endpoints require `X-API-Key` header (get the key from `docker compose logs platform-api`).

## Project structure

```
pleng/
├── docker-compose.yml           # THE PRODUCT — 6 services
├── .env.example                 # 4 required env vars
├── Makefile                     # up, down, logs, chat, ps
├── LICENSE                      # AGPL-3.0
│
├── platform-api/                # Docker orchestrator
│   ├── Dockerfile               # Multi-stage: docker:27-cli + python:3.12
│   ├── app.py                   # FastAPI — routes, auth, skill.md
│   ├── deployer.py              # Deploy engine — compose up, Traefik labels, promote
│   └── database.py              # SQLite — sites, logs, settings, API key
│
├── agent/                       # AI brain (isolated — no Docker socket)
│   ├── Dockerfile               # python + node + claude-code + pleng CLI
│   ├── server.py                # Flask HTTP — receives messages, runs Claude Code
│   ├── entrypoint.sh            # Copies CLAUDE.md to /projects
│   ├── workspace/CLAUDE.md      # System prompt — tells Claude Code about pleng CLI
│   └── tools/pleng.py           # CLI tool — deploy, logs, stop, promote, chat
│
├── telegram-bot/                # Thin bridge
│   ├── Dockerfile
│   └── bot.py                   # Telegram ↔ agent HTTP, /sites command
│
├── analytics/                   # Built-in tracking
│   ├── Dockerfile
│   ├── app.py                   # FastAPI — collector + stats API
│   └── static/t.js              # Tracking script (<1KB)
│
└── dashboard/                   # Read-only web panel
    ├── Dockerfile               # Multi-stage: node build + nginx
    ├── nginx.conf               # Static files only (API via Traefik)
    └── src/
        ├── App.tsx              # Login + sidebar + routes
        └── pages/
            ├── LoginPage.tsx    # Password → API key
            ├── Dashboard.tsx    # Sites overview (auto-refresh)
            ├── SitesPage.tsx    # Site cards grid
            └── SiteDetailPage.tsx  # Logs, build-log, promote
```

## Roadmap

### Phase 1: Core — Done
- [x] Platform API — deploy, stop, restart, remove, logs, promote
- [x] Traefik — sslip.io staging, Let's Encrypt production
- [x] Agent — Claude Code in container with `pleng` CLI
- [x] Telegram bot
- [x] Dashboard — read-only, password-protected
- [x] Analytics — built-in pageview tracking
- [x] skill.md — auto-generated, for external agents
- [x] Auth — API key auto-generated, internal/external split
- [x] Upload endpoint — deploy without git

### Phase 2: Operations
- [ ] Health checks — auto-restart crashed containers
- [ ] Telegram alerts — site down, disk full, OOM
- [ ] Resource monitoring — CPU/RAM/disk per container
- [ ] Weekly summary reports via Telegram

### Phase 3: CI/CD
- [ ] Git webhook — auto-deploy on push
- [ ] Rollback to previous version
- [ ] Zero-downtime deploys

### Phase 4: Scale
- [ ] Multi-server support
- [ ] Backup and restore
- [ ] Environment cloning (staging → prod)

## Why not just use Coolify?

| | Coolify | Dokploy | Railway | **Pleng** |
|---|---|---|---|---|
| Self-hosted | Yes | Yes | No | **Yes** |
| AI agent | No | No | No | **Yes** |
| Natural language | No | No | No | **Yes** |
| Telegram | No | No | No | **Yes** |
| skill.md for agents | No | No | No | **Yes** |
| Built-in analytics | No | No | No | **Yes** |
| Free staging URLs | No | No | Auto | **Auto (sslip.io)** |
| Setup | Complex | Medium | Cloud | **`docker compose up`** |
| Price | Free | Free | $5-20/mo | **Free** |
| License | AGPL-3.0 | Apache 2.0 | Closed | **AGPL-3.0** |

## License

AGPL-3.0 — same license as Coolify. Self-host freely. If you modify the code and offer it as a service, you must open-source your changes. See [LICENSE](LICENSE).

---

<p align="center">
  <strong>Your infra, your agent, your rules.</strong>
</p>
