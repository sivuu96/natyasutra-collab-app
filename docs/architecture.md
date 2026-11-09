# Architecture — NatyaSutra Collaboration Hub

Status: Phase 0 design. Not yet implemented.

## 1. Shape

A modular monolith with one API process, one realtime gateway (in the same process), one background worker, PostgreSQL, and S3-compatible object storage. Each developer also runs a local bridge on their own machine.

```
 Browser (React/Vite)                      Developer machine
   │  HTTPS + WSS                            ┌──────────────────────────┐
   ▼                                         │ Local bridge (Python)     │
 ┌──────────────────────────────────┐        │  ├─ device pairing        │
 │ API + Realtime gateway (FastAPI) │◄──────┤  ├─ outbound WSS (TLS)    │
 │  auth · projects · channels      │  out-  │  ├─ AgentAdapter          │
 │  tasks · decisions · agents      │  bound │  │   ├─ claude_code (-p)  │
 │  invocation router · audit       │        │  │   └─ codex (app-server)│
 └───────┬───────────────┬──────────┘        │  ├─ repo allow-list       │
         │               │                   │  └─ local event queue     │
 ┌───────▼──────┐  ┌─────▼───────────┐       └──────────────────────────┘
 │ PostgreSQL   │  │ Object storage  │
 │ (all state)  │  │ (transcripts,   │
 └───────▲──────┘  │  attachments)   │
         │         └─────────────────┘
 ┌───────┴────────────────────────┐
 │ Worker (outbox, imports,        │
 │ webhooks, notifications)        │
 └─────────────────────────────────┘
```

The bridge always initiates the connection. The hub never calls into a developer's machine.

## 2. Repository layout (planned)

```
collab-app/
├─ backend/        FastAPI app, SQLAlchemy models, Alembic migrations, tests
├─ frontend/       React + TypeScript + Vite + Tailwind
├─ bridge/         Python CLI/daemon, harness adapters, bridge tests
├─ shared/         Event envelope JSON Schema + generated types (single contract)
├─ deploy/         Docker Compose (dev), Render blueprint, Vercel config
├─ docs/           This folder
├─ IMPLEMENTATION_PLAN.md
└─ NatyaSutra_Collaboration_Hub_Implementation_Greenfield.md   (spec, unchanged)
```

No file in this tree touches the NatyaSutra product runtime or database.

## 3. Backend modules

| Module | Responsibility |
|---|---|
| `auth` | Registration, login, sessions, password hashing, rate limits. |
| `authz` | The single place where project membership and role checks are made. Every route and WebSocket subscription calls it. |
| `teams`, `projects` | Teams, projects, memberships, repository links. |
| `discussions` | Channels, messages, threads, mentions, attachments. |
| `tasks`, `decisions` | Records, transitions, revisions, links. |
| `agents` | Agent registry, devices, capabilities, status. |
| `invocations` | Creating, routing, tracking invocations and approvals. |
| `events` | Envelope validation, persist-then-broadcast, sequencing, replay. |
| `realtime` | WebSocket gateway; per-connection authorisation. |
| `audit` | Append-only audit writes; read API. |
| `search` | PostgreSQL full-text queries, always project-scoped. |
| `git` | GitHub integration interface (Phase 5). |
| `imports` | Conversation import pipeline (Phase 4). |
| `worker` | Outbox consumer, retries, scheduled jobs. |

Dependencies point inward: routes → services → repositories. Services own authorisation calls and audit writes. Models never import routes.

## 4. Data flows

### 4.1 Human message in a channel
1. Client POSTs a message. `authz` confirms membership in the project.
2. Service writes `messages` and an `audit_events` row in one transaction; the same transaction writes an outbox row.
3. Realtime gateway picks up the committed event and broadcasts it to subscribers of that project. Subscribers are authorised at subscribe time and re-checked on each send.

### 4.2 Agent mention (Phase 2)
See [agent-protocol.md](agent-protocol.md) §5.

### 4.3 Conversation import (Phase 4)
Bridge scans configured source directories, runs secret detection locally, proposes a review report, then uploads only after the owner approves. Uploads are stored private to the owner. Sharing is a separate, audited action.

## 5. Data stores

- **PostgreSQL** — all collaboration state: users, teams, projects, memberships, channels, messages, tasks, decisions, agents, devices, invocations, approvals, events, audit, outbox, imported-conversation metadata, normalised messages.
- **Object storage** — raw transcript originals and attachments only. Canonical requirements and code stay in Git.
- **Local bridge storage** — a small SQLite file (or JSON-lines) for the outbound event queue and cursor. No credentials in this store beyond the short-lived access token held in memory or the OS keychain.

PostgreSQL is authoritative. Nothing is cached elsewhere in Phase 1–3 (brief §12.2 "no Redis").

## 6. Deployment targets

| Component | Target | Verified? |
|---|---|---|
| Frontend | Served by the API container (no separate Vercel deployment) | Yes — live at Render |
| API + realtime | Render web service (Docker) | Yes — live at Render |
| Worker | Not deployed (the bridge runs on each developer's machine) | n/a |
| Database | Supabase PostgreSQL (free plan, us-west-2) | Yes — migrated and verified |
| Local dev | Docker Compose (PostgreSQL, API, worker) | Planned for Phase 1 |

Configuration is environment-only. No production URLs or credentials appear in source.

## 7. Non-functional targets (Phase 1)

- WebSocket reconnect with exponential backoff and jitter on the client.
- Event broadcast after commit only; no broadcast of uncommitted state.
- All list endpoints paginated.
- Health (`/health`, liveness) and readiness (`/ready`, checks DB connectivity).

## 8. Implemented API surface (Phase 1)

All routes are under `/api/v1`. "Member" means any project role; the minimum role is in brackets.

| Method and path | Purpose | Minimum role |
|---|---|---|
| `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` | Sessions | — |
| `POST /teams`, `GET /teams`, `POST /teams/{id}/projects` | Teams and projects (team admin to create a project) | team member |
| `GET /projects`, `GET /projects/{id}` | Projects you belong to; `latest_sequence` for resume | member |
| `GET/POST /projects/{id}/members`, `PATCH/DELETE /projects/{id}/members/{user}` | Membership and roles | admin |
| `GET/POST /projects/{id}/channels` | Channels | viewer / member |
| `GET/POST /projects/{id}/channels/{cid}/messages` | Messages (`parent_message_id` for threads) | viewer / member |
| `PATCH/DELETE /projects/{id}/messages/{mid}` | Edit (author) / soft delete (author or admin) | member |
| `GET/POST /projects/{id}/tasks`, `GET/PATCH /projects/{id}/tasks/{tid}` | Tasks | viewer / member |
| `GET/POST /projects/{id}/decisions`, `GET/PATCH /projects/{id}/decisions/{did}`, `…/revisions` | Decisions | viewer / member (status change: decision owner or admin) |
| `GET /projects/{id}/activity?after=N` | Sequenced event feed | member |
| `GET /projects/{id}/audit` | Audit log | admin |
| `GET /projects/{id}/search?q=` | Search messages, tasks, decisions | member |
| `WS /ws/projects/{id}?since=N` | Live events with replay | member |
| `GET /health`, `GET /ready` (outside `/api/v1`) | Operations | — |

Errors: `404` for absent or unauthorised project resources; `403` for insufficient role; `409` for conflicts;
`422` for validation; `429` for rate limits; `403` with `CSRF check failed` for missing or mismatched CSRF.

Event envelope (persisted in `project_events`, broadcast after commit):

```json
{ "event_id": "uuid", "schema_version": 1, "event_type": "conversation.message.created",
  "project_id": "uuid", "sequence": 12, "occurred_at": "ISO-8601",
  "actor_user_id": "uuid", "payload": { } }
```

Phase 1 event types: `conversation.channel.created`, `conversation.message.created`,
`conversation.message.updated` (edit or delete), `task.created`, `task.updated`, `decision.updated`,
`project.seeded` (seed data only). Heartbeats (`realtime.heartbeat`) are not persisted.

## 9. Implemented API surface (Phase 2: devices, bridge and agents)

| Method and path | Purpose | Auth |
|---|---|---|
| `POST /devices/pairing-codes` | One-time pairing code (10 min, single use) | session cookie + CSRF |
| `GET /devices`, `DELETE /devices/{id}` | List own devices; revoke one (closes its socket) | session cookie + CSRF |
| `GET /me/agents` | Agents owned by the current user, with online state | session cookie |
| `POST /bridge/pair` | Redeem a pairing code; returns device id and secret once | pairing code (rate-limited) |
| `POST /bridge/token` | Exchange device secret for a 15-minute access token | device secret |
| `WS /bridge/ws` | Bridge channel: hello, heartbeat, acknowledgements, invocation streams | bearer token (no cookies) |
| `GET /projects/{id}/agents` | Agents attached to the project, with online state | member |
| `POST /projects/{id}/agents`, `DELETE /projects/{id}/agents/{agent}` | Attach or detach (owner, or admin) | member; owner or admin |
| `GET /projects/{id}/invocations` | Recent invocation records | member |
| `POST /projects/{id}/invocations/{inv}/cancel` | Cancel queued or running work | requester, owner, or admin |
| `GET /projects/{id}/approvals?status=pending` | Approvals awaiting a decision | member |
| `POST /projects/{id}/approvals/{approval}` | Approve or deny (409 if already decided or no longer awaited) | owner or admin |

Mentions are processed at message creation: `POST /projects/{id}/channels/{cid}/messages` accepts `queue_if_offline` (default false). Messages written by agents carry `author_agent_handle`.

Event types added in Phase 2: `agent.attached`, `agent.status.changed`, `agent.invocation.created`, `agent.invocation.delivered`, `agent.invocation.accepted`, `agent.invocation.rejected`, `agent.invocation.completed`, `agent.invocation.failed`, `agent.invocation.cancelled`, `agent.invocation.cancel_requested`, `agent.approval.required`, `agent.approval.resolved`, and `agent.output.delta` (broadcast only; never persisted or sequenced). Message creation from agent output uses `conversation.message.created`.

The bridge wire contract is in [agent-protocol.md](agent-protocol.md).

## 10. Implemented API surface (Phase 3: dedicated sessions)

| Method and path | Purpose | Who |
|---|---|---|
| `POST /projects/{id}/sessions` `{agent_id, title, visibility}` | Open a session with an attached agent | member |
| `GET /projects/{id}/sessions` | Sessions you can see (yours, plus shared ones) | member |
| `GET /projects/{id}/sessions/{sid}` | One session | visible to you |
| `GET /projects/{id}/sessions/{sid}/messages` | Transcript | visible to you |
| `POST /projects/{id}/sessions/{sid}/turns` `{body, queue_if_offline}` | Send a message to the agent (one turn at a time) | owner |
| `POST /projects/{id}/sessions/{sid}/visibility` `{visibility}` | Share (`project`) or make private | owner |
| `POST /projects/{id}/sessions/{sid}/close` | Close the session | owner or admin |

Cancelling a session turn uses `POST /projects/{id}/invocations/{inv}/cancel`.

Data model: `agent_sessions` (owner, agent, title, visibility, harness session reference, status), `agent_session_messages` (transcript; never copied into events), and `agent_invocations.session_id` (channel is null for session turns).

Harness continuity: a session stores the harness's own session id from the first turn's completion, and sends it with every later turn. Claude Code resumes with `--resume`; Codex with `thread/resume`.

Privacy, enforced in four places:
- REST: sessions, transcripts, turns, and invocations are filtered in the query. Other members get 404, not 403.
- Live: each event carries its session's `visibility` and `owner_user_id`. The realtime handler delivers private events only to the owner, both on replay and live, and this includes streamed output deltas.
- Activity feed: private session events are removed for non-owners.
- Approvals: a private session's approval is listed only to its owner and the agent's owner, and only they can decide it.

Persisted events for session turns contain identifiers and visibility, never the message or delta text.
