# Development

This guide covers running the hub locally, the test suites, and the checks to run before you push. Commands are for Windows PowerShell; on other systems use `.venv/bin/python` in place of `.venv\Scripts\python.exe`.

## Quick start

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres      # 1. database
cd backend
.venv\Scripts\python.exe -m alembic upgrade head                # 2. schema
.venv\Scripts\python.exe -m scripts.seed                        # 3. optional sample data (prints a password once)
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000   # 4. API
cd ..\frontend; npm run dev                                     # 5. UI at http://localhost:5173
```

Local runs use `EXECUTION_PROVIDER=fake`, which records agent runs without starting anything. Nothing reaches Google Cloud.

## Repositories and what runs where

| Part | Folder | Tests |
|---|---|---|
| Hub API, worker, migrations | `backend/` | `pytest` in `backend/` |
| Web app | `frontend/` | `npm test` in `frontend/` |
| Agent runtime | `runtime/` | `pytest` in `runtime/` |
| Production image and Compose | `deploy/` | `docker compose ... up --build` |

## Backend (Python 3.11)

Use the **Windows** Python 3.11 installation. The MSYS2 Python has a MinGW build tag that cannot install the `pydantic-core` or `psycopg[binary]` wheels.

If Windows Smart App Control is on, it can block the `pydantic_core` library in a local virtual environment, and the tests fail to start. Run the backend suite in a container instead:

```powershell
docker run --rm -v "<path>ackend:/app" -w /app python:3.11-slim sh -c "pip install -q -r requirements-dev.txt && pytest -q"
```

```powershell
cd backend
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # runtime + test and lint tools
copy .env.example .env          # then edit DATABASE_URL if needed
```

`requirements.txt` includes `uvicorn[standard]`. Plain `uvicorn` has no WebSocket support, so every `/api/v1/ws/...` connection would return 404.

### Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

The default database is SQLite in memory, and each test gets a fresh one. To run the suite on PostgreSQL as well:

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

`alembic check` should print `No new upgrade operations detected`. If it does not, the models and the migrations disagree; fix the migration before you commit.

Migrations are PostgreSQL-first. Constraint names must be wrapped in `op.f(...)`, because the naming convention in `app/db/base.py` would otherwise add the prefix again.

| Revision | Contents |
|---|---|
| `b38873a6b2ce` | Users, teams, memberships, projects, channels, messages, tasks, decisions, revisions, audit |
| `eed762fe4dbf` | Auth sessions, project events, `projects.event_seq` |
| `ca45a4ac516f` | Early agent tables (later reduced by `f2a3b4c5d6e7`) |
| `c2bea0dd1f66` | Dedicated sessions |
| `5e0d1f2a3b4c` | Execution outbox |
| `7a1c2d3e4f50` | Agents without a device |
| `9b2c3d4e5f60` | Invocation leases |
| `b4d5e6f7a8b9` | Run events |
| `c5e6f7a8b9c0` | Agent repository and branch |
| `d6f7a8b9c0d1` | Run activity and cancel |
| `e7a8b9c0d1e2` | User handles |
| `f2a3b4c5d6e7` | Removes the local bridge: devices, pairing, approvals, inbound events, Codex, and bridge-only columns. Refuses to run while a device-backed agent exists. Cannot be reversed. |
| `a3b4c5d6e7f8` | Drops columns nothing read: agent description, platform admin flag, session last-seen time. Cannot be reversed. |
| `b4c5d6e7f8a9` | Drops the two outbox columns only the removed job stop step used. Reversible: restores the empty columns. |
| `c5d6e7f8a9b0` | Files an agent returned in its reply, kept with the run. Reversible: drops the table. |
| `e7f8a9b0c1d2` | Password reset links: only a hash of each token is stored. Reversible: drops the table. |
| `f8a9b0c1d2e3` | Session deletion: a `deleted_at` marker; the rows are kept. Reversible: drops the column. |
| `a0b1c2d3e4f5` | Drops two columns nothing read: a run's result-message link and a session's closed-at time. Reversible: restores the empty columns. |

The bridge migration was checked on PostgreSQL 16: it refused with a device-backed agent present, ran after that agent was removed, and `alembic check` reported no drift. The latest migration was checked the same way: upgrade, downgrade, upgrade, then `alembic check`, with no drift.

### Seed data

```powershell
.venv\Scripts\python.exe -m scripts.seed
```

Creates `alice@example.com` (owner) and `bob@example.com` (member) in a team, a project, channels, a task and a proposed decision. A random password is generated per run and printed once. The script is idempotent.

### Run the API

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --port 8000
```

- `GET /health`: liveness, no database access.
- `GET /ready`: database reachable.
- API: `/api/v1/...`. See [Backend](components/backend.md#api).

### Agents locally

With `EXECUTION_PROVIDER=fake`, a mention or session turn is queued, and the worker records a submission. No runtime starts, so no reply appears. To test the runtime end to end, run the real container (below) against the real Cloud Run service, or test the runtime in isolation (the runtime tests use a fake hub and a fake Claude Code binary).

## Runtime (Python 3.11)

```powershell
cd runtime
python -m pip install -r requirements.txt   # once: httpx, pyjwt, cryptography, Secret Manager client
python -m pytest -q
```

The tests use a fake hub and a fake harness script, so they need no network and no Anthropic key.

To build the image locally, from the repository root:

```powershell
docker build -f runtime/Dockerfile -t collab-agent-runtime:local .
```

## Frontend (Node 22 LTS)

Use Node 22 LTS. Node 26 breaks the Vitest suite (its test setup fails before any test runs). The type check and the build work on either.

```powershell
cd frontend
npm install
npm run dev          # Vite on http://localhost:5173, proxies /api (including WebSockets) to :8000
npm test             # Vitest
npx tsc --noEmit -p . # type check
npm run build        # type check + production bundle in dist/
```

Set `VITE_API_TARGET` to point the proxy at another API address.

## End-to-end smoke test

With the API and `npm run dev` running:

```powershell
cd backend
.venv\Scripts\python.exe -m scripts.smoke_e2e --base http://localhost:5173
```

It registers two throwaway users, creates a project, adds the member, sends a message and a task, and checks that the owner's WebSocket receives both live. It then logs out and checks the session is revoked.

## Local PostgreSQL

```powershell
docker compose -f deploy/docker-compose.yml up -d postgres
```

Requires Docker Desktop. The container `deploy-postgres-1` listens on `localhost:5432`. The credentials in `deploy/docker-compose.yml` are local defaults and must not be used anywhere else.

## Before you push

1. `backend`: `pytest`, `ruff check`, and `alembic check` if you changed models.
2. `runtime`: `pytest`.
3. `frontend`: `npm test` and `npx tsc --noEmit -p .`.
4. Check that no secret, key or `.env` file is staged: `git status` and `git diff --cached`.

## Known local-development gotchas

- The rate limiter and realtime hub are in-process. Run **one** API process.
- The API sets `Secure` on cookies only when `ENVIRONMENT=production`. Local runs use plain HTTP.
- The first request to the API sets the `ns_csrf` cookie. A state-changing request without that cookie and a matching `X-CSRF-Token` header returns 403.
- `deploy/.env` (used by Compose) must define `RUNTIME_TOKEN_SECRET`. Older files that define only `BRIDGE_TOKEN_SECRET` must be renamed.
