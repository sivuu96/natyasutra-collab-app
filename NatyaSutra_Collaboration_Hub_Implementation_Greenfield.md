# NatyaSutra Collaboration Hub --- Implementation Brief for Claude Code

> **Purpose:** Build a self-hostable, cloud-hosted collaboration
> workspace that lets multiple developers work on the NatyaSutra port
> together while each person uses their own locally authenticated Claude
> Code and/or Codex installation.
>
> **How to use:** Place this file at the root of the
> collaboration-platform repository and ask Claude Code to implement it.
> Claude Code must inspect the repository first, create an
> implementation plan, then build the application incrementally. Do not
> attempt a large, unreviewed one-shot rewrite.

------------------------------------------------------------------------

## 1. Product vision

Build **NatyaSutra Hub**, a multi-user, agentic software-development
collaboration application.

The product combines:

1.  A shared web application for people, discussions, tasks, decisions,
    project knowledge and agent activity.
2.  A local agent bridge installed and run by each developer. It
    connects that developer's local Claude Code and/or Codex harness to
    the cloud workspace.
3.  Git repositories as the canonical source of truth for product
    artefacts and code.
4.  A hosted PostgreSQL database for collaboration state.
5.  A private-by-default archive of local AI conversations, with
    explicit controls for publishing selected conversations to the team.

The hub coordinates work; it must **not** become a cloud proxy for model
inference. Claude Code and Codex run on the developer's own computer and
authenticate using the developer's own supported local
login/subscription flow. The hub must never collect, transmit, store or
share provider credentials, cookies, tokens or subscription session
data.

The initial project is NatyaSutra, but the design should support
multiple projects and repositories later.

## 2. Core product principles

-   **Git is the source of truth** for requirements, architecture, data
    model, UI specifications, documentation, migrations, tests and
    application code.
-   **PostgreSQL is the source of truth** for users, teams, project
    membership, discussions, messages, tasks, decisions, agent
    registrations, session metadata, events and audit records.
-   **Object storage is for transcript originals and attachments**, not
    for canonical requirements or code.
-   Keep the collaboration platform's database separate from
    NatyaSutra's product database. Do not modify, migrate or replace the
    existing NatyaSutra product database as part of building this hub.
-   Local agents can disconnect. The cloud collaboration application
    must remain usable when no agents are online.
-   Conversation imports are private to their owner by default. Sharing
    with a project requires an explicit action or policy.
-   Every agent action must be attributable to a user, registered local
    agent, session and project.
-   Use least privilege, explicit approvals and auditable actions.
-   Prefer simple, deployable infrastructure over premature distributed
    systems.

## 3. Required user experience

### 3.1 Main navigation

Provide these primary areas:

-   **Workspace:** project overview, linked repositories, project
    knowledge and recent activity.
-   **Discussions:** channels, threads, comments, mentions, attachments
    and agent participation.
-   **Tasks:** task list/board, owners, assignees, status, priority,
    dependencies and linked discussions/decisions/PRs.
-   **Decisions:** proposed and accepted decisions, rationale,
    alternatives, participants and links to affected files.
-   **Agents:** registered agents, owner, harness, capabilities,
    online/offline status, current task and active session.
-   **Git / Artifacts:** repositories, branches, commits, pull requests
    and links to requirements, architecture, data-model and UI files.
-   **Conversation Archive:** the current user's imported conversations
    and conversations explicitly shared with the project.
-   **Activity:** audit trail and chronological project activity.

Build a responsive desktop-first interface. Make it usable on smaller
screens, but optimize the multi-pane collaboration experience for
desktop.

### 3.2 Discussions and shared chat

Users must be able to:

-   Create project channels (for example `general`, `product`,
    `architecture`, `frontend`, `backend`).
-   Send messages and reply in threads.
-   Mention people and agents using `@handle`.
-   Attach files with safe size/type limits.
-   Link messages to tasks, decisions, commits, pull requests and
    repository files.
-   Edit/delete their own messages subject to an auditable policy.
-   See agent status and streaming responses.
-   Convert a message or discussion into a task or decision.
-   Search discussions.

A message addressed to an agent should be routed to the local bridge
that owns that agent. A message addressed to a person should notify that
person. If an agent is offline, show that clearly and queue the request
only if the sender explicitly chooses queueing; never imply that an
offline agent has received or completed a request.

### 3.3 Agent participation model

Agents are named participants, not generic cloud chatbots.

Each registered agent has:

-   Stable ID and unique project-visible handle,
    e.g. `claude-architecture`, `claude-frontend`, `codex-backend`.
-   Owner user.
-   Harness type: `claude_code` or `codex`.
-   Display name, role, capabilities and optional description.
-   Local bridge/device registration.
-   Online/offline/busy state and last heartbeat.
-   Allowed projects and repositories.
-   Current session and current task, if any.

Support two modes:

1.  **Shared discussions:** an agent responds inside a project
    channel/thread after being mentioned.
2.  **Dedicated sessions:** a user opens a Claude-like session with a
    selected local agent. Session messages and streamed output are
    visible to authorized project members according to the session's
    sharing setting.

Do not assume every agent has the same capabilities. Model capabilities
explicitly.

### 3.4 Agent mention and routing

Example:

`@claude-architecture Review the current requirements and data model. Identify inconsistencies and post findings here.`

Expected flow:

1.  Backend resolves the handle and checks project membership and
    permissions.
2.  Backend creates an agent invocation and an auditable event.
3.  The owning local bridge receives the invocation over an
    authenticated outbound connection.
4.  The bridge validates project, repository, working directory and
    allowed operation.
5.  The bridge routes the request into the selected local harness using
    its supported integration.
6.  Output/events stream back through the bridge to the cloud.
7.  The cloud persists messages/events and broadcasts updates to
    authorized clients.
8.  Completion/failure/cancellation is visible in the conversation and
    activity feed.

Never expose a local bridge port to the public internet. The bridge
should initiate an outbound TLS connection to the backend and maintain
it with authenticated heartbeats/reconnect logic.

### 3.5 Local agent bridge

Build a small cross-platform local service/CLI, preferably Python
initially.

Responsibilities:

-   Pair/register a device with a user account using a short-lived,
    one-time pairing code.
-   Maintain an outbound authenticated connection to the backend.
-   Discover configured local harness executables and report
    availability.
-   Register one or more named agents.
-   Receive and acknowledge invocations.
-   Start/resume sessions through supported local harness interfaces.
-   Stream text and structured events to the backend.
-   Support cancellation and status reporting.
-   Maintain a local queue for transient network interruptions, with
    idempotent event IDs.
-   Associate each agent with explicitly configured local repository
    paths.
-   Create isolated Git worktrees/branches for assigned coding tasks
    where appropriate.
-   Sync local conversation history through a separate,
    opt-in/private-by-default pipeline.
-   Log locally without recording credentials or sensitive environment
    variables.

The bridge must not run arbitrary shell commands from the cloud by
default. Implement a policy/permission layer. Require explicit user
approval for potentially destructive or high-impact operations,
including deleting files, force-pushing, merging, changing credentials,
running deployment commands, or executing commands outside an approved
repository/worktree. Make approval state visible in the web UI and local
CLI.

#### Harness integration

Use official, supported integration surfaces and current vendor
documentation.

-   **Codex:** investigate and use the supported Codex App Server
    interface where available. Keep Codex authentication local.
-   **Claude Code:** investigate supported Claude Code CLI/session
    integration options. Invoke the user's locally installed and
    authenticated Claude Code process. Do not scrape private app
    internals, copy credentials, automate login flows, or route requests
    through a third-party proxy.
-   Hide harness-specific differences behind a common adapter interface,
    but do not flatten away useful native events, permission requests,
    tool status or error details.
-   If a harness integration is not available or its supported interface
    is unclear, implement a clearly marked adapter stub and document the
    limitation. Do not fake a successful integration.

Proposed adapter contract (adapt after inspecting current official
interfaces):

``` python
class AgentAdapter:
    def discover(self) -> list[AgentDescriptor]: ...
    async def start_session(self, request: StartSessionRequest) -> SessionDescriptor: ...
    async def send_message(self, session_id: str, message: str) -> None: ...
    async def stream_events(self, session_id: str): ...
    async def cancel(self, session_id: str) -> None: ...
    async def close_session(self, session_id: str) -> None: ...
```

### 3.6 Conversation history import

Implement a separate local conversation sync module.

Requirements:

-   Import supported Claude Code and Codex conversation histories from
    documented/local supported locations and formats.
-   Do not assume paths or formats without checking the installed
    harness/version. Make source paths configurable.
-   Initial import plus incremental sync.
-   Deduplicate with stable source identifiers and content hashes.
-   Preserve original raw transcript data where permitted, alongside a
    normalized representation.
-   Normalize messages, timestamps, roles, attachments/references, tool
    events and session metadata without discarding source-specific
    fields.
-   Associate each conversation with owner, source harness, local
    project/repository, branch and working directory when known.
-   Allow owner to review, exclude, redact and explicitly share
    conversations.
-   Default visibility is **private to owner**. A project conversation
    archive contains only explicitly shared conversations.
-   Support deletion and retention controls, including deleting stored
    raw and normalized copies.
-   Never import provider credentials, environment variables, unrelated
    files or unrelated conversations.
-   Detect likely secrets (API keys, tokens, passwords, private keys)
    and redact or block upload. Provide a review report before first
    bulk import.
-   Show import progress, skipped files, errors and last successful
    sync.
-   Make sync resumable and idempotent.

If a source history format cannot be safely/legitimately imported,
document it and provide a manual export/import path rather than
reverse-engineering private formats.

### 3.7 Tasks

Task fields:

-   Project, title, description and acceptance criteria.
-   Status: `backlog`, `todo`, `in_progress`, `in_review`, `blocked`,
    `done`, `cancelled`.
-   Priority: `low`, `medium`, `high`, `urgent`.
-   Creator, human owner and/or agent assignee.
-   Labels, due date and dependencies.
-   Linked discussion, decision, repository, branch, commit and pull
    request.
-   Activity history and comments.

Support assignment to a human or a registered agent. Agent assignment
must create an invocation only after the task is explicitly started or
dispatched. Do not treat merely assigning an agent as proof work has
begun.

### 3.8 Decisions

A decision record should include:

-   Title and decision statement.
-   Context/problem.
-   Options considered.
-   Rationale and consequences.
-   Status: `proposed`, `accepted`, `rejected`, `superseded`.
-   Proposer, decision owner and participants.
-   Date and revision history.
-   Related tasks, discussions and Git paths/commit SHAs.

Discussions are informal; decisions are explicit, durable records. Do
not automatically mark an AI-generated recommendation as accepted.

### 3.9 Git integration and project knowledge

Git remains canonical for:

-   Product requirements and user journeys.
-   System and component architecture.
-   Data model and schema definitions.
-   UI/design specifications.
-   Source code, migrations and tests.
-   Technical documentation.

The hub should link to GitHub repositories, branches, commits and pull
requests. Prefer GitHub APIs/webhooks for metadata and status; do not
copy the full repository into PostgreSQL.

Support Git-aware discussions. Store repository ID, path, commit SHA and
optional line range so a comment is anchored to the version reviewed. If
the file changes, preserve the original anchor and show that it refers
to an older revision.

Do not invent or impose a new NatyaSutra repository structure before
inspecting the existing repository. Preserve the current app structure
unless a change is justified and approved.

## 4. Recommended technical architecture

Use a modular monolith for the first release. Avoid microservices
initially.

### 4.1 Suggested stack

-   Frontend: React + TypeScript + Vite (or use the existing frontend
    framework if this repository already has one).
-   Styling: Tailwind CSS and an accessible component system.
-   Backend: Python + FastAPI.
-   Database: PostgreSQL.
-   ORM/migrations: SQLAlchemy 2 + Alembic, or an established equivalent
    already in the repo.
-   Realtime: WebSockets, with authenticated connections and server-side
    authorization.
-   Background work: a database-backed job/outbox pattern initially;
    introduce Redis/queue only when justified.
-   Object storage: S3-compatible storage for transcript originals and
    attachments.
-   Search: PostgreSQL full-text search initially.
-   Local bridge: Python CLI/daemon, using the same typed event contract
    as the backend.
-   Deployment: Vercel for frontend; Render for API/WebSocket service,
    background worker and managed PostgreSQL. Keep deployment
    configuration portable.
-   Git provider: GitHub initially, behind an integration interface.

If the repository already uses a sound alternative, explain the
trade-off and reuse it rather than introducing unnecessary rewrites.

### 4.2 Logical components

1.  Web frontend.
2.  REST API for CRUD and administrative operations.
3.  Realtime gateway for collaboration events and agent streaming.
4.  Agent invocation/router service.
5.  Local bridge client.
6.  Conversation import/sync service.
7.  Git provider integration.
8.  Background worker for notifications, imports and webhook processing.
9.  PostgreSQL.
10. S3-compatible object storage.

### 4.3 Realtime event envelope

Define and document a versioned event envelope. At minimum:

``` json
{
  "event_id": "uuid",
  "schema_version": 1,
  "event_type": "agent.output.delta",
  "project_id": "uuid",
  "conversation_id": "uuid",
  "agent_id": "uuid",
  "session_id": "uuid",
  "invocation_id": "uuid",
  "sequence": 12,
  "occurred_at": "ISO-8601 timestamp",
  "payload": {}
}
```

Use appropriate nullable fields for event types that do not need every
ID. Validate payloads with typed schemas. Make delivery idempotent.
Handle duplicate, delayed and out-of-order events. Persist important
events before broadcasting them.

Initial event types should cover:

-   `bridge.connected`, `bridge.heartbeat`, `bridge.disconnected`
-   `agent.registered`, `agent.status.changed`
-   `agent.invocation.created`, `agent.invocation.accepted`,
    `agent.invocation.rejected`
-   `agent.session.started`, `agent.session.completed`,
    `agent.session.failed`, `agent.session.cancelled`
-   `agent.output.delta`, `agent.output.message`
-   `agent.approval.required`, `agent.approval.resolved`
-   `conversation.message.created`, `conversation.message.updated`
-   `task.created`, `task.updated`, `decision.updated`

Document reconnection, replay and sequence handling.

## 5. Initial database model

Design normalized PostgreSQL tables with UUID primary keys, timestamps,
foreign keys, indexes and appropriate unique constraints. Add
tenant/project scoping to all relevant queries.

Suggested entities:

-   `users`
-   `teams`
-   `team_memberships`
-   `projects`
-   `project_memberships`
-   `repositories`
-   `project_repositories`
-   `channels`
-   `conversations`
-   `conversation_memberships`
-   `messages`
-   `message_attachments`
-   `tasks`
-   `task_dependencies`
-   `task_comments`
-   `decisions`
-   `decision_revisions`
-   `agents`
-   `agent_devices`
-   `agent_capabilities`
-   `agent_sessions`
-   `agent_invocations`
-   `agent_approvals`
-   `agent_events`
-   `conversation_import_sources`
-   `imported_conversations`
-   `imported_messages`
-   `audit_events`
-   `notifications`
-   `webhook_deliveries`

Avoid over-normalizing event payloads; use typed columns for
query-critical fields and JSONB for versioned provider-specific
metadata. Define indexes for project/time feeds, message search, task
status/assignee, agent status, source conversation IDs and idempotency
keys.

Add a migration strategy and seed data for local development.

## 6. Security, privacy and reliability requirements

These are release-blocking requirements, not optional enhancements.

### Authentication and authorization

-   Use a proven authentication provider or secure established auth
    implementation. Do not write custom password cryptography.
-   Support project/team membership and roles: at minimum `owner`,
    `admin`, `member`, `viewer`.
-   Enforce authorization on every API, WebSocket subscription, object
    download and agent invocation.
-   Use short-lived, revocable bridge/device credentials with rotation
    and pairing expiry.
-   Store secrets only in deployment secret managers/environment
    configuration, never in Git.
-   Apply rate limits and abuse protection to authentication, pairing,
    message sending and agent invocation.

### Agent safety

-   The bridge initiates outbound TLS connections only.
-   Never transmit provider credentials to the hub.
-   Explicitly scope each agent to approved project/repository/worktree
    paths.
-   Require human approval for configured sensitive operations.
-   Prevent path traversal and access outside allowed roots.
-   Do not run arbitrary cloud-supplied shell commands by default.
-   Record invocations, approvals, cancellations, tool outcomes and
    failures.
-   Make it possible to revoke a device immediately.

### Data privacy

-   Imported conversations private by default.
-   Explicit sharing and clear visibility labels.
-   Secret scanning/redaction before upload.
-   Configurable retention and deletion.
-   Audit access to private/shared transcripts.
-   Do not log message bodies, credentials or sensitive transcript
    contents unnecessarily.
-   Provide clear user-facing disclosure about what is synced and who
    can see it.

### Reliability

-   Reconnect with exponential backoff and jitter.
-   Heartbeat and stale-agent detection.
-   Idempotent event ingestion.
-   Durable persistence before broadcast for important events.
-   Resumable conversation imports.
-   Explicit states for queued, delivered, accepted, running, awaiting
    approval, completed, failed and cancelled.
-   Never display a queued invocation as running or completed.
-   Graceful handling of local machine sleep, network loss and process
    restart.

## 7. Deployment and configuration

Provide:

-   `frontend` deployment configuration for Vercel.
-   Backend and worker deployment configuration for Render.
-   PostgreSQL schema migrations.
-   Object storage configuration.
-   Environment variable examples in `.env.example` with placeholders
    only.
-   Local development using Docker Compose where practical.
-   Health/readiness endpoints.
-   Structured logs and basic operational metrics.
-   A deployment guide covering domains, TLS, CORS, WebSocket routing,
    database backups, storage permissions and secret configuration.

Do not hardcode production URLs, credentials or provider-specific
assumptions.

## 8. MVP scope and delivery phases

Build in working, testable increments. At the end of each phase, the app
must run and the implemented features must have tests.

### Phase 0 --- Repository reconnaissance and plan

Before coding:

1.  Inspect the entire existing repository: framework, package manager,
    code layout, conventions, tests, deployment files and current
    product functionality.
2.  Identify whether this repository is the NatyaSutra product or a new
    collaboration-platform repository. Do not mix the hub with the
    NatyaSutra product runtime/database.
3.  Inspect any available NatyaSutra architecture documents and existing
    requirements. If an architecture PDF or source file is unavailable,
    explicitly report that; do not invent its contents.
4.  Identify current official Claude Code and Codex integration
    documentation. Verify supported local interfaces and limitations
    before implementation.
5.  Write `IMPLEMENTATION_PLAN.md` with phases, architecture decisions,
    risks, file changes and acceptance criteria.
6.  Write `docs/architecture.md`, `docs/security.md` and
    `docs/agent-protocol.md`.
7.  Ask for clarification only where a decision genuinely blocks safe
    implementation. Otherwise use the defaults in this brief and record
    assumptions.

Do not start a destructive refactor.

### Phase 1 --- Cloud collaboration foundation

Implement:

-   Authentication and user profiles.
-   Teams, projects and memberships.
-   Project workspace shell and navigation.
-   Channels, discussions, messages and threads.
-   Tasks and decisions.
-   Activity/audit events.
-   PostgreSQL migrations and seed data.
-   Basic search.
-   Authorization tests.

**Acceptance criteria:** two separate users can sign in from different
browsers, join the same project, see and send messages in real time,
create/update tasks and create decision records. A user without project
membership cannot read project data.

### Phase 2 --- Local bridge and agent registry

Implement:

-   Local bridge CLI with pairing flow.
-   Authenticated outbound connection and heartbeat.
-   Device and agent registration.
-   Agent directory and online/offline state.
-   Common adapter interface.
-   First real harness adapter after validating its supported interface.
-   Invocation routing, acknowledgement, streaming, cancellation and
    errors.
-   Permission/approval workflow.
-   Audit trail.

**Acceptance criteria:** two developers on separate machines can
register local agents. A project member can mention an online agent, the
correct local bridge receives the request, the local harness responds,
and the response streams into the shared conversation. The hub never
receives provider credentials.

### Phase 3 --- Second harness and dedicated sessions

Implement the second harness adapter, dedicated sessions, session
history, resume/cancel behavior, and clear sharing/visibility controls.

**Acceptance criteria:** both Claude Code and Codex can be used through
their respective local supported interfaces, subject to documented
vendor capabilities. If one integration is blocked by unsupported
interfaces, document the exact limitation and keep the adapter isolated.

### Phase 4 --- Conversation import and archive

Implement opt-in initial import, incremental sync, normalization, raw
archive storage, private-by-default visibility, secret scanning,
review/redaction, search, sharing and deletion.

**Acceptance criteria:** a developer can import their supported local
histories, review what will be uploaded, exclude/redact content, and
share only selected conversations. Duplicate syncs do not create
duplicate conversations or messages.

### Phase 5 --- Git-aware collaboration and worktrees

Implement GitHub integration, repository/branch/commit/PR links,
file/line anchored comments, task-to-branch association and local
isolated worktree creation.

**Acceptance criteria:** an agent task can be associated with a
dedicated branch/worktree; the resulting commit/PR is linked to the task
and visible in project activity. No agent silently writes into another
agent's worktree.

### Phase 6 --- Hardening and deployment

Implement production deployment, backups, monitoring, rate limits,
security review, load/reconnect tests, documentation and onboarding.

**Acceptance criteria:** a new team can deploy the service, invite two
developers, connect local agents and collaborate without sharing
provider credentials.

## 9. Testing strategy

Include:

-   Unit tests for models, permissions, routing, adapters and event
    validation.
-   API integration tests.
-   WebSocket tests for authorization, reconnect and event ordering.
-   Multi-user tests proving project isolation.
-   Bridge tests with fake/mock harness adapters.
-   Adapter contract tests.
-   Conversation import tests for deduplication, redaction and
    resumability.
-   Git worktree tests.
-   Security tests for path traversal, unauthorized project access,
    expired/revoked device credentials and unsafe invocation rejection.
-   End-to-end test for: user A mentions agent owned by user B → B's
    bridge receives request → agent streams response → both users see
    it.

Do not require real provider subscriptions in automated CI. Use fake
adapters in CI and document manual integration tests for real local
harnesses.

## 10. Documentation to deliver

Keep documentation current as implementation changes:

-   `README.md` --- product overview and quick start.
-   `IMPLEMENTATION_PLAN.md` --- phases and status.
-   `docs/architecture.md` --- system architecture and data flows.
-   `docs/database.md` --- ERD/schema and migration instructions.
-   `docs/security.md` --- threat model, permissions, secrets and
    privacy.
-   `docs/agent-protocol.md` --- bridge handshake, event types, retries
    and invocation lifecycle.
-   `docs/claude-code.md` --- supported local integration and setup.
-   `docs/codex.md` --- supported local integration and setup.
-   `docs/conversation-import.md` --- sources, formats, privacy and
    sync.
-   `docs/deployment-render-vercel.md` --- production deployment.
-   `docs/development.md` --- local development and tests.

## 11. Explicit non-goals for the first release

Do not build these unless the core workflow is working:

-   A replacement for Claude Code or Codex.
-   A cloud inference proxy or shared provider account.
-   A general-purpose IDE or remote desktop.
-   Autonomous agent-to-agent delegation without human
    visibility/controls.
-   Automatic merging or deployment.
-   Complex enterprise billing.
-   Microservices/Kubernetes.
-   A custom vector database before PostgreSQL search proves
    insufficient.
-   Full mirroring of Git repositories into the collaboration database.

## 12. Instructions to Claude Code

Act as the lead engineer implementing this specification.

1.  Start with repository reconnaissance. Do not immediately generate
    files or rewrite the project.
2.  Summarize the current repository and identify the safest
    implementation path.
3.  Create `IMPLEMENTATION_PLAN.md` with a concrete task checklist and
    mark assumptions.
4.  Implement Phase 1 first unless the repository already contains a
    working equivalent.
5.  Work in small vertical slices. After each slice, run formatting,
    linting, type checks and tests.
6.  Do not leave core paths as mock implementations while claiming the
    phase is complete. Clearly label stubs and explain what remains.
7.  Keep interfaces typed and documented. Prefer maintainable, modular
    code over clever abstractions.
8.  Add database migrations for schema changes; do not rely on manual
    production SQL.
9.  Add authorization checks server-side; frontend hiding is not
    security.
10. Never store or transmit Claude/Codex provider credentials.
11. Verify current official vendor integration documentation before
    implementing harness adapters. Do not assume unsupported APIs or
    reverse-engineer private formats.
12. Do not invent contents of NatyaSutra source documents. If the
    architecture PDF is not available in the repository, ask the user to
    add it or proceed with the collaboration platform without making
    claims about the product schema.
13. Preserve existing NatyaSutra application code and product database.
    The hub is a separate application/system.
14. At the end of each phase, report: what works, files changed,
    commands/tests run, known limitations, security considerations and
    the next concrete tasks.
15. Keep the application runnable throughout development.

**First action:** inspect the repository and produce the Phase 0
reconnaissance report and `IMPLEMENTATION_PLAN.md`. Do not start Phase 1
until the plan and repository boundaries are clear.
