# Status and roadmap

This page records what is built, the decisions behind it, and what comes next. The design is in [docs/](docs/); this page is the status view.

## Built

| Area | State |
|---|---|
| Accounts, teams, projects, membership and roles | Done |
| Password reset by emailed link (Brevo), one-use links that expire in 30 minutes | Done |
| Channels, messages (threads, edit, delete), live updates with replay | Done |
| Tasks, decisions with revisions, activity feed, audit log, search | Done |
| Cloud agents: mention and session runs on a Cloud Run service, one warm instance | Done |
| Run reliability: one-use leases, retries, cancel, stuck-run sweeper | Done |
| Progress above the message box while a run works | Done |
| Agent replies rendered as Markdown, with raw HTML not rendered | Done |
| Files returned by a run (up to five, text, downloadable by people who can see the run) | Done |
| PDF reading for agents (poppler in the runtime image, read in ranges of 20 pages) | Done |
| Agents that push to their own branch (operator flag, never main or master, no pull requests) | Done |
| `@` suggestions for agents and people | Done (suggestions only; no notifications) |
| Sessions: private or shared, one turn at a time, transcript replay | Done |
| Sessions: project owners can send in shared sessions | Done |
| Sessions: creator, project owners and admins can delete (soft delete) | Done |
| Model key held by a proxy in the runtime, never in the agent's process | Done |
| Agent file and search tools limited to the run's folder by permission rules | Done |
| Local bridge (agents on a member's machine, Codex) | Removed. Cloud agents are the only way to run an agent. |
| Render web service, Supabase database, Cloud Run runtime | Deployed |

## Decisions

| Decision | Reason |
|---|---|
| Hub on Render, one instance; database on Supabase in us-west-2 | Free plans, no team member installs anything. One instance keeps the in-process worker and realtime simple. |
| Agent runs on a Cloud Run service, one warm instance, one run at a time | The warm instance removes the container start. Each run still starts with nothing: a new clone and a new Claude Code process. |
| Anthropic team API key in Secret Manager, held by a proxy in the runtime | Usage belongs to the team, not a person. The key stays out of the agent's process, so the agent's tools cannot read it. |
| GitHub App; one repository per run, read-only unless the agent is flagged for writing | Agents read repositories. Only flagged agents, on branches other than main, can push to their own branch. The App key lives in Secret Manager. |
| The agent's read and search tools are limited by permission rules to the run's folder | Without the rules, the tools reach any file the process can read. Tested in the Linux runtime image with the pinned CLI. |
| Agents return files as blocks in the reply. Flagged agents may also edit their clone and push it | The CLI cannot confine writes to one folder in print mode (tested), so writing tools are not allowed. The runtime takes the blocks out of the reply and keeps the files. |
| Session memory by transcript replay, capped at 40 turns or 20,000 characters | Cloud runs start fresh. Replay needs no stored Claude state. |
| Model `claude-haiku-5-5` by default | Cost and speed for team questions. Configurable with `CLAUDE_MODEL`. |
| Email through Brevo | Sends to any address from a verified sender address, with no domain required. Resend needed a domain for anything but its test sender. |
| Session deletion is a soft delete (`deleted_at`) | The rows are kept, but every list, page, run and download hides them. |
| Replies render as Markdown, with raw HTML not rendered | Replies come from an AI that reads repository text, so they are treated as untrusted. |
| Bridge removed | Cloud covers the team's needs, and the bridge doubled the code paths for agents, approvals and devices. |

## Next

Ordered by what the team is likely to need first.

1. **Mention notifications.** Members can be suggested with `@`, but they are not notified. Decide the channel (in-app, email) before building it.
2. **Session memory.** If sessions outgrow the replay cap, resume the Claude Code conversation on the warm instance. See Deferred below.
3. **Egress controls for runs.** Runs can reach any host through the runtime service. Restrict the service's network before running untrusted repositories.
4. **Usage visibility.** Show the team how many runs and tokens each agent uses, so the shared key's spend is visible.
5. **Scaling the hub.** Only needed if the team grows: a shared rate-limit store, a message bus for realtime events, and a leader election for the worker.

## Deferred

- **Resuming a session's CLI conversation on a warm instance.** Not built; the hub keeps sending the transcript, which is correct on any instance. The idea, for when it is worth the complexity:
  - The runtime keeps, per session, a home folder and a clone, and the CLI's own session ID. Follow-ups run `--resume` with only the new message. First turns use `--session-id`.
  - The hub names the previous turn in the exchange. The runtime resumes only if its last successful turn is that one; otherwise it starts from the transcript. A failed turn discards the workspace.
  - Needs session affinity so follow-ups reach the instance that holds the session. Without it most follow-ups miss and fall back, so the gain is small. Investigate Cloud Run session affinity (cookie) first.
  - Idle workspaces removed after 30 minutes, at most five per instance; on Cloud Run these use instance memory.
- Conversation import and archive, and git-aware features such as worktrees (from the original brief).
- Hardening beyond the controls in [security](docs/security.md).

## Open items

- Decide whether admins should see other people's private sessions in the session list, so they can delete them from the page. Today the API allows it, but the list shows only what the person can see.
- Decide whether `deploy/.env` (local Compose) should keep its old variable name. It must define `RUNTIME_TOKEN_SECRET` to start.
- Confirm the service's minimum instances, concurrency and maximum instances after each update (see [deployment](docs/deployment.md#35-the-service)).
- Frontend unit tests do not run on Node 26. Use Node 22 LTS to run them.
