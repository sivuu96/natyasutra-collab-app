# Claude Code integration

Status: **working** for shared-thread requests (mention an agent in a channel). Dedicated sessions are Phase 3.

## How it runs

The bridge starts your installed `claude` CLI in print mode, in a folder you approved:

```
claude --allowedTools Glob Grep Read [--resume <session>] -p "<prompt>" \
       --output-format stream-json --verbose --include-partial-messages
```

- The CLI authenticates itself with your own login. The bridge does not read, copy, or pass credentials, and it does not route requests through a proxy.
- The working directory is the agent's default repository, checked against its approved roots before every run.
- Only `Read`, `Glob`, and `Grep` are allowed at first. If the model asks for anything else, the CLI reports a permission denial, and the bridge asks the hub for a human decision (see `docs/agent-protocol.md` §6).

## Verified against

| Item | Verified value |
|---|---|
| Claude Code version | 2.1.319 (installed from the official npm package `@anthropic-ai/claude-code`) |
| Binary on Windows | `bin\claude.exe` inside the npm package folder. Use this path. The `claude.cmd` shim is not needed and is not used. |
| Authentication | Existing local login on the test machine. Login was not performed by the bridge. |
| Model used in tests | claude-haiku-5-5 |

## Output shapes the adapter relies on

Captured from live runs:

- `{"type":"system","subtype":"init","session_id":...}` — session identifier, used for resume.
- `{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":...}}}` — live text, sent to the hub as output deltas.
- `{"type":"assistant","message":{"content":[{"type":"text","text":...}]}}` — a complete text block, sent as a message.
- `{"type":"system","subtype":"permission_denied",...}` — informational. Not relied on.
- `{"type":"result","subtype":"success","is_error":false,"session_id":...,"permission_denials":[{"tool_name":...,"tool_use_id":...,"tool_input":{...}}]}` — end of turn. `permission_denials` is the authoritative list of tools that were refused.

Unknown event types are ignored, not treated as errors.

## Setup

```powershell
# Install (once)
npm install -g @anthropic-ai/claude-code
# Log in (once, interactively, in your own terminal)
claude
```

Then point the bridge at the binary, since the npm shim is not on PATH for every shell:

```powershell
collab-bridge agent add --handle claude-architecture --display "Claude (architecture)" `
    --harness claude_code --repo C:\work\natyasutra `
    --harness-bin "C:\Users\<you>\AppData\Roaming\npm\node_modules\@anthropic-ai\claude-code\bin\claude.exe"
```

`collab-bridge status` shows whether the harness was found and which version it reports. A successful `--version` check does not prove you are logged in. Login is only checked when a run starts, and an error from the CLI surfaces as a failed invocation.

## Known limits

- A denied tool does not make the run crash. The model explains the refusal in its reply. After approval, the same session resumes with the approved tool added. In testing this was correct, but the resumed run depends on the session still existing in the CLI's own store.
- The stream format is not versioned by Anthropic in any way the bridge can check. Re-run the test below after any CLI upgrade.
- Cost: each request is a real model call billed to your Claude subscription or account. The probes during development each cost a fraction of a cent (about $0.003 for a short reply). The end-to-end run made a handful of short requests; its total cost was not recorded.

## Re-verifying after an upgrade

```powershell
cd bridge
.venv\Scripts\python.exe -m pytest tests/test_claude_adapter.py -q
```

Then run one short live request from a channel (for example "Reply with exactly BRIDGE OK") and confirm the reply appears attributed to the agent.
