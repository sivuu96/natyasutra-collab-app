# Codex integration

Status: **working** for mentions and dedicated sessions, with approvals. Verified against codex-cli **0.167.0**.

## How it runs

For each request, the bridge starts your installed Codex CLI as an app server and talks to it over stdio:

```
codex app-server        # JSON-RPC 2.0, newline-delimited, over stdin/stdout
```

One app server runs per request and is closed when the turn ends. Conversation continuity comes from the thread Codex stores on disk (`ephemeral: false`), which the bridge resumes by id (`thread/resume`). Sessions in the hub store that id, so a later turn picks up the same thread.

- Authentication is your own local Codex login. The bridge never reads Codex credential files or proxies requests.
- Threads start `read-only` with `approvalPolicy: "on-request"`. Writes and commands are either refused by the sandbox or sent to the hub as approval requests (see below).
- The working directory is the agent's default repository, checked against its approved roots before every run.

## Protocol, as verified

Method names and shapes were taken from the schema the installed CLI generates (`codex app-server generate-json-schema`), then checked against live runs. Used by the adapter:

| Direction | Message | Use |
|---|---|---|
| client → server | `initialize` `{clientInfo}` then notification `initialized` | Handshake. Anything else before it is refused. |
| client → server | `thread/start` `{cwd, sandbox, approvalPolicy, ephemeral}` → `thread.id` | New conversation. |
| client → server | `thread/resume` `{threadId, cwd, sandbox, approvalPolicy}` | Continue a stored conversation. |
| client → server | `turn/start` `{threadId, input:[{type:"text", text}]}` → `turn.id` | One user message. |
| client → server | `turn/interrupt` `{threadId, turnId}` | Cancel a running turn. |
| server → client | `item/agentMessage/delta` `{delta, turnId, itemId}` | Live text. |
| server → client | `item/completed` `{item}` (`item.type == "agentMessage"`, `item.text`) | Final text of a message. |
| server → client | `turn/completed` `{turn: {id, status, error?}}` | Status: `completed`, `interrupted`, or `failed`. |
| server → client (request) | `item/commandExecution/requestApproval` | A command needs approval. The client must answer. |
| server → client (request) | `item/fileChange/requestApproval` | A file change needs approval. The client must answer. |
| client → server (response) | `{id, result: {decision}}` where decision is `accept`, `acceptForSession`, `decline`, or `cancel` | The answer. The adapter sends `accept` or `decline` only, so a human decision is never upgraded. |

Other server requests (user-input prompts, tool calls, token refresh, and so on) are refused with a JSON-RPC error. They are never guessed at.

The method list that did not match the community guides: the names above are from the schema, not from blog posts. `initialize` requires only `clientInfo`; `thread/start` returns the thread under `result.thread.id`.

## Approvals and the sandbox

Two behaviours were observed on the live CLI:

1. A write attempted in the read-only sandbox was **refused by the sandbox** with no approval request. The model reported the failure. This is safe, and it is the expected outcome when a human has not approved the write.
2. In another run, the model sent `item/commandExecution/requestApproval` for the same kind of write. Answering `accept` let the command run and the file was created.

So the hub cannot assume every restricted action produces a prompt. Both outcomes are handled: a prompt becomes an `approval.required` for a human, and a silent refusal just shows as the agent's reply.

## Live verification

Recorded during Phase 3 with the real adapter (`scratchpad/codex_live.py`, not in the repo):

- Plain turn: streamed text, final text `CODEX OK`, status completed.
- Write request: approval `command` raised, answered `accept`, file created, final reply `DONE`.

Tests that need no model run against a stand-in app server with the same JSON-RPC shapes (`bridge/tests/fake_codex_app_server.py`): handshake, resume, streamed turns, file-change approval round-trip (accept and decline), cancellation, and the missing-binary path.

## Known limits

- Each request starts and stops its own app server. This costs a second or two per turn.
- The approval key is the server's own JSON-RPC id, kept as received. If Codex changes its id type, approvals will fail to answer. Re-run `test_codex_adapter.py` after upgrading the CLI.
- `item/commandExecution/outputDelta` (command output) is not forwarded to the hub. Only the agent's text is shared.
- The stdio transport is the stable interface. The WebSocket transport is documented as experimental and is not used.
- Re-verify after any Codex upgrade: the schema can change between versions.

## Setup

```powershell
npm install -g @openai/codex
codex          # log in once, in your own terminal
```

Then add the agent (on Windows, pointing at the npm shim is fine; the adapter routes `.cmd` files through `cmd /c`):

```powershell
collab-bridge agent add --handle codex-backend --display "Codex (backend)" --harness codex `
    --repo C:\work\natyasutra --harness-bin "C:\Users\<you>\AppData\Roaming\npm\codex.cmd"
```
