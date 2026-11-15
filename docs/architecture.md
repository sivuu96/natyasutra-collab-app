# Architecture

This page shows how the pieces fit together and how a request flows through them. Each component has its own page:

- [Backend](components/backend.md): the API, data model and services.
- [Frontend](components/frontend.md): the web app and its live updates.
- [Execution](components/execution.md): the run queue, leases, the Cloud Run provider and the sweeper.
- [Runtime](components/runtime.md): the service that runs an agent.

## 1. Components

```
 Browser (React app)
    │  HTTPS: /api/v1/...        WebSocket: /api/v1/ws/projects/{id}
    ▼
 Hub (FastAPI, one process on Render)  ── SQL ──▶ PostgreSQL (Supabase)
    │  execution worker (in the hub process)
    │  POST /runs with an identity token (dispatcher service account)
    ▼
 Cloud Run service, one warm instance, one run at a time  (the runtime)
    │  reads secrets ──▶ Secret Manager (Anthropic key, GitHub App key)
    │  installation token + git clone ──▶ GitHub (read-only)
    │  claude -p (read-only tools) ──▶ Anthropic API
    │  lease exchange + events ──▶ Hub (HTTPS, bearer token)
```

- The **hub** serves the API, the built web app and the WebSocket. It owns all state. It is the only component that talks to the database.
- The **execution worker** runs inside the hub. It sends each run to the runtime service.
- A **run** is one request to the runtime service. It carries a one-use lease, does one piece of work, reports its events to the hub and returns.
- The **runtime** is the service's container image. It contains Claude Code, a pinned version, and no credentials of its own.

Nothing runs on a team member's machine.

## 2. A mention becomes a reply

1. A person posts a message with `@claude` in a channel. The hub saves the message, one **invocation** for each attached agent mentioned, and an **outbox** row, all in one transaction. It then broadcasts `message.created` and `agent.invocation.created`.
2. The worker claims the outbox row. It mints a **run lease**: a random token, stored only as a hash, valid for 10 minutes and usable once. It sends the lease and a new execution name to the runtime service, signed with the dispatcher's identity token. It binds the lease to that execution name.
3. The run starts. The runtime calls `POST /api/v1/internal/executions/exchange` with the lease and the execution name. The hub checks the lease, marks the lease used, moves the invocation to `accepted`, and returns the prompt, the repository and a **runtime token** (15 minutes, scoped to this invocation).
4. The runtime reads the Anthropic key from Secret Manager, gets a GitHub installation token for the one repository, clones it read-only, and runs Claude Code with read-only tools.
5. The runtime sends events to `POST /api/v1/internal/executions/events`: `started`, `progress`, `output`, `message`, `completed`, or `failed`, with a heartbeat every 30 seconds. Each event has a sequence number, and the hub applies each one at most once.
6. The hub turns events into live updates: progress frames for the progress line, output for the streaming text, and the reply as a channel message. The invocation becomes `completed` or `failed`.

If the agent stops responding, the sweeper fails the run (see [Execution](components/execution.md)). If a person presses Stop, the invocation becomes `cancelled` at once, and the runtime stops at its next heartbeat.

## 3. A session turn

A session turn follows the same path with two differences:

- The invocation belongs to a session, not a channel, and its reply goes to the session transcript.
- The run's prompt includes the session's earlier turns. The runtime keeps nothing between runs, so the earlier conversation is sent again each time, up to 40 turns and 20,000 characters. The stored invocation keeps only the new message. See [Backend](components/backend.md#sessions) for the details.

Only one turn may be in progress per session. A second turn is refused until the first finishes.

## 4. Run states

```
queued ──(runtime exchanges its lease)──▶ accepted ──(first progress or output)──▶ running
   │                                         │                                       │
   │                                         ├──── completed ◀─────────────────────────┤
   │                                         ├──── failed (runtime error, sweeper, dispatch failure)
   └──── cancelled (anyone allowed to stop it)
```

- `queued`: waiting for the worker, or waiting for the runtime to exchange its lease.
- `accepted`: the runtime is up and holds its run context. No output yet.
- `running`: output has started.
- `completed`, `failed`, `cancelled`: final. A final state is never changed.

## 5. Live updates

Each project has one WebSocket at `/api/v1/ws/projects/{id}`. The client sends `since=<sequence>` to replay what it missed, then receives events as they happen.

- Persistent events (messages, tasks, decisions, membership, runs) are stored with a project sequence number before they are sent.
- Live-only events (streamed output text, progress lines) are not stored. A client that reconnects during a run catches up from the next event.
- Events about a private session are sent only to its owner, both on replay and live. Events carry the session's visibility and owner, so the realtime handler can filter them.

## 6. Data

PostgreSQL (SQLite in development and tests) holds:

| Area | Tables |
|---|---|
| Identity | `users` (with `handle`), `auth_sessions`, `password_resets`, `teams`, `team_memberships` |
| Projects | `projects`, `project_memberships`, `channels`, `project_events`, `audit_events` |
| Work | `messages`, `tasks`, `decisions`, `decision_revisions` |
| Agents | `agents`, `project_agents`, `agent_invocations`, `invocation_files`, `agent_sessions`, `agent_session_messages` |
| Runs | `execution_outbox`, `invocation_leases`, `run_events` |

Migrations are in `backend/migrations/versions/`. The current head is `e7f8a9b0c1d2`.

## 7. Where things run

| Piece | Where |
|---|---|
| Hub (API, web app, WebSocket, worker) | Render web service, one instance |
| Database | Supabase PostgreSQL |
| Runtime | Google Cloud Run service `collab-agent-runtime-service`, project `natyasutra-agents`, region `us-west1`. One warm instance, one run at a time. |
| Runtime image | Artifact Registry, repository `agent-runtime` |
| Secrets | Google Secret Manager (Anthropic key, GitHub App private key) |
| Repositories | GitHub, through a GitHub App with read-only contents access |

The hub's single-instance rule is covered in [deployment](deployment.md#known-limits).
