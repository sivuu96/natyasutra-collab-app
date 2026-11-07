# Agent Protocol — Bridge ↔ Hub

Status: **implemented in Phase 2** (protocol version 1). The contract is mirrored on both sides:
`backend/app/services/bridge_protocol.py` (hub) and `bridge/collab_bridge/protocol.py` (bridge).
`backend/tests/test_bridge_contract.py` fails the build if the two drift.

This supersedes the Phase 0 design. Where the two differ, this document is current.

## 1. Connection and authentication

| Step | Endpoint | Auth | Notes |
|---|---|---|---|
| Pair | `POST /api/v1/bridge/pair` `{code, device_name}` | one-time code | Returns `device_id` and `device_secret` once. Code is single-use, expires after 10 min. Rate limit 10/hour per IP. |
| Token | `POST /api/v1/bridge/token` `{device_id, device_secret}` | device secret | Returns a 15-minute JWT (HS256). Checks revocation on every call. Rate limit 30/min per device. |
| Connect | `WSS /api/v1/bridge/ws` | `Authorization: Bearer <token>` | Outbound from the bridge only. Cookies are never accepted here. |

- The hub never stores the device secret or the pairing code, only SHA-256 hashes.
- Revoking a device (`DELETE /api/v1/devices/{id}`) closes its socket with close code **4003**, marks its agents revoked, and fails its active invocations. Its next token exchange returns 401.
- A bridge that receives 401 from token exchange, or close code 4003 or 1008, stops retrying and reports it.
- The bridge reconnects with exponential backoff and full jitter (0.2 s to 60 s).
- Heartbeat: the bridge sends `bridge.heartbeat` every 25 s; the hub closes after 60 s of silence. Heartbeats are not persisted.

## 2. Frame format

Both directions use the same envelope:

```json
{ "type": "invocation.accepted", "event_id": "uuid", "payload": { } }
```

- `event_id` is a UUID chosen by the sender. Frames the bridge persists (see §5) are de-duplicated by `event_id`.
- Unknown types and payloads that fail validation are refused with `bridge.error` (`frame_invalid`). Extra payload fields are refused (`extra="forbid"`).
- A frame that is well-formed but deterministically wrong (for example an invocation another device owns) gets `bridge.error` with `frame_rejected`, and is acknowledged so the bridge does not retry it forever.
- Messages over 1 MB are refused.

### 2.1 Bridge → hub

| Type | Payload | Persisted | Meaning |
|---|---|---|---|
| `bridge.hello` | `{bridge_version, agents: [{handle, display_name, harness, description?, capabilities[]}]}` | no | Sent first on every connection. Registers or refreshes agents owned by this device. |
| `bridge.heartbeat` | — | no | Liveness. |
| `bridge.ack` *(hub→bridge)* | `{event_id, duplicate?}` | — | The hub has durably stored this frame. The bridge drops it from its outbox. |
| `invocation.accepted` | `{invocation_id}` | yes | The bridge validated the request and started work. |
| `invocation.rejected` | `{invocation_id, reason_code, detail?}` | yes | Refused locally (unknown agent, outside approved roots, harness unavailable, prompt too long). |
| `invocation.output_delta` | `{invocation_id, text}` | **no** | Live text fragment. Broadcast only; never stored. |
| `invocation.output_message` | `{invocation_id, text}` | yes | A complete reply. Stored as a message authored by the agent. |
| `invocation.completed` | `{invocation_id, harness_session_ref?}` | yes | Finished successfully. |
| `invocation.failed` | `{invocation_id, code, message}` | yes | Finished with an error. |
| `invocation.cancelled` | `{invocation_id}` | yes | Stopped because cancellation was requested. |
| `approval.required` | `{invocation_id, operation, tools[], detail?}` | yes | The harness needs tools that policy does not allow. Work pauses until a human decides. |

### 2.2 Hub → bridge

| Type | Payload | Meaning |
|---|---|---|
| `bridge.welcome` | `{protocol_version, device_id}` | First frame on a new connection. |
| `bridge.ack` | `{event_id, duplicate?}` | Acknowledges a persisted bridge frame. |
| `bridge.error` | `{code, detail?, event_id?}` | `frame_invalid`, `frame_rejected`, `malformed_json`, or `agent_registration_refused` (a handle is invalid or already belongs to another user). |
| `invocation.created` | `{invocation_id, project_id, agent_handle, prompt, requested_by, harness_session_ref?}` | Work for one of this device's agents. **No path, command, or environment is sent.** |
| `invocation.cancel` | `{invocation_id}` | Stop this invocation. |
| `approval.resolved` | `{invocation_id, approval_id, decision}` | `approved` or `denied`. Re-sent on reconnect if the bridge was offline when the decision was made. |

## 3. Invocation lifecycle

```
mention ─► queued ─► delivered ─► accepted ─► running ─► completed
             │           │            │  ▲        │
             │           │            │  └── awaiting_approval
             ▼           ▼            ▼             ▼
        rejected    (bridge gone →) failed    cancelled / failed
```

- **queued**: stored, not yet with a live bridge. Only created if the sender chose `queue_if_offline`.
- **rejected** with `agent_offline`: the agent's bridge was not connected and the sender did not ask for queueing. Nothing ran and nothing is queued. The UI says so.
- **delivered**: the frame was handed to the bridge's socket. Not yet proof of receipt.
- **accepted**: the bridge validated the request and started. Only this state means work began.
- **running**: output has started, or work resumed after an approval.
- **awaiting_approval**: waiting for a human decision.
- **completed / failed / cancelled / rejected**: terminal. A terminal invocation cannot change again.
- If the bridge disconnects while work is in progress, the invocation becomes **failed** with `bridge_disconnected`, and any pending approval for it is **denied**. The hub never shows work as still running when its bridge has gone.

## 4. Routing (mentions)

1. A message in a channel with `@handle` is parsed. Handles are matched against agents **attached to this project**. Unattached handles are plain text.
2. For each attached agent, an invocation row is created in the same transaction as the message.
3. After commit: if the owner's bridge is connected, the frame is sent and the invocation becomes `delivered`. Otherwise it is `queued` (if requested) or `rejected` with `agent_offline`.
4. Only the agent's owner bridge can report on that invocation. Another device, even one owned by the same user, cannot change it (`frame_rejected`).
5. Editing a message does not re-trigger it. Only new messages create invocations.

## 5. Persistence and ordering

- Every inbound frame of a persisted type is written to `agent_events` (keyed by `event_id`) **before** it is applied. A duplicate `event_id` is acknowledged with `duplicate: true` and not re-applied.
- Each state change writes a `project_events` row with the next per-project sequence number, **then** broadcasts. Deltas are the only exception: they are broadcast without a sequence number and are never stored.
- Audit records record lifecycle transitions and never record deltas or message bodies.
- On the bridge: frames of persisted types go into a local SQLite outbox first, and are deleted only when the hub acknowledges their `event_id`. After a reconnect, the outbox is replayed in order.

## 6. Approvals

- Approval requests are created by the bridge when a harness asks for tools outside the read-only set (`Read`, `Glob`, `Grep`).
- The decision can be made by the **agent owner** or a **project admin**. The requester cannot approve their own request.
- Decisions are conditional: only a pending approval on an `awaiting_approval` invocation can be decided. Otherwise the API returns 409.
- An approved decision resumes the same harness session with only the approved tool names added. A denial or a 30-minute timeout ends the run, and the reply says the tools were not approved.

## 7. Cancellation

- Cancelling queued work is immediate.
- Cancelling work a bridge holds sends `invocation.cancel`. The bridge terminates the harness process and replies `invocation.cancelled`.
- Who may cancel: the requester, the agent's owner, or a project admin.

## 8. Harness adapter contract (bridge side)

```python
class AgentAdapter(Protocol):
    harness: str
    def discover(self, harness_bin: str | None = None) -> HarnessStatus: ...
    def run(self, request: RunRequest) -> AsyncIterator[HarnessEvent]: ...
    async def cancel(self, invocation_id: str) -> None: ...
```

`HarnessEvent.kind` is one of `text_delta`, `text_final`, `session`, `permission_denied`, `approval_request`, `completed`, `failed`. `permission_denied` ends a run and the runner reruns it after approval (Claude Code). `approval_request` pauses a live run; the runner answers through `answer_approval` after the human decides (Codex). Adapters never see hub credentials and never receive paths or commands from the hub. The working directory comes from local config and is checked again on every run (see `bridge/collab_bridge/policy.py`).

Adapter status:

| Harness | Status | Evidence |
|---|---|---|
| Claude Code (`claude_code`) | **Working.** Runs the locally installed CLI in print mode. | Output shapes captured from Claude Code 2.1.319, including `permission_denials`. Unit tests use those shapes. An end-to-end run through the hub is recorded in `docs/development.md`. See `docs/claude-code.md`. |
| Codex (`codex`) | **Working.** Drives `codex app-server` over stdio. Live approvals. | Method names and shapes from the CLI's generated schema (codex-cli 0.167.0); live turn and write-approval run recorded. See `docs/codex.md`. |
| Fake (`fake`) | Test harness only, enabled with `collab-bridge run --fake-harness`. | Deterministic. Used by the test suite. |

## 8.1 Dedicated session turns (Phase 3)

A session turn is an ordinary invocation with `session_id` set and no channel. Differences from a mention:

- `invocation.created` carries `harness_session_ref` (the session's stored harness id). The bridge resumes that thread or session. A session's first turn has none.
- `invocation.output_message` is written to the session transcript (`agent_session_messages`), not a channel. The event carries only the message id.
- `invocation.completed` carries the harness's session id, which the hub stores on the session for the next turn.
- Output deltas and session events carry the session's `visibility` and `owner_user_id`. Private output is delivered only to the owner.
- A session accepts one turn at a time. A second turn while one is in progress returns 409.

Two harness behaviours the adapters handle:

- Claude Code: a permission denial ends the run and is resumed after approval (§6).
- Codex: an approval request is answered live on the same connection. The approval key is the server's own request id, kept as received (`docs/codex.md`).

## 9. Error codes

| Code | Where | Meaning |
|---|---|---|
| `auth_failed` / HTTP 401 | token exchange | Device secret wrong, or device revoked. Not retried. |
| `4003` | WebSocket close | Device revoked. Not retried. |
| `1008` | WebSocket close | Bad or missing token. Not retried. |
| `frame_invalid` | `bridge.error` | Frame broke the contract. Dropped. |
| `frame_rejected` | `bridge.error` | Deterministically refused (wrong device, already finished). Acknowledged. |
| `agent_offline` | invocation | Agent not connected and not queued. |
| `unknown_agent`, `policy`, `harness_unavailable`, `prompt_too_long` | `invocation.rejected` | Refused on the bridge. |
| `bridge_disconnected` | invocation | Bridge dropped during work. |
| `harness_exit`, `harness_error_*`, `harness_unavailable` | `invocation.failed` | Harness failed. |
