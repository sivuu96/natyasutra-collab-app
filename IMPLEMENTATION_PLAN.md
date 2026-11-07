# NatyaSutra Collaboration Hub — Implementation Plan

Status legend: `[x]` done · `[ ]` not started · `[~]` in progress · `[!]` blocked

Source of truth for scope: `NatyaSutra_Collaboration_Hub_Implementation_Greenfield.md`.

---

## 0. Phase 0 — Reconnaissance (current)

### 0.1 Repository inspection

| Item | Finding |
|---|---|
| Git repository | Not initialised in `collab-app/`. |
| Contents | One file: `NatyaSutra_Collaboration_Hub_Implementation_Greenfield.md`. No existing code, no framework, no tests, no deployment files. |
| Framework / package manager | None yet (greenfield). |
| Existing product code | None present. |
| Tooling on this machine | Python 3.13.11 ✔ · Git 2.53.0 ✔ · Docker 29.2.0 ✔ · **Node.js / npm not installed ✘** |

### 0.2 Source document analysis

**Hub specification (`…Greenfield.md`)** — the build contract. Key requirements:
- Modular monolith; React + TypeScript + Vite frontend; FastAPI backend; PostgreSQL; SQLAlchemy 2 + Alembic; WebSockets; S3-compatible object storage; PostgreSQL full-text search.
- Local bridge (Python) that connects **outbound** to the hub. The hub never proxies model inference and never stores provider credentials.
- Harness adapters: Claude Code and Codex, behind a common `AgentAdapter` interface. Unverified integrations must be clearly-marked stubs.
- Release-blocking security: server-side authorisation everywhere, revocable device credentials, private-by-default conversation imports, approval gates for destructive operations, audit events.
- Six phases. Phase 1 acceptance: two users in different browsers see and send messages in real time; a non-member cannot read project data.

**NatyaSutra Architecture PDF (v2.0)** — *product* context only. It was attached to this conversation but is **not** in the repository. It describes the NatyaSutra product (visitor intent engine, 113-table product schema, CodeIgniter/MySQL). Per the brief, it does not define the hub's architecture, and this plan makes **no claims** about the product schema. The hub uses its own PostgreSQL database and its own tables.

**Master prompt (pasted)** — restates the brief and asks for a working application, not a scaffold. It also states the repo contains both source documents; only the brief is present. This discrepancy is recorded as an open item (§0.5).

### 0.3 Verified vendor integration surfaces (as of 2026-11-07)

| Harness | Surface | Verified from | Status |
|---|---|---|---|
| Claude Code | Headless CLI: `claude -p "<prompt>" --output-format stream-json --verbose`. Emits newline-delimited JSON with an init event; `--include-partial-messages` gives token deltas. Unattended permission denials surface as system messages. Session resume/management is documented under "Manage sessions". | code.claude.com/docs/en/headless, /docs/en/sessions | **Verified at the documentation level.** Exact stream-json event field names are *not* confirmed; must be checked against the live CLI output during Phase 2. |
| Claude Code | Agent SDK (Python/TS) is an alternative to subprocess use. | docs.claude.com Agent SDK overview | Candidate for Phase 2; decide after prototyping. |
| Codex | `codex app-server`: JSON-RPC 2.0 over **stdio** (default, newline-delimited). Handshake: `initialize` → `initialized`. Thread and turn methods; server-initiated approval requests; backpressure error `-32001` with retry. WebSocket transport is **experimental / not for production**. | developers.openai.com/codex/app-server; openai/codex `codex-rs/app-server/README.md` | **Verified surface, exact method names unconfirmed.** Community guides list method names; these must be checked against the official README and the `generate-ts` schema before coding. |

Consequence: Phase 2 uses **stdio** for Codex and **subprocess `-p` + stream-json** for Claude Code, each behind an adapter. Neither uses private app internals, credential files or a proxy.

### 0.4 Architecture decisions (defaults; recorded as assumptions)

| # | Decision | Rationale | Reversible? |
|---|---|---|---|
| D1 | Separate app under `collab-app/`; no contact with NatyaSutra runtime or DB. | Brief §2 and §12, item 13. | Yes |
| D2 | Modular monolith: `backend/` (FastAPI), `frontend/` (React+TS+Vite), `bridge/` (Python), `docs/`, `deploy/`. | Brief §4. | Yes |
| D3 | PostgreSQL via Docker Compose for local dev; SQLAlchemy 2 + Alembic migrations. | Brief §4.1, §5. | Yes |
| D4 | Authentication: local email+password with **argon2-cffi** hashing (established library, no custom crypto), server-side opaque session tokens stored hashed, plus an OIDC hook for later. | Brief §6 forbids custom password crypto; keeps self-hosting simple. | Medium — revisit if the team wants SSO first. |
| D5 | Bridge credentials: short-lived access tokens refreshed from a long-lived device secret; the device secret is stored hashed server-side and revocable per device. | Brief §6 "revocable device credentials". | Yes |
| D6 | Realtime: FastAPI WebSockets with per-connection authorisation; events persisted to `agent_events`/`audit_events` before broadcast; `sequence` per stream for ordering and replay. | Brief §4.3, §6. | Yes |
| D7 | Background work: database-backed outbox table polled by a worker process. No Redis or broker. | Brief §4.1 "introduce Redis only when justified". | Yes |
| D8 | Search: PostgreSQL `tsvector` + GIN index. | Brief §4.1. | Yes |
| D9 | Object storage: local filesystem adapter in dev behind an S3-compatible interface; S3 adapter later. | Brief §4.1, §7. | Yes |
| D10 | Git provider: GitHub behind an interface; no GitHub calls in Phase 1. | Brief §3.9. | Yes |
| D11 | Frontend: React + TypeScript + Vite + Tailwind. **Blocked until Node.js LTS is installed** (see §0.5). | Brief §4.1. | Yes |
| D12 | Bridge: Python CLI + daemon. Claude Code adapter via `claude -p` subprocess; Codex adapter via `codex app-server` stdio. Both start as clearly-labelled stubs until verified live. | Brief §3.5. | Yes |
| D13 | Monorepo, no microservices, no Kubernetes. | Brief §11. | Yes |

### 0.5 Blockers and open items

| # | Item | Impact | Owner / action |
|---|---|---|---|
| B1 | ~~Node.js / npm not installed.~~ **Resolved:** Node v26.13.0, npm 11.20.0 installed. | Frontend can now be scaffolded (task 1.9). | — |
| B2 | **NatyaSutra PDF not in the repository.** | None on hub architecture (the brief is self-contained). Noted so no product claims are made. | User may add it to `docs/reference/` if wanted; not required. |
| B3 | Claude Code stream-json event schema not yet confirmed. | Phase 2 adapter must be validated against the installed CLI version. | Verify in Phase 2 with a real local run. |
| B4 | Codex app-server method names not yet confirmed from official source. | Phase 2 Codex adapter. | Verify against `codex app-server` README / generated schema in Phase 2. |
| B5 | Real GitHub, Render and Vercel accounts not available. | Phase 5–6 deployment cannot be verified. Will be documented as "not verified". | User supplies credentials/accounts when ready. |

None of B1–B5 blocks finishing Phase 0 or starting Phase 1 backend work.

### 0.6 Phase 0 deliverables

- [x] Repository reconnaissance (this file §0.1)
- [x] Source analysis (§0.2)
- [x] Vendor surface verification (§0.3)
- [x] `IMPLEMENTATION_PLAN.md` (this file)
- [x] `docs/architecture.md`
- [x] `docs/security.md`
- [x] `docs/agent-protocol.md`

---

## 1. Phase 1 — Cloud collaboration foundation

Acceptance (brief §8): two users sign in from different browsers, join one project, exchange messages in real time, create/update tasks, create decisions; a non-member cannot read project data.

Work breakdown (vertical slices, each with tests):

- [x] **1.1** Backend skeleton: FastAPI app factory, settings from env, `/health` and `/ready`, structured JSON logging, request IDs. Tests: health, readiness, request ID, settings validation. *(Error-handling middleware is not yet built; deferred to 1.3.)*
- [x] **1.2** Database: SQLAlchemy 2 models for users, teams, team and project memberships, channels, messages, tasks, decisions, decision revisions, audit_events. Alembic initial migration `b38873a6b2ce`. Docker Compose PostgreSQL 16 running and healthy. Verified: 16/16 tests pass on PostgreSQL (including FK and metadata integration tests), migration upgrade/check/downgrade on PostgreSQL, `/ready` returns 200 against the live database.
- [x] **1.3** Auth: registration, login, logout, server-side sessions (only SHA-256 of the token stored), argon2 password hashing, idle and absolute session expiry, deactivated users locked out, per-IP and per-email rate limits, double-submit CSRF on every state-changing API call. Tests: 16 auth tests including wrong password, logout revocation, idle and absolute expiry, rate limit, CSRF rejection.
- [x] **1.4** Teams, projects, memberships with roles `owner|admin|member|viewer`. One authorisation module (`app/services/authz.py`) used by every project route. Tests: **project-isolation suite** — a non-member gets 404 on every project-scoped read and write (checked by enumerating the endpoints), and cannot open the WebSocket; admins cannot grant or touch owners; the last owner cannot be removed; removed members lose access immediately.
- [x] **1.5** Channels, messages, threads (reply within the same channel only), author-only edit, soft delete (author or admin), `@handle` extraction recorded in the persisted event. Tests: threading, cross-channel reply rejected, edit/delete permissions, channel name validation.
- [x] **1.6** Realtime gateway at `/api/v1/ws/projects/{id}?since=N`: Origin allow-list, cookie authentication, membership check at connect and every 30 s, events persisted in `project_events` with per-project sequence before broadcast, replay from `since`, duplicate suppression, heartbeat, slow-consumer resync. Tests: live delivery, replay, `since` skipping, bad Origin rejected, unauthenticated rejected. Verified end to end through the Vite proxy.
- [x] **1.7** Tasks (status, priority, assignee must be a project member, filters) and decisions. Decisions start `proposed` even if the client sends `accepted`; only the decision owner or an admin can accept or reject; every change writes a revision. Tests: lifecycle, filters, revision history, status permission, no automatic acceptance.
- [x] **1.8** Activity feed (sequenced, resumable with `after`), admin-only audit log (contains no message bodies), project-scoped search (PostgreSQL `websearch_to_tsquery` on PostgreSQL; literal `LIKE` on SQLite, with `%` and `_` escaped). Tests: scope isolation, wildcard handling, deleted messages not searchable — run on both SQLite and PostgreSQL.
- [x] **1.9** Frontend: React 19 + TypeScript + Vite 8 + Tailwind 4. Login, register, project list and creation, workspace with live status indicator, discussions (channels, threads, edit/delete, mention highlight), tasks, decisions with revisions, activity and audit, people management, search. Unit tests (18) for the API client, realtime backoff/dedupe/stop-on-1008, and the login page. Production build succeeds. Verified in a real browser (Microsoft Edge via Playwright): register → create team and project → post message → create task → record decision → activity → sign out.
- [x] **1.10** Seed script `backend/scripts/seed.py` (idempotent; generates a random password per run and prints it once; nothing secret is written to the repo). Documented in `docs/development.md`.

Phase 1 exit status:
- Backend: `pytest` 66 passed, 2 skipped on SQLite; **68 passed on PostgreSQL 16** (`TEST_DATABASE_URL` set). `ruff check` and `ruff format --check` clean.
- Frontend: `npm run typecheck` clean, `npm test` 18 passed, `npm run build` succeeds.
- End-to-end: `backend/scripts/smoke_e2e.py` passes through the Vite proxy (two users, live message and task events delivered over WebSocket).
- Acceptance: two users in separate sessions see each other's messages live (smoke test, WebSocket path). Non-members are denied on every project endpoint (isolation suite).
- Known limitations carried forward: rate limiter and realtime hub are in-process (single API instance only); no password reset; no email; mentions do not yet notify; agent assignment is Phase 2.

## 2. Phase 2 — Local bridge and agent registry

Status: **implemented and verified end to end with the real Claude Code CLI** (Phase 2 acceptance, single-machine version). Tests: backend 97 on SQLite and 99 on PostgreSQL (includes the bridge flow and contract tests); bridge 40; frontend 23.

Done:
- [x] Pairing: one-time code created in the web app, redeemed by `collab-bridge pair`. Single-use, 10-minute expiry, hashed at rest, rate-limited. Covered by tests.
- [x] Device registry and revocation: revoking closes the socket (4003), revokes the device's agents, fails active work, and refuses future token exchange. Covered by tests.
- [x] Short-lived tokens (15 min JWT) exchanged from the device secret; bridge sockets accept only bearer tokens, never cookies. Covered by tests.
- [x] Outbound WebSocket with heartbeat (25 s / 60 s timeout), reconnect with jittered backoff, and an outbox replayed until acknowledged. Covered by tests.
- [x] Agent registry: handles are global and first-come, never taken over. The bridge is told when a handle is refused. Covered by tests.
- [x] Mention routing: only agents attached to the project are invoked; offline agents are reported as offline, never as running; queueing only when the sender asks. Covered by tests.
- [x] Invocation lifecycle with explicit states and terminal states that cannot change. Covered by tests.
- [x] Approvals: requested by the bridge, decided by the agent's owner or a project admin, never by the requester. Disconnects deny pending approvals. Covered by tests.
- [x] Cancellation for queued and running work. Covered by tests.
- [x] Device isolation: a device cannot change another device's invocations. Covered by tests.
- [x] `AgentAdapter` interface; Claude Code adapter verified against live CLI output (`docs/claude-code.md`); Codex stub that refuses to run (`docs/codex.md`); deterministic fake harness for tests.
- [x] End-to-end run (`scratchpad` driver, not in the repo): paired bridge, real Claude reply "BRIDGE OK" attributed to the agent, then a write denied, approved by a human in the hub, and the file written in the approved folder.

Not yet done, or not verified:
- [ ] **Two developers on two machines.** Verified only with one machine: two devices owned by one user, plus cross-user handle refusal. Two real machines are still to be tested.
- [ ] Codex adapter (Phase 3).
- [ ] Hub-side presence is per process; a multi-instance deployment needs a shared registry first (`docs/security.md` §10).
- [ ] Bridge run as a background service on Windows/macOS/Linux is not packaged; currently run in a terminal.
- [ ] Frontend Agents and Devices pages are type-checked and unit-tested for their permission rules, but were not exercised in a browser with a live bridge.

## 3. Phase 3 — Second harness and dedicated sessions

Status: **implemented and verified end to end with both harnesses.** Tests: backend 112 on SQLite (stable across 4 runs) and 114 on PostgreSQL; bridge 51; frontend 27.

Done:
- [x] Codex adapter over `codex app-server` (stdio JSON-RPC). Method names and shapes confirmed from the CLI's own generated schema (codex-cli 0.167.0), not from community guides. Live: plain turn streamed and completed; a write request raised an approval, was answered, and the file was created. Stand-in tests cover handshake, resume, streaming, approval accept and decline, cancel, and missing binary. (`docs/codex.md`)
- [x] Live approvals for harnesses that pause mid-run (Codex), alongside the rerun-after-approval flow (Claude Code). Cancelling a run that waits on an approval releases it immediately.
- [x] Dedicated sessions: open with an attached agent, turns with one-at-a-time enforcement, transcript, owner-only sending, sharing, closing. Session turns resume the harness's own thread or session. Live E2E: both harnesses remembered a word across two turns (PINEAPPLE).
- [x] Visibility controls: private by default. Enforced on REST, live WebSocket (replay and deltas), activity feed, and approvals. Tests cover each path, including a live socket check.
- [x] Fixed during testing: a welcome frame that arrived before the agent was registered (now sent after registration); a process-transport leak on loop shutdown (`aclosing`); a worker that started before its queue existed (Phase 2).

Not done or not verified:
- [ ] A write approval raised inside a dedicated session, end to end with a live harness. Covered by unit tests and the hub's visibility tests, not by a live run.
- [ ] Session history beyond the transcript (search, export). The transcript is listed; search is not built.
- [ ] Browser check of the Sessions view with live agents. Type-checked, unit-tested for streaming rules, not exercised in a browser.
- [ ] Two real machines (carried over from Phase 2).
- [ ] Making a session private after sharing does not retract events already delivered (`docs/security.md`).

## 4. Phase 4 — Conversation import and archive
Opt-in import, raw archive in object storage, normalisation, secret scanning and redaction, review step, idempotent sync, private-by-default sharing, deletion.

## 5. Phase 5 — Git-aware collaboration and worktrees
GitHub integration behind an interface (B5 for live verification), anchored comments, task↔branch links, isolated worktree creation with no cross-agent writes.

## 6. Phase 6 — Hardening and deployment
Render (API, worker, managed PostgreSQL) and Vercel (frontend) configuration, backups, monitoring, rate-limit review, reconnect tests, onboarding docs. Nothing is claimed deployed until it has been deployed and verified (B5).

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Harness CLI output or app-server API changes between versions. | Adapter isolation; contract tests against recorded fixtures; pin and record the tested CLI version in `docs/claude-code.md` / `docs/codex.md`. |
| Scope too large for one pass. | Strict phase gates; each phase ends runnable and tested. |
| Auth implemented incorrectly. | Use established libraries only; authorisation tests required before any project-scoped feature merges. |
| Private transcripts leaking into shared views. | Visibility enum defaults to `private`; sharing is an explicit write with an audit event; tests for the default. |
| Bridge executing unsafe commands. | No cloud-supplied shell execution in the protocol at all; only typed operations; path allow-list checks. |

## 8. Assumptions log

- A1: The collaboration hub is a separate application; this repo is not the NatyaSutra product repo. (Brief §2.)
- A2: Team size is small; a single PostgreSQL instance is sufficient for Phase 1–3.
- A3: Developers have local, already-authenticated Claude Code and/or Codex installs.
- A4: No NatyaSutra product schema is needed to build the hub.
- A5: Phase 1 uses human assignees only; agent dispatch arrives with Phase 2.
