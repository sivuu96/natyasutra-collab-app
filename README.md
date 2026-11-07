# NatyaSutra Collaboration Hub

A workspace where a team plans and discusses the NatyaSutra port together: channels and threads, tasks, decisions with history, search, an activity trail, and live updates. Each teammate can also connect their own Claude Code or Codex to the hub through a local bridge, so an agent can answer in a channel or in a private session.

## Using the hub

- **Hosted:** once deployed (see [docs/deployment.md](docs/deployment.md)), your team opens the address in a browser. An administrator creates your account.
- **Local:** follow the quick start below.

## Status

| Area | State |
|---|---|
| Discussions, tasks, decisions, search, activity, live updates, roles | Built and tested (backend suite on PostgreSQL, frontend suite, browser walkthrough) |
| Local agent bridge (`bridge/`) | Working with Claude Code and Codex, verified end to end on one machine. Not yet tested across two machines. |
| Dedicated agent sessions (private or shared) | Working, verified end to end with both harnesses |
| Production deployment (Render) | Container and Blueprint ready and tested locally. Live deployment pending. |
| Conversation import, Git integration | Not started (Phases 4–5) |

See [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) for detail and open items.

## Quick start (Windows, PowerShell)

Prerequisites: Docker Desktop, Python 3.11 (Windows build), Node.js LTS.

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m scripts.seed          # prints a one-time password for alice@ and bob@example.com
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000

# in a second terminal
cd frontend
npm install
npm run dev                                        # open http://localhost:5173
```

To run the production build locally instead, see "Run the production image" in [docs/deployment.md](docs/deployment.md).

Full instructions, tests, and troubleshooting: [docs/development.md](docs/development.md).

## Documentation

- [Deployment](docs/deployment.md) — deploying to Render, creating accounts, operating the hub
- [Architecture](docs/architecture.md) — components, data flows, API surface
- [Security and privacy](docs/security.md) — threat model, controls, known limitations
- [Agent protocol](docs/agent-protocol.md) — the bridge wire contract
- [Claude Code](docs/claude-code.md) and [Codex](docs/codex.md) — harness integration
- [Development](docs/development.md) — setup, tests, migrations
- [Implementation plan](IMPLEMENTATION_PLAN.md) — phases, decisions, open items

The product specification is [NatyaSutra_Collaboration_Hub_Implementation_Greenfield.md](NatyaSutra_Collaboration_Hub_Implementation_Greenfield.md).
This hub is a separate application. It does not connect to or modify the NatyaSutra product database.
