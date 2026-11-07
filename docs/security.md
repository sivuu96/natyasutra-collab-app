# Security and Privacy — NatyaSutra Collaboration Hub

Status: Phase 0 threat model and rules. These are release-blocking requirements (brief §6).

## 1. Assets

| Asset | Why it matters |
|---|---|
| Developer provider credentials (Anthropic, OpenAI login state) | Must **never** reach the hub. Owned by the local harness. |
| Private conversation transcripts | Private to owner by default. |
| Project data (messages, tasks, decisions) | Visible only to project members with the right role. |
| Device credentials for bridges | Can invoke agents on a developer's machine. Highest-impact secret in the system. |
| Local repositories and worktrees | Can be read or modified by agents. |

## 2. Trust boundaries

1. Browser ↔ hub: untrusted client. All checks are server-side.
2. Bridge ↔ hub: authenticated device, but the device runs on a developer laptop that can be lost. Its permissions are scoped by the hub and enforced locally.
3. Hub ↔ harness: there is no direct link. The harness is only reachable through the local bridge.
4. Hub ↔ GitHub: read/write via integration token held in deployment secrets only (Phase 5).

## 3. Threats and controls

| Threat | Control |
|---|---|
| Non-member reads a project | `authz` module on every route and WS subscription; isolation tests for every project-scoped endpoint. |
| IDOR (guessing IDs) | UUID IDs plus membership check; cross-project queries forbidden in tests. |
| Stolen session token | Only a hash stored server-side; idle and absolute expiry; logout revokes; rotation on login. |
| Password cracking | argon2-cffi with library defaults; no custom crypto. |
| Brute force on login / pairing | Rate limits per IP and per account on auth, pairing and invocation endpoints. |
| Stolen bridge device credential | Access token lifetime ≤ 15 min; device secret stored hashed; per-device revoke takes effect immediately on next heartbeat or WS message. |
| Pairing code reuse | Single use, ≤ 10 min expiry, bound to the requesting user. |
| Cloud pushes arbitrary shell commands to a laptop | The protocol has **no** shell-execution message. Only typed operations (`start_session`, `send_message`, `cancel`, `close_session`) exist. Anything else is rejected. |
| Path traversal on bridge | Every working directory is resolved with `realpath` and must lie under an explicitly configured repository root. Symlink escapes rejected. |
| Destructive actions (delete files, force-push, merge, deploy, credential change, commands outside approved roots) | Require an approval event. Approval is shown in web UI and CLI. Default is deny on timeout. |
| Secret leakage in transcripts | Local secret scan before upload (API keys, tokens, passwords, private keys). Hits are redacted or the upload is blocked, and the owner sees a report first. |
| Secret leakage in logs | Logs never include message bodies, transcript contents, env vars or credentials. A logging filter redacts `Authorization` headers and token-shaped strings. |
| Replay / duplicate events | Idempotency keys on every event; `(stream_id, sequence)` uniqueness. |
| XSS via message content | Messages rendered as text, or sanitised Markdown with no raw HTML. |
| CSRF | Session cookies `SameSite=Lax`, `HttpOnly`, `Secure` in production; state-changing endpoints require a CSRF token or a custom header check. |
| CORS misconfiguration | Allow-list of frontend origins from env; no wildcard with credentials. |
| Malicious upload | Size and MIME/extension allow-list; files stored outside web root; served with `Content-Disposition: attachment`. |
| Supply chain | Pinned dependency versions in lockfiles; dependency audit in CI (Phase 6). |

## 4. Authentication

- Email + password, argon2-cffi hashing, minimum length 12.
- Server-side sessions: random 256-bit token; only SHA-256 of the token is stored.
- Login and password-change paths rate-limited.
- No provider credentials, OAuth tokens to model providers, or cookies from Claude/Codex are ever requested or stored.

## 5. Authorisation model

Roles per project: `owner`, `admin`, `member`, `viewer`.

| Action | owner | admin | member | viewer |
|---|---|---|---|---|
| Read channels/messages | ✔ | ✔ | ✔ | ✔ |
| Post messages | ✔ | ✔ | ✔ | ✘ |
| Create/update tasks, decisions | ✔ | ✔ | ✔ | ✘ |
| Manage membership | ✔ | ✔ | ✘ | ✘ |
| Register own agent to project | ✔ | ✔ | ✔ | ✘ |
| Invoke an agent | ✔ | ✔ | ✔ | ✘ |
| Approve destructive operations | ✔ | ✔ | owner of the agent only | ✘ |
| Delete project | ✔ | ✘ | ✘ | ✘ |

Non-members receive `404`, not `403`, to avoid revealing project existence.

## 6. Conversation privacy

- Imported conversations default to `visibility = private` with `owner_user_id` set.
- Sharing to a project is an explicit `share` action creating an `audit_events` row.
- Shared transcript access is audited on read.
- Owner can unshare, delete raw and normalised copies, and set retention.
- Disclosure text in the UI states exactly what is synced and who can see it.

## 7. Agent safety rules

1. Bridge uses outbound TLS only. No listening port is opened.
2. Credentials for harnesses are never read by the bridge; the harness process authenticates itself.
3. Each agent is bound to an allow-list of repository roots and worktree directories on that machine.
4. Sensitive operations need an approval record in the database and a matching local confirmation. Timeouts deny.
5. Every invocation, approval, cancellation, tool outcome and failure produces an audit event.
6. Device revocation is immediate: the hub closes the socket and rejects further events from that device.

## 8. Audit

`audit_events` is append-only (no UPDATE/DELETE granted to the application role in production). Each row records actor (user or agent), project, action, target, outcome, timestamp, request ID. Audit rows never contain message bodies.

## 9. Open security items

- S1: Choose the production password-reset channel (email provider not decided). Phase 1 ships without self-service reset; admin reset only.
- S2: Decide on SSO/OIDC before any external team uses the hub.
- S3: Penetration test before Phase 6 sign-off.

## 10. Implemented in Phase 1 and known limitations

Verified by the test suite (66 on SQLite, 68 on PostgreSQL 16) and by the end-to-end smoke test.

| Control | Implementation | Tests |
|---|---|---|
| Password hashing | argon2-cffi default parameters; minimum 12 characters; timing-equalised verify for unknown accounts | `test_auth.py` |
| Session tokens | 256-bit random token in an HttpOnly, SameSite=Lax cookie (`Secure` in production). Only SHA-256 stored. | `test_session_token_stored_only_as_hash` |
| Session lifetime | Idle timeout (default 24 h, extended on use) and absolute timeout (default 14 days, not extendable) | idle and absolute expiry tests |
| Logout | Server-side revocation; replaying the old cookie fails | `test_logout_revokes_server_side_session` |
| Account deactivation | Deactivated users are rejected on existing sessions (no automatic lockout yet; rate limits cover brute force) | `test_deactivated_user_cannot_use_existing_session` |
| CSRF | Double-submit: `ns_csrf` cookie must equal `X-CSRF-Token` on every POST/PATCH/PUT/DELETE under `/api/` | CSRF tests |
| Rate limits | Login: 30 per IP and 10 per email per 15 min. Register: 5 per IP per hour. Returns 429 with `Retry-After`. | `test_login_rate_limited_per_email` |
| Enumeration resistance | Login failures use one generic message; duplicate registration returns a generic 400; non-members get 404 | `test_login_wrong_password_is_generic_401`, isolation suite |
| Project isolation | Single authorisation function; every project route calls it; non-members get 404 on all reads and writes | `test_authz_isolation.py` (enumerates endpoints) |
| Role rules | Owners only grant, change or remove owners; admins cannot grant above their own role; last owner protected | role tests |
| Message integrity | Authors edit only their own messages; deletion is soft and cleared from every response | discussion tests |
| Decision integrity | Proposed by default; only the decision owner or an admin can accept or reject; no automatic acceptance | decision tests |
| Realtime authorisation | Origin allow-list; cookie authentication; membership checked at connect and every 30 s | `test_realtime.py`, isolation suite |
| Audit | Append-only rows for security and mutation events; admin-only read; bodies are never recorded | `test_audit_never_contains_message_bodies` |
| Search scope | Every query carries the project filter; LIKE wildcards escaped | search tests |
| Log hygiene | JSON logs redact `Bearer …` and `password=`/`token=`/`secret=` patterns | not yet covered by a test |

### Bridge and agent controls (Phase 2)

Verified by `test_bridge_flow.py` and `test_bridge_contract.py` (backend), and `test_policy_and_config.py`, `test_runner.py`, `test_claude_adapter.py` (bridge).

| Control | Implementation |
|---|---|
| Hub cannot reach into a machine | Bridge connects outbound only; no listening port. |
| Hub cannot run commands | No frame type carries a command or path. The protocol is validated by an allow-list of types and strict payload schemas. |
| Device secret | Random 256-bit, shown once, stored hashed on the hub. On the bridge: OS credential store, else a file in the state directory. Never in config. |
| Pairing code | One-time, 10-minute expiry, hashed, rate-limited per IP. |
| Bridge authentication | Bearer JWT (HS256, 15 minutes, audience and issuer checked). Cookies are refused on the bridge socket, so a logged-in browser cannot be used as a bridge. |
| Revocation | Immediate: socket closed with 4003, token exchange refused, agents revoked, active work failed. |
| Working directory | Must resolve inside an approved root. Symlinks and junctions are resolved before the containment check. Checked on every run. |
| Tool use | Only `Read`, `Glob`, `Grep` run without approval. Anything else pauses for a human decision in the hub. Approval: owner or project admin only; the requester cannot approve their own request. |
| Cross-device isolation | A device can act only on invocations of agents it owns. |
| Agent identity | Handles are global and first-come. Another user's handle is refused and the bridge is told. |
| Attachment consent | An agent can only be invoked in projects where its owner attached it. Only the owner (or an admin) can attach or detach. |
| Audit | Lifecycle transitions, approvals, attachments, and device events are audited. Streamed deltas and message bodies are not. |
| Logs | The bridge logs IDs and states. Harness stderr is scrubbed for key-shaped text before it leaves the machine. |
| Message-to-agent prompt | Uses the message body as the prompt. Anyone who can post in a project can ask an attached agent to act within its approved folder. That is the intended trust model: attaching an agent is the owner's consent for the project. |

### Dedicated session controls (Phase 3)

Verified by `test_sessions.py` (15 tests, including a live WebSocket check) and by an end-to-end run with real Claude Code and Codex.

| Control | Implementation |
|---|---|
| Private by default | New sessions are private. Other members get 404 on the session, transcript, turns, and invocations. |
| Live output | Each event carries `visibility` and `owner_user_id`. The socket delivers private events only to the owner, on replay and live, including streamed deltas. |
| Feeds | The activity feed hides private session events from non-owners. |
| Approvals | A private session's approval is visible and decidable only by the session owner and the agent's owner. |
| Sharing | Only the owner can share or unshare. Sharing gives read access and live output, not the right to send turns. |
| Closing | The owner or a project admin can close a session. Closed sessions refuse turns. |
| Persisted events | Session events contain identifiers and visibility, never message or delta text. |

Known gap: a session's events were stored with the visibility in effect at the time. Making a session private after it was shared does not retract events already delivered to members.

### Bridge limitations

1. A hub-side revocation closes the socket, but a bridge that has already started a harness process stops it only when its runner receives the cancel or when the process exits. Revocation does not kill a running harness on the machine. The bridge should be stopped locally as well.
2. The approval timeout (30 minutes) and the queue limit (500 frames per connection) are fixed values in code, not configuration.
3. The bridge has no per-tool allow-list beyond read-only; a human approves by tool name, not by exact command. Approving `Bash` approves all shell commands for that request.
4. Prompt and reply content pass through the hub database in plain text, as chat messages do. Only the bridge's logs are kept free of content.

### Known limitations (must be addressed before any external use)

1. **In-process rate limiter and realtime hub.** Both work only with a single API process. Multiple instances need a shared store (for example PostgreSQL or Redis) for limits and a shared bus (PostgreSQL LISTEN/NOTIFY or Redis pub/sub) for events. Until then, deploy one replica.
2. **No password reset, no email verification, no MFA.** Registration is open by default; set `ALLOW_REGISTRATION=false` to close it.
3. **Proxy headers.** Rate limits key on the TCP peer address. Behind a reverse proxy, run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy>` so limits apply to real clients. Not yet configured in deployment files.
4. **No CSP or security headers yet.** Planned for Phase 6 with the production deployment config.
5. **Cookies on `localhost` only tested over HTTP.** `Secure` is enforced only when `ENVIRONMENT=production`; HTTPS deployment is not yet verified.
6. **Mentions do not notify.** The mention list is recorded in the persisted event payload. Notification delivery is not built.
7. **Log redaction is a best-effort regex.** Structured logging avoids logging bodies, but the redaction filter needs its own test before Phase 6 sign-off.
