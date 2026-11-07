# Development

Quick start (after the one-time setup below):

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres      # 1. database
cd backend
.venv\Scripts\python.exe -m alembic upgrade head                # 2. schema
.venv\Scripts\python.exe -m scripts.seed                        # 3. optional sample data (prints a password once)
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000   # 4. API
cd ..\frontend; npm run dev                                     # 5. UI at http://localhost:5173
```

## Backend (Python 3.11)

Use the **Windows** Python 3.11 installation. The MSYS2 Python on this machine has a MinGW build
tag that cannot install `pydantic-core` or `psycopg[binary]` wheels.

```powershell
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env          # then edit DATABASE_URL if needed
```

`requirements.txt` includes `uvicorn[standard]`. Plain `uvicorn` has no WebSocket implementation,
so every `/api/v1/ws/...` connection returns 404. This was found and fixed during Phase 1.

### Tests

```powershell
.venv\Scripts\python.exe -m pytest -q                 # SQLite in-memory: 66 passed, 2 skipped
```

Run the whole suite on PostgreSQL (68 passed on PostgreSQL 16):

```powershell
docker exec deploy-postgres-1 psql -U collab -d collab -c "CREATE DATABASE collab_test;"
$env:TEST_DATABASE_URL="postgresql+psycopg://collab:change-me-locally@localhost:5432/collab_test"
.venv\Scripts\python.exe -m pytest -q
Remove-Item Env:TEST_DATABASE_URL
```

The suite drops and recreates tables in `TEST_DATABASE_URL`. Never point it at a database with data.

Lint and format:

```powershell
.venv\Scripts\python.exe -m ruff check app tests migrations scripts
.venv\Scripts\python.exe -m ruff format --check app tests migrations scripts
```

### Migrations

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic check                         # reports drift between models and migrations
.venv\Scripts\python.exe -m alembic revision --autogenerate -m "describe change"
```

| Revision | Contents | Verified on |
|---|---|---|
| `b38873a6b2ce` | Users, teams, memberships, projects, channels, messages, tasks, decisions, revisions, audit | SQLite, PostgreSQL 16 |
| `eed762fe4dbf` | Auth sessions, project events, `projects.event_seq` | PostgreSQL 16 |

Each was checked with upgrade, `alembic check` (no drift), downgrade, upgrade.

### Seed data

```powershell
.venv\Scripts\python.exe -m scripts.seed
```

Creates `alice@example.com` (owner) and `bob@example.com` (member) in a team, a project, five channels,
a task and a proposed decision. A random password is generated per run and printed once. The script is
idempotent. Note that the seeded password appears only in that terminal's output and is not stored.

### Run the API

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000
```

- `GET /health` — liveness, no database access.
- `GET /ready` — database reachable.
- API: `/api/v1/...` (see the route list in `docs/architecture.md`).

## Frontend (Node 26 LTS, npm 11)

```powershell
cd frontend
npm install
npm run dev          # Vite on http://localhost:5173, proxies /api (including WebSockets) to :8000
npm run typecheck
npm test             # Vitest, 18 tests
npm run build        # type check + production bundle in dist/
```

Set `VITE_API_TARGET` to point the proxy at another API address if needed.

## End-to-end smoke test

With the API and `npm run dev` running:

```powershell
cd backend
.venv\Scripts\python.exe -m scripts.smoke_e2e --base http://localhost:5173
```

It registers two throwaway users, creates a project, adds the member, sends a message and a task, and
checks that the owner's WebSocket receives both live. It then logs out and checks the session is revoked.

## Local PostgreSQL

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres
```

Requires Docker Desktop to be running. Container `deploy-postgres-1`, healthy on `localhost:5432`.
The credentials in `deploy/docker-compose.yml` are local-only defaults and must not be used anywhere else.

## Local bridge (Phase 2)

```powershell
cd bridge
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m pytest -q            # 40 tests
.venv\Scripts\python.exe -m ruff check collab_bridge tests
```

The bridge's own usage guide is `bridge/README.md`. Set `BRIDGE_TOKEN_SECRET` for the API in any shared environment. If it is unset, the API generates an ephemeral secret and logs a warning, so bridge tokens stop working on restart.

Apply migration `ca45a4ac516f` (bridge tables, `messages.author_agent_id`) before using the bridge:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

Rate limits are in memory. Registering more than five accounts from one address within an hour returns 429 until the API restarts. This applies to local testing as well.

Test-database caution: the PostgreSQL integration tests drop every table in `TEST_DATABASE_URL`, including `alembic_version`. After running them, rebuild the database from migrations (drop the `public` schema, then `alembic upgrade head`) before testing migrations again. Use a database that holds nothing you need.

### End-to-end check with the real Claude Code CLI

This needs a logged-in Claude Code install and the API and bridge running. It makes a few short model calls. A scratch driver used during Phase 2 walked through these steps:

1. Register two users and create a project, then create a pairing code.
2. `collab-bridge pair`, then `collab-bridge agent add --harness-bin <path to claude.exe>`, then `collab-bridge run`.
3. Attach the agent to the project and mention it. Expect the reply `BRIDGE OK`, attributed to the agent.
4. Ask it to write a file. Expect an approval request, approve it as the agent owner, and expect the file to appear in the approved folder.

Agent handles are global and first-come. Use a fresh handle for each run.

## Dedicated sessions and Codex (Phase 3)

Apply migration `c2bea0dd1f66` (agent sessions, session messages, nullable invocation channel) before using sessions:

```powershell
.venv\Scripts\python.exe -m alembic upgrade head
```

Codex needs the CLI installed and logged in on the bridge machine. On Windows, use the native binary under the npm package (`codex.exe`, see `docs/codex.md`); the `.cmd` shim needs Node on PATH.

The end-to-end check used a scratch driver (not in the repo) that: opens a private session with each agent, sends two turns (remember a word, then recall it), checks that a second member cannot open the private session (404), and shares the Codex session to confirm the member can read it. Both agents passed.

## Known local-development gotchas

- The rate limiter and realtime hub are in-process. Run **one** API process. Running several behind a load
  balancer needs a shared store and bus first (see `docs/security.md` §10).
- The API sets `Secure` on cookies only when `ENVIRONMENT=production`. Local runs use plain HTTP.
- The first request to the API sets the `ns_csrf` cookie. Any state-changing call without that cookie and a
  matching `X-CSRF-Token` header returns 403.
