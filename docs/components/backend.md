# Backend

The backend is the hub: a FastAPI application that serves the API, the WebSocket, the built web app, and the execution worker. It is a single process, and it owns all state in PostgreSQL.

Source: `backend/`. Entry point: `app.main:create_app` (a factory, so tests and uvicorn create the app the same way).

## Layout

| Path | Responsibility |
|---|---|
| `app/main.py` | Builds the app: middleware, CSRF, security headers, routers, the worker lifespan, and the `/health` and `/ready` probes. |
| `app/core/config.py` | Settings from environment variables (see below). |
| `app/core/security.py` | Password hashing (argon2) and session tokens. |
| `app/core/ratelimit.py` | In-process sliding-window rate limiter for sign-in and registration. |
| `app/db/` | SQLAlchemy base with naming conventions, the engine and session factory. |
| `app/models/` | ORM models, grouped by area (see below). |
| `app/api/` | Routers. One module per area. |
| `app/services/` | Business logic that the routers call: agents, execution, leases, runtime events, sweeper, transcript, and so on. |
| `app/services/providers/` | Execution providers: the in-process fake and the Cloud Run service provider. |
| `app/realtime/hub.py` | In-process fan-out of committed events to WebSocket subscribers. |
| `migrations/` | Alembic migrations. The chain runs from `b38873a6b2ce` to `b4c5d6e7f8a9`. |
| `scripts/` | Operator commands (below). |
| `tests/` | pytest suite. Runs on SQLite; PostgreSQL integration tests run when `TEST_DATABASE_URL` is set. |

## Configuration

Settings come from environment variables, or from `backend/.env` in development. Defaults are for local development.

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` turns on secure cookies, HSTS and stricter checks. |
| `DATABASE_URL` | `sqlite:///./collab_dev.db` | SQLAlchemy URL. Production uses PostgreSQL; `postgres://` URLs are accepted. |
| `RUNTIME_TOKEN_SECRET` | generated per process | Signs runtime tokens. Required in production (32+ characters). |
| `EXECUTION_PROVIDER` | `fake` | `fake` for local runs and tests; `cloud_run_service` to send runs to the Cloud Run service. |
| `EMAIL_API_KEY` | none | Brevo API key for password reset emails. A secret. Without it, reset requests get a 503 that says email is not set up. |
| `EMAIL_FROM` | none | Sender for reset emails, verified in Brevo, for example `NatyaSutra <you@example.com>`. |
| `PUBLIC_APP_URL` | `RENDER_EXTERNAL_URL` | Address of the web app, used in reset links. |
| `GCP_DISPATCHER_KEY` | none | Base64 of the dispatcher service-account key JSON. Required with `cloud_run_service`. A secret: never logged or returned. |
| `RUNTIME_SERVICE_URL` | none | Address of the Cloud Run service that runs agents. Required with `cloud_run_service`. |
| `GITHUB_ALLOWED_REPOS` | empty | Comma-separated `owner/name` list. A run for any other repository is refused. |
| `EXECUTION_WORKER_ENABLED` | `true` | Runs the outbox worker in this process. |
| `EXECUTION_WORKER_INTERVAL_SECONDS` | `5` | How often the worker and sweeper run. |
| `ALLOW_REGISTRATION` | `true` | Open sign-up. Set `false` to close it. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated origins for the browser. |
| `FRONTEND_DIST` | none | Folder with the built web app; the API serves it when set. |
| `LOG_LEVEL` | `INFO` | Log level. Logs are JSON; they never contain message bodies or credentials. |

Startup checks: `cloud_run_service` requires the dispatcher key and the service address. Production requires a runtime token secret and PostgreSQL.

## Models

| Module | Tables | Notes |
|---|---|---|
| `identity.py` | `users`, `teams`, `team_memberships` | `users.handle` is unique across people and agents. |
| `auth.py` | `auth_sessions`, `password_resets` | Stores a hash of each session token. |
| `projects.py` | `projects`, `project_memberships`, `channels` | Membership roles: owner, admin, member, viewer. |
| `work.py` | `messages`, `tasks`, `decisions`, `decision_revisions` | Messages can be threaded and edited. |
| `events.py` | `project_events` | Persisted events with a per-project sequence number. |
| `audit.py` | `audit_events` | Append-only. Never stores message bodies. |
| `agents.py` | `agents`, `project_agents`, `agent_invocations`, `invocation_files` | An agent has a handle, a harness (`claude_code`), a repository and a branch. Attaching is the owner's consent for a project. |
| `sessions.py` | `agent_sessions`, `agent_session_messages` | A session is one conversation with one agent. Visibility is `private` or `project`. |
| `execution.py` | `execution_outbox`, `invocation_leases`, `run_events` | See [Execution](execution.md). |
| `enums.py` | | Shared enums and check constraints. |

## API

All routes are under `/api/v1`. Project routes check membership on every request and return 404 to non-members.

| Area | Routes |
|---|---|
| Auth | `POST /register`, `POST /login`, `POST /logout`, `GET /me`, `POST /forgot-password`, `POST /reset-password` |
| Teams and projects | `GET/POST /teams`, `POST /teams/{id}/projects`, `GET /projects`, `GET /projects/{id}`, `GET/POST /projects/{id}/members`, `PATCH/DELETE /projects/{id}/members/{user}` |
| Channels and messages | `GET/POST /projects/{id}/channels`, `GET/POST /projects/{id}/channels/{channel}/messages`, `PATCH`/`DELETE /projects/{id}/messages/{message}` |
| Tasks and decisions | `GET/POST /projects/{id}/tasks`, `GET/PATCH /projects/{id}/tasks/{task}`, `GET/POST /projects/{id}/decisions`, `GET/PATCH /projects/{id}/decisions/{decision}`, `GET /projects/{id}/decisions/{decision}/revisions` |
| Activity, audit, search | `GET /projects/{id}/activity`, `GET /projects/{id}/audit`, `GET /projects/{id}/search` |
| Agents | `GET /me/agents`, `GET/POST /projects/{id}/agents`, `GET /projects/{id}/invocations`, `POST /projects/{id}/invocations/{invocation}/cancel`, `GET /projects/{id}/invocations/{invocation}/files/{file}` |
| Sessions | `GET/POST /projects/{id}/sessions`, `GET /projects/{id}/sessions/{session}`, `GET .../messages`, `POST .../turns`, `POST .../visibility`, `POST .../close` |
| Live updates | `WS /api/v1/ws/projects/{id}?since=<sequence>` |
| Runtime (internal) | `POST /api/v1/internal/executions/exchange`, `POST /api/v1/internal/executions/events` |
| Operations | `GET /health`, `GET /ready` |

Writes need a CSRF token: the browser echoes the `ns_csrf` cookie in the `X-CSRF-Token` header. The runtime endpoints are the only exception; they use bearer credentials instead.

## Authorisation

Every project route calls `load_project_for` (`app/services/authz.py`), which checks membership and the minimum role. That function is the single point for project access. Agent identity never grants access to a project.

## Services

### Mentions and runs (`services/agents.py`)

- `create_invocations_for_message` runs inside the message transaction. For each mention of an attached agent, it creates an invocation in `queued` state and an outbox row.
- `create_session_turn` does the same for a session turn. It refuses a turn for a revoked agent.
- `request_cancel` marks the invocation cancelled. The runtime stops at its next heartbeat.
- `apply_outcome` broadcasts events after the commit.

### Sessions

A session's turns run one at a time. The turn route checks for an active invocation in the session before it creates one.

Who may send a turn: the session's owner, and for a shared (project) session, any project owner. Project admins and other members cannot. The session list's `can_send` flag tells the frontend which composers to show.

Each run carries the session's earlier turns, because the runtime keeps nothing between runs and cannot resume a previous Claude session. `services/transcript.py` builds that prompt when the run's lease is exchanged:

- The newest transcript row is this turn's own message. It is left out of the history and sent last, as the new message.
- The history is limited to 40 rows and 20,000 characters. Each reply is shortened to 4,000 characters, and the oldest turns are dropped first.
- The prompt is built from the database at that moment. The stored invocation keeps only the new message, because invocation prompts are broadcast in events.

Limits: a long session loses its oldest turns, and the agent has no memory beyond the cap. See [Architecture](../architecture.md#3-a-session-turn).

### Realtime (`app/api/realtime.py`, `app/realtime/hub.py`)

Events are written to `project_events` in the same transaction as the change, then broadcast. On connect, a client gets the events after its `since` sequence, then live events. Events about a private session are sent only to its owner.

### Handles (`services/handles.py`)

A handle is the name used in a mention. People and agents share one namespace, so a mention always resolves to one of them. Handles are unique and checked against both tables.

### Audit

Writes that matter (membership, agent attach, run requests, cancels, session changes) are recorded in `audit_events`. Audit entries never include message bodies or credentials.

## Operator scripts

Run from `backend/` with the production `DATABASE_URL` set.

| Command | Purpose |
|---|---|
| `python -m scripts.manage create-user --email ... --name ...` | Create an account when sign-up is closed. |
| `python -m scripts.create_cloud_agent --email ... --handle ... --project-slug ... --repo owner/name --ref main` | Create a cloud agent for a project owner or admin, attach it, and write an audit entry. Checks the handle is free and the repository is on the allow-list. `--dry-run` prints without writing. |
| `python -m scripts.seed` | Sample data for local development. |
| `python -m scripts.smoke_e2e --base http://localhost:5173` | End-to-end check against a running hub. |

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check app scripts migrations tests
```

Test fixtures (`tests/conftest.py`) create a fresh SQLite database per test. The fake execution provider records submissions, and tests drive the worker with `process_once`.
