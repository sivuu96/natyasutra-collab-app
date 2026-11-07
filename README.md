# NatyaSutra Collaboration Hub

A self-hostable workspace where a small team plans and discusses the NatyaSutra port together:
discussions, tasks, decisions, search, and an activity trail, updated live.

Local AI agents run on each developer's own machine through the bridge in `bridge/`. Claude Code works end to end; Codex is planned for Phase 3.
See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for status.

## Status

| Area | State |
|---|---|
| Backend API (FastAPI, PostgreSQL, Alembic) | Phase 1 complete, tested on SQLite and PostgreSQL 16 |
| Frontend (React, TypeScript, Vite, Tailwind) | Phase 1 complete, type-checked, unit-tested, built, walked through in Microsoft Edge |
| Local agent bridge (`bridge/`) | Phase 2 implemented. Claude Code verified end to end. Codex is Phase 3. Two real machines not yet tested. |
| Conversation import, Git integration | Not started (Phases 4–5) |
| Production deployment (Render, Vercel) | Not deployed. Configuration not yet written. |

## Quick start (Windows, PowerShell)

Prerequisites: Docker Desktop, Python 3.11 (Windows build), Node.js LTS.

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m scripts.seed          # prints a one-time password for alice@ and bob@example.com
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000

# in a second terminal
cd frontend
npm install
npm run dev                                        # open http://localhost:5173
```

Full instructions, tests, and troubleshooting: [docs/development.md](docs/development.md).

## Documentation

- [Architecture](docs/architecture.md) — components, data flows, API surface
- [Security and privacy](docs/security.md) — threat model, controls, known limitations
- [Agent protocol](docs/agent-protocol.md) — planned bridge contract (Phase 2)
- [Development](docs/development.md) — setup, tests, migrations, smoke test
- [Implementation plan](IMPLEMENTATION_PLAN.md) — phases, decisions, blockers

The product specification is [NatyaSutra_Collaboration_Hub_Implementation_Greenfield.md](NatyaSutra_Collaboration_Hub_Implementation_Greenfield.md).
This hub is a separate application. It does not connect to or modify the NatyaSutra product database.
