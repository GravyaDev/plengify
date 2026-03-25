# Contributing to Pleng

Thanks for your interest in contributing! Pleng is an AI-native PaaS — here's how to get involved.

## Quick start

```bash
git clone https://github.com/mutonby/pleng
cd pleng
cp .env.example .env   # fill in your tokens
make up                # builds and starts all 6 containers
```

### Prerequisites

- Docker and Docker Compose
- A Telegram bot token (free from [@BotFather](https://t.me/BotFather))
- Claude Code auth — either OAuth (`~/.claude`) or an Anthropic API key

### Running tests

```bash
# Platform API
cd platform-api && pip install -r requirements.txt && pytest tests/ -v

# Pleng CLI
cd agent/tools && pip install requests pytest && pytest tests/ -v

# Dashboard
cd dashboard && npm install && npm run build
```

## Project structure

| Directory | What it does | Language |
|---|---|---|
| `platform-api/` | FastAPI — orchestrates Docker, owns state (SQLite) | Python |
| `agent/` | Claude Code container + `pleng` CLI | Python |
| `telegram-bot/` | Bridges Telegram with the agent | Python |
| `analytics/` | Pageview tracking from Traefik logs | Python |
| `dashboard/` | Web panel (React + Vite + Tailwind) | TypeScript |

## How to contribute

### Reporting bugs

Open an [issue](https://github.com/mutonby/pleng/issues) with:
- What you did (steps to reproduce)
- What you expected
- What actually happened
- `docker compose logs <service>` output if relevant

### Suggesting features

Open an issue with the `enhancement` label. Describe the use case, not just the solution.

### Submitting code

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add tests if you're touching `platform-api` or `pleng` CLI logic
4. Make sure CI passes: `pytest` for Python, `npm run build` for dashboard
5. Open a pull request

### Pull request guidelines

- Keep PRs focused — one thing per PR
- Follow existing code style (PEP 8 for Python, TypeScript for dashboard)
- Use type hints in Python
- Don't add dependencies unless necessary
- Update the README if you change user-facing behavior

## Architecture rules

These are intentional design decisions — please don't change them:

- **Agent has no Docker socket.** All infrastructure changes go through `pleng` CLI → platform-api HTTP. This is the security boundary.
- **SQLite, not Postgres.** Single-VPS scale. One file. Zero extra containers.
- **User's docker-compose.yml is never modified.** Pleng generates an overlay file for Traefik labels.
- **Services communicate via HTTP** over the internal Docker network.

## Code style

- **Python**: PEP 8, type hints, f-strings
- **TypeScript**: React + Tailwind, functional components
- **Commits**: short imperative subject line ("Add rate limiting", "Fix deploy timeout")

## License

By contributing, you agree that your contributions will be licensed under AGPL-3.0.
