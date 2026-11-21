# Backend

The backend is the hub: a FastAPI application that serves the API, the WebSocket, the built web app, the execution worker and the sweeper. It is a single process, and it owns all state in PostgreSQL. It is the only part that writes to the database.

Source: `backend/`. Entry point: `app.main:create_app`, a factory, so tests and uvicorn build the app the same way.

## Layout

| Path | Responsibility |
|---|---|
| `app/main.py` | Builds the app: middleware, CSRF, security headers, routers, the worker lifespan, `/health` and `/ready`. |
| `app/core/config.py` | Settings from environment variables (below). |
| `app/core/security.py` | Password hashing (argon2) and token hashing (SHA-256). |
| `app/core/ratelimit.py` | In-process sliding-window rate limiter for sign-in, registration and password reset. |
| `app/db/` | SQLAlchemy base with naming conventions, the engine and session factory. |
| `app/models/` | ORM models, grouped by area (below). |
| `app/api/` | Routers, one module per area, and the request and response schemas. |
| `app/services/` | Business logic the routers call (below). |
| `app/services/providers/` | Execution providers: the in-process fake and the Cloud Run service provider. |
| `app/realtime/hub.py` | In-process fan-out of saved and live events to WebSocket subscribers. |
| `migrations/` | Alembic migrations. The chain runs from `b38873a6b2ce` to `a0b1c2d3e4f5`. |
| `scripts/` | Operator commands (below). |
| `tests/` | pytest suite. Runs on SQLite; PostgreSQL integration tests run when `TEST_DATABASE_URL` is set. |

## Configuration

Settings come from environment variables, or from `backend/.env` in development. Defaults are for local development.

| Variable | Default | Purpose |
|---|---|---|
| `ENVIRONMENT` | `development` | `production` turns on secure cookies, HSTS and stricter startup checks. |
| `DATABASE_URL` | `sqlite:///./collab_dev.db` | SQLAlchemy URL. Production uses PostgreSQL; `postgres://` URLs are accepted. |
| `LOG_LEVEL` | `INFO` | Log level. Logs are JSON and never contain message bodies or credentials. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated origins allowed to call the API and open the WebSocket. |
| `SESSION_IDLE_MINUTES` | `1440` (24 hours) | A sign-in session ends after this much inactivity. |
| `SESSION_ABSOLUTE_HOURS` | `336` (14 days) | A sign-in session ends after this long, whatever the activity. |
| `COOKIE_SECURE` | from `ENVIRONMENT` | Overrides whether the session cookie is marked secure. Set `false` only for plain-HTTP local testing. |
| `ALLOW_REGISTRATION` | `true` | Open sign-up. Set `false` to close it. |
| `FRONTEND_DIST` | none | Folder with the built web app; the API serves it when set. |
| `RUNTIME_TOKEN_SECRET` | generated per process | Signs runtime tokens. Required in production (32+ characters). |
| `EXECUTION_PROVIDER` | `fake` | `fake` for local runs and tests; `cloud_run_service` to send runs to the Cloud Run service. |
| `RUNTIME_SERVICE_URL` | none | Address of the Cloud Run service. Required with `cloud_run_service`. |
| `GCP_DISPATCHER_KEY` | none | Base64 of the dispatcher service-account key JSON. Required with `cloud_run_service`. A secret: never logged or returned. |
| `GITHUB_ALLOWED_REPOS` | empty | Comma-separated `owner/name` list. A run for any other repository is refused. |
| `EXECUTION_WORKER_ENABLED` | `true` | Runs the outbox worker and sweeper in this process. |
| `EXECUTION_WORKER_INTERVAL_SECONDS` | `5` | How often the worker and sweeper run. Production uses `3`. |
| `EMAIL_API_KEY` | none | Brevo API key for password reset emails. A secret. Without it, reset requests get a 503 that says email is not set up. |
| `EMAIL_FROM` | none | Sender for reset emails, verified in Brevo, for example `NatyaSutra <you@example.com>`. |
| `PUBLIC_APP_URL` | `RENDER_EXTERNAL_URL` | Address of the web app, used in reset links. |

Startup checks: `cloud_run_service` requires the dispatcher key and the service address. Production requires a runtime token secret and PostgreSQL.

## Models

| Module | Tables | Notes |
|---|---|---|
| `identity.py` | `users`, `teams`, `team_memberships` | `users.handle` is unique across people and agents. |
| `auth.py` | `auth_sessions`, `password_resets` | Both store only a SHA-256 of their token. A reset link is single-use and expires after 30 minutes. |
| `projects.py` | `projects`, `project_memberships`, `channels` | Roles: owner, admin, member, viewer. |
| `work.py` | `messages`, `tasks`, `decisions`, `decision_revisions` | Messages can be threaded, edited and deleted. |
| `events.py` | `project_events` | Saved events with a per-project sequence number. |
| `audit.py` | `audit_events` | Append-only. Never stores message bodies. |
| `agents.py` | `agents`, `project_agents`, `agent_invocations`, `invocation_files` | An agent has a handle, a harness (`claude_code`), a repository and a branch. Attaching is the owner's consent for a project. A run has a status and an optional error. Files a run returned are kept here. |
| `sessions.py` | `agent_sessions`, `agent_session_messages` | A session is one conversation with one agent. Visibility is `private` or `project`. Deleting sets `deleted_at`; the rows stay. |
| `execution.py` | `execution_outbox`, `invocation_leases`, `run_events` | See [Execution](execution.md). |
| `enums.py` | | Shared enums and check constraints. |

## API

All routes are under `/api/v1`. Project routes check membership on every request and return 404 to non-members.

| Area | Routes |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/forgot-password`, `POST /auth/reset-password` |
| Teams and projects | `GET/POST /teams`, `POST /teams/{id}/projects`, `GET /projects`, `GET /projects/{id}` |
| Members | `GET/POST /projects/{id}/members`, `GET /projects/{id}/member-candidates`, `PATCH/DELETE /projects/{id}/members/{user}` |
| Channels and messages | `GET/POST /projects/{id}/channels`, `GET/POST /projects/{id}/channels/{channel}/messages`, `PATCH/DELETE /projects/{id}/messages/{message}` |
| Tasks and decisions | `GET/POST /projects/{id}/tasks`, `GET/PATCH /projects/{id}/tasks/{task}`, `GET/POST /projects/{id}/decisions`, `GET/PATCH /projects/{id}/decisions/{decision}`, `GET /projects/{id}/decisions/{decision}/revisions` |
| Activity, audit, search | `GET /projects/{id}/activity`, `GET /projects/{id}/audit`, `GET /projects/{id}/search` |
| Agents and runs | `GET /me/agents`, `GET/POST /projects/{id}/agents`, `GET /projects/{id}/invocations`, `POST /projects/{id}/invocations/{invocation}/cancel`, `GET /projects/{id}/invocations/{invocation}/files/{file}` |
| Sessions | `GET/POST /projects/{id}/sessions`, `GET /projects/{id}/sessions/{session}`, `GET .../messages`, `POST .../turns`, `POST .../visibility`, `POST .../close`, `DELETE /projects/{id}/sessions/{session}` |
| Live updates | `WS /api/v1/ws/projects/{id}?since=<sequence>` |
| Runtime (internal) | `POST /api/v1/internal/executions/exchange`, `POST /api/v1/internal/executions/events` |
| Operations | `GET /health`, `GET /ready` |

Writes need a CSRF token: the browser echoes the `ns_csrf` cookie in the `X-CSRF-Token` header. This includes sign-in, registration and password reset. The only exception is the runtime endpoints, which use bearer credentials instead.

Rate limits: sign-in 30 per IP and 10 per email in 15 minutes; registration 5 per IP per hour; password reset requests 10 per IP and 3 per email per hour; reset completions 20 per IP per hour.

## Authorisation

Every project route calls `load_project_for` (`app/services/authz.py`), which checks membership and the minimum role. That function is the single point for project access. Agent identity never grants access to a project.

Session rules, on top of that:

| Action | Who |
|---|---|
| See a private session | Its owner only. Others get "not found". |
| See a shared session | Every project member. |
| Send a turn | The session's owner, and for a shared session any project owner. |
| Change visibility, close | The session's owner. |
| Delete | The session's creator, any project owner, or any project admin. Refused while a turn is running. |

## Services

### Mentions and runs (`services/agents.py`)

- `create_invocations_for_message` runs inside the message transaction. For each mention of an attached agent, it creates an invocation in `queued` state and an outbox row.
- `create_session_turn` does the same for a session turn, after the session rules are checked.
- `request_cancel` marks the invocation cancelled. The runtime stops at its next heartbeat.
- `apply_outcome` publishes live events after the commit.

### Execution (`services/execution.py`, `execution_worker.py`, `sweeper.py`, `providers/`)

The outbox, leases and dispatch. The worker runs every `EXECUTION_WORKER_INTERVAL_SECONDS`. See [Execution](execution.md).

### Leases and runtime events (`services/leases.py`, `services/runtime_events.py`)

The exchange checks a lease and returns the run's context and runtime token. Each event from a run is applied once, in order, and its effect is published. See [Execution](execution.md) and [Runtime](runtime.md#events).

### Sessions (`services/transcript.py`)

A session's turns run one at a time. Each run carries the session's earlier turns, because the runtime keeps nothing between runs. `session_prompt` builds that prompt when the lease is exchanged:

- The newest transcript row is this turn's own message. It is left out of the history and sent last, as `New message:`.
- The history is limited to the latest 40 rows and 20,000 characters. Each reply is shortened to 4,000 characters, and the oldest turns are dropped first.
- The prompt is built from the database at that moment. The stored invocation keeps only the new message.
- The earlier turns are labelled `@<agent>` for the agent and by name for people, and wrapped in `<earlier_turns>`.

Limits: a long session loses its oldest turns, and the agent has no memory beyond the cap. See [Architecture](../architecture.md#6-a-session-turn).

### Files returned by runs (`services/files.py`)

A run may return up to five text files (`MAX_FILES_PER_RUN`). A file is visible to whoever can see its run. Downloads are plain-text attachments with `nosniff`, so the browser never renders them. Deleted sessions hide their files.

### Password reset (`services/password_reset.py`, `services/email.py`)

A request creates one link, valid for 30 minutes, and emails it through Brevo. A new request cancels older unused links. The same answer is given whether or not the account exists. Completing a reset sets the new password, ends every session of the account, and writes an audit entry. Email is sent with the Brevo API (`services/email.py`); nothing logs a recipient, body or response.

### Realtime (`app/api/realtime.py`, `app/realtime/hub.py`)

Saved events are written to `project_events` in the same transaction as the change, then published. On connect, a client gets the events after its `since` sequence, then live events. Events about a private session are sent only to its owner. See [Architecture](../architecture.md#10-live-updates).

### Handles (`services/handles.py`)

A handle is the name used in a mention. People and agents share one namespace, so a mention always resolves to one of them.

### Audit

Writes that matter (membership, agent attach, runs, cancels, sessions, password resets) are recorded in `audit_events`. Entries never include message bodies or credentials.

## Operator scripts

Run from `backend/` with the production `DATABASE_URL` set. In the deployed hub these run from the Render shell, which needs a paid plan; on the free plan, run them from your own machine against the production database.

| Command | Purpose |
|---|---|
| `python -m scripts.manage create-user --email ... --name ...` | Create an account when sign-up is closed. |
| `python -m scripts.manage set-password --email ...` | Set a new password for an account. Ends all its sessions. |
| `python -m scripts.create_cloud_agent --email ... --handle ... --project-slug ... --repo owner/name --ref main` | Create a cloud agent for a project owner or admin, attach it, and write an audit entry. `--write` lets its runs commit and push to `--ref`; never for `main` or `master`. Checks the handle is free and the repository is on the allow-list. `--dry-run` prints without writing. |
| `python -m scripts.set_agent_write --handle ... --on` or `--off` | Turn an agent's write permission on or off. `--on` refuses `main` and `master`. Audited. |
| `python -m scripts.seed` | Sample data for local development. |
| `python -m scripts.smoke_e2e --base http://localhost:5173` | End-to-end check against a running hub. |

## Tests

```powershell
docker run --rm -v "<path>\backend:/app" -w /app python:3.11-slim sh -c "pip install -q -r requirements-dev.txt && pytest -q"
python -m ruff check app scripts migrations tests
```

The suite runs on SQLite, with a fresh database per test. The fake execution provider records submissions, and tests drive the worker with `process_once`. PostgreSQL-specific checks run when `TEST_DATABASE_URL` is set.
