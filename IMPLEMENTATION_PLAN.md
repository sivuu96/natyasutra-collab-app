# Status and roadmap

This page records what is built, the decisions behind it, and what comes next. The design is in [docs/](docs/); this page is the status view.

## Built

| Area | State |
|---|---|
| Accounts, teams, projects, membership and roles | Done |
| Channels, messages (threads, edit, delete), live updates with replay | Done |
| Tasks, decisions with revisions, activity feed, audit log, search | Done |
| Cloud agents: mention and session runs on Cloud Run Jobs | Done |
| Run reliability: one-use leases, retries, cancel, stuck-run sweeper | Done |
| Progress above the message box while a run works | Done |
| `@` suggestions for agents and people | Done (suggestions only; no notifications) |
| Dedicated sessions: private or shared, one turn at a time, transcript replay | Done |
| Local bridge (agents on a member's machine, Codex) | Removed. Cloud agents are the only way to run an agent. |
| Render web service, Supabase database, Cloud Run runtime | Deployed |

## Decisions

| Decision | Reason |
|---|---|
| Hub on Render, one instance; database on Supabase in us-west-2 | Free plans, no team member installs anything. One instance keeps the in-process worker and realtime simple. |
| Agent runs on Cloud Run Jobs, one execution per run | No always-on agent process. Each run starts with nothing and exits. |
| Anthropic team API key in Secret Manager | Usage belongs to the team, not a person. |
| GitHub App with read-only contents access, one repository per run | Agents read repositories; they never write. The App key lives in Secret Manager. |
| Read-only tools for the agent (`Read`, `Glob`, `Grep`) | Limits what a run can do, including what injected text can make it do. |
| Session memory by transcript replay, capped at 40 turns or 20,000 characters | Cloud runs start fresh. Replay needs no stored Claude state. |
| Model `claude-haiku-5-5` by default | Cost and speed for team questions. Configurable with `CLAUDE_MODEL`. |
| Bridge removed | Cloud covers the team's needs, and the bridge doubled the code paths for agents, approvals and devices. |

## Next

Ordered by what the team is likely to need first.

1. **Mention notifications.** Members can be suggested with `@`, but they are not notified. Decide the channel (in-app, email) before building it.
2. **Session memory.** If sessions outgrow the replay cap, consider Cloud Run services so the Claude session survives between messages. See [execution](docs/components/execution.md#services-instead-of-jobs-possible-next-step).
3. **Egress controls for runs.** Runs can reach any host. Restrict the job's network before running untrusted repositories.
4. **Usage visibility.** Show the team how many runs and tokens each agent uses, so the shared key's spend is visible.
5. **Scaling the hub.** Only needed if the team grows: a shared rate-limit store, a message bus for realtime events, and a leader election for the worker.

## Deferred

- **Resuming a session's CLI conversation on a warm instance.** Not built; the hub keeps sending the transcript, which is correct on any instance. The idea, for when it is worth the complexity:
  - The runtime keeps, per session, a home folder and a clone, and the CLI's own session ID. Follow-ups run `--resume` with only the new message. First turns use `--session-id`.
  - The hub names the previous turn in the exchange. The runtime resumes only if its last successful turn is that one; otherwise it starts from the transcript. A failed turn discards the workspace.
  - Needs session affinity so follow-ups reach the instance that holds the session. Without it most follow-ups miss and fall back, so the gain is small. Investigate Cloud Run session affinity (cookie) first, and check that the CLI resumes under its Linux home in the runtime image.
  - Idle workspaces removed after 30 minutes, at most five per instance; on Cloud Run these use instance memory.

- Writes by agents to repositories (pull requests). Not planned: agents read only.
- Conversation import and archive, and git-aware features such as worktrees (from the original brief).
- Hardening beyond the controls in [security](docs/security.md).

## Open items

- Rename the `BRIDGE_TOKEN_SECRET` variable in deployments that still use it. The hub reads both names; the old one can be removed once Render has `RUNTIME_TOKEN_SECRET`.
- Decide whether `deploy/.env` (local Compose) should keep its old variable name. It must define `RUNTIME_TOKEN_SECRET` to start.
- Confirm the Cloud Run job's `--max-retries 0` and task timeout after each job update (see [deployment](docs/deployment.md#35-the-job)).
