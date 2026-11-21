# Security and privacy

This page covers what the hub protects, who can reach what, the controls in place, and what is still missing. It describes the system as it is deployed: a hosted hub, cloud agent runs, and a small team.

## 1. What is worth protecting

| Asset | Why it matters |
|---|---|
| Team conversations, tasks, decisions | The team's work. Some of it is private (sessions). |
| Accounts and sessions | Access to projects. |
| Anthropic API key | Spend, and access to the team's usage. Lives only in Secret Manager. |
| GitHub App private key | Read access to the allowed repositories. Lives only in Secret Manager. |
| Dispatcher service-account key | Can sign requests that run agents. Lives only in Render. |
| Runtime lease and runtime token | Let one run call the hub. Each is for one invocation and expires. |
| Repository contents | Read by agents during a run. Not stored after it. |

## 2. Trust boundaries

```
Browser ──(cookie + CSRF)──▶ Hub ──(identity token, then lease; bearer runtime token)──▶ Runtime (Cloud Run service)
                              │                                                          │
                              │ dispatcher key                                           │ runtime SA
                              ▼                                                          ▼
                        signs the identity token                       Secret Manager, GitHub, Anthropic
```

- **Browser to hub.** Session cookie, CSRF check on writes, project membership on every project route.
- **Hub to Cloud Run.** The hub signs each run request with the dispatcher account's identity token. Cloud Run accepts only callers holding the `run.invoker` role on the runtime service, so the service is private. The dispatcher account cannot read secrets.
- **Files returned by the agent.** The agent cannot write files. A file it returns is text, checked by name and size by both the runtime and the hub, and only people who can see its run can download it. Downloads are sent as plain-text attachments with `nosniff`, so the browser never renders them.
- **Runtime to hub.** The runtime has no account. It holds one lease, exchanges it once, and then uses a 15-minute token for its own invocation.
- **Runtime to secrets and GitHub.** The runtime's account (`agent-runtime`) can read two named secrets. The GitHub token it creates covers one repository and is read-only.

## 3. Threats and controls

| Threat | Control |
|---|---|
| Stolen session cookie | HttpOnly cookies, secure cookies in production, idle timeout (24 hours by default), absolute timeout (14 days). Tokens are stored as hashes. |
| Cross-site request forgery | Double-submit token: the `ns_csrf` cookie must match the `X-CSRF-Token` header on every write. Runtime endpoints use bearer credentials instead. |
| Password guessing | Login is limited to 30 attempts per IP and 10 per email in 15 minutes. Registration is limited per IP. Passwords are hashed with argon2. Sign-up is closed by default. |
| A user reads another team's project | Every project route checks membership and returns 404 for non-members. Agent identity never grants access. |
| A user reads a private session | Private sessions return 404 to anyone but the owner, in the REST API and in live events. |
| A replayed or reused run lease | One use, 10-minute lifetime, stored as a hash, revoked when a newer attempt starts, bound to the execution. |
| A run impersonating another run | Runtime tokens name one invocation and are verified on every event. |
| A run starting for the wrong repository | The hub checks the repository against `GITHUB_ALLOWED_REPOS` and refuses before the lease is used. |
| A run writing to the repository | The GitHub token has read-only contents access. The agent's tools are limited to `Read`, `Glob` and `Grep`. |
| A run escaping its workspace | The clone is in a temporary directory, deleted at the end. The run user is not root, and the image has no credentials. |
| Secrets leaking into logs or chat | Secrets come from Secret Manager at run time. The runtime redacts the key and `sk-ant-…` patterns from output. Logs never include message bodies or credentials. |
| A runaway agent | Runtime timeout of 25 minutes; the sweeper fails a run after 40 minutes at most, and fails silent runs after 5 minutes. A person can stop a run. |
| A replayed or duplicated event | Each event has a sequence number, stored once. A finished run refuses further events. |
| Injection through repository content | The agent's tools are read-only. The agent cannot run commands. A session's earlier turns are labelled as history, not instructions. This reduces, but does not remove, the risk that a document the agent reads changes its answer. |
| Stolen dispatcher key | It can sign requests that run agents, which costs money. Rotate it (see [deployment](deployment.md#5-operating-the-hub)) and keep it only in Render. |
| A forgotten password | Reset links are random, stored only as a hash, valid for 30 minutes and usable once. A new request cancels older links. The request answer is the same whether or not the account exists. A reset ends every session. |
| Security headers | Content-Security-Policy, `X-Frame-Options: DENY`, `nosniff`, a strict referrer policy, and HSTS in production. |

## 4. Authentication

- Registration and login create a server-side session. The browser holds an opaque token in a cookie; the hub stores its hash.
- Sessions end after 24 hours without use or 14 days in total, whichever is first.
- Logging out ends the session. Setting a password from the operator shell ends every session for that account.
- Passwords use argon2 through the `argon2-cffi` library. No custom cryptography.

## 5. Authorisation

| Role | Can |
|---|---|
| Owner | Everything in the project, including role changes and deleting members. |
| Admin | Add, change and remove members, except owners (only an owner can make or remove an owner). Attach any agent, cancel any run, close any session. |
| Member | Post, create tasks and decisions, attach their own agents, start sessions, cancel their own runs. |
| Viewer | Read the project. |

Agent owners can cancel runs on their agents. Session owners are the only people who can send turns in their session.

## 6. Conversation privacy

- **Channels** are visible to every project member.
- **Private sessions** are visible only to their owner. Other members get 404, not 403, so the session's existence is hidden. Their events are not sent to other members, live or on replay.
- **Shared sessions** can be read by every project member, and they show a notice to say so. The session's owner and the project's owners can send turns. Only the owner can change visibility or close the session. The creator, project owners and admins can delete it; a deleted session is hidden everywhere, but its rows are kept. Private sessions are visible and writable only to their owner.
- **Audit records** never contain message bodies.
- **Runs** send their events to the hub only. Output text is live-only: it is streamed to the people viewing the session and is not stored, except as the final reply.

The agent's reply is stored in the channel or the session transcript, like any message. Anyone who can read the channel or the shared session can read the reply.

## 7. Agent safety rules

- An agent runs only when someone asks for it in a project where it is attached. Attaching is the owner's consent for that project.
- Cloud agents read a copy of one repository and write nothing. They cannot run shell commands, open pull requests or reach the network other than the Anthropic API.
- Repositories must be on the allow-list. Adding one is an operator action, and the GitHub App must be installed on it.
- Agents see only the prompt and the earlier turns of their session. They have no access to other sessions, other channels, or the rest of the hub.
- Each run is one container. Nothing carries over between runs except the transcript that the hub sends.

## 8. Audit

The audit log records: account and membership changes, project and channel creation, agent attach, run requests and cancels, session creation, visibility changes, closing, and denied actions. Each entry records who, what, when, the target, and the request id. It does not record message bodies or credentials.

## 9. Operator access

Operators reach the hub through the Render shell and the Supabase dashboard. Both are protected by their own accounts. Operators should use a strong, unique password and two-factor authentication on Render, Supabase, Google Cloud and GitHub.

The Supabase Data API must stay closed (see [deployment](deployment.md#database-supabase)). The hub's own database user is the only one that should read the tables.

## 10. Known limitations

These are known and accepted for a small trusted team. Each must be addressed before the hub is used by people outside the team or at larger scale.

- **One instance.** The rate limiter, realtime fan-out and execution worker are in-process. Scaling out needs a shared rate-limit store, a message bus for realtime events, and a single leader for the worker.
- **Dispatch retries.** A run request that reaches the service after a lost response is refused as a duplicate (`409`), because the service remembers which executions are running. A lease that is used a second time is refused as well. See [execution](components/execution.md#cloud-run-service-provider).
- **Long-lived dispatcher key.** The hub holds a service-account key. Workload identity would remove the key; this needs a platform change.
- **No outbound network controls on runs.** The runtime service can reach any host, so the agent could in principle send data to a site it finds. Restrict the service's network with egress controls before running untrusted repositories.
- **Prompt injection.** The agent reads repository text and replies to people. A document can try to change the agent's behaviour or its answer. Read-only tools limit the damage, but the agent's replies should be treated as suggestions, not verified facts.
- **No review of agent replies.** Replies are not checked before they are shown. Anyone reading a reply should check claims that matter.
- **Free-plan hosting.** Render and Supabase free plans sleep or pause and have no automatic backups. See [deployment](deployment.md#known-limits).
- **Session memory is capped.** Old turns are dropped from long sessions. This is a functional limit, not a security control.
- **Passwords and recovery.** There is no self-service password reset. An operator resets it from the shell.

## 11. Reporting a problem

Report security problems to the project owner directly, not in a channel. Do not paste secrets, tokens or private conversations into an issue or a message. If a secret may have leaked, rotate it first (see [deployment](deployment.md#5-operating-the-hub)), then report.
