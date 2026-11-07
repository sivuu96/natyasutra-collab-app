# collab-bridge

A small program that runs on a developer's own computer. It connects that machine's AI agents (Claude Code today, Codex later) to the NatyaSutra collaboration hub, so teammates can mention an agent in a channel and receive its reply there.

The bridge is the only part that touches a model. It runs your locally installed, locally logged-in harness. The hub coordinates; it never receives your provider credentials and never runs model requests for you.

## What it does and does not do

- Connects **outbound** to the hub over TLS. It opens no listening port.
- Runs a harness only for an agent you configured, in a working directory you approved.
- Reads only the read-only tools (`Read`, `Glob`, `Grep`) without asking. Any other tool a harness requests pauses the run until a person approves it in the hub.
- Never runs commands sent by the hub. The hub sends a prompt; the bridge decides where and how to run it.
- Does **not** read, copy, or transmit Claude or Codex credentials, cookies, or subscription data.

## Requirements

- Python 3.11 or newer (Windows, macOS, or Linux).
- The Claude Code CLI, installed and logged in on this machine (`claude`). Point to it with `--harness-bin` if it is not on PATH.
- Network access to the hub.

## Setup

```powershell
cd bridge
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt   # the bridge's own dependencies
```

1. In the hub, open **Devices** and create a one-time pairing code.
2. Pair this machine:

   ```powershell
   .venv\Scripts\python.exe -m collab_bridge pair --hub https://your-hub --code ABCD-EFGH --name "Work laptop"
   ```

3. Offer an agent. Give it a handle (globally unique, 3–40 lowercase letters, digits, or hyphens) and the folders it may work in:

   ```powershell
   .venv\Scripts\python.exe -m collab_bridge agent add --handle claude-architecture `
       --display "Claude (architecture)" --harness claude_code `
       --repo C:\work\natyasutra --harness-bin "C:\path\to\claude.exe"
   ```

4. Start serving:

   ```powershell
   .venv\Scripts\python.exe -m collab_bridge run
   ```

5. In the hub, open the project, go to **Agents**, and offer your agent to it. Then mention `@claude-architecture` in a channel.

Other commands: `status`, `agent list`, `agent remove --handle …`, `forget` (removes the local secret and config; also revoke the device in the hub).

State lives in `~/.collab-bridge` (override with `--state-dir` or `COLLAB_BRIDGE_HOME`):

- `config.json` — hub address, device id, agents. **Contains no secrets.**
- The device secret — stored in the operating-system credential store (Windows Credential Manager, macOS Keychain, or Secret Service) via `keyring`. If no store is available it falls back to a file in the state directory, which should be considered sensitive.
- `bridge.sqlite3` — the outbox of unacknowledged events.

## Security model

| Concern | Control |
|---|---|
| Hub reaching into your machine | Impossible by design: outbound connection only. |
| Hub running arbitrary commands | No such message exists in the protocol. Work is a prompt for a configured agent. |
| Agent touching folders it should not | Working directory must be inside an approved root, checked after resolving symlinks and junctions, on every run. |
| Agent using powerful tools | Anything beyond read-only tools needs approval in the hub. Approval is per request. |
| Stolen device secret | Revoke the device in the hub. Its token expires within 15 minutes and cannot be renewed. |
| Logs leaking secrets | The bridge logs event IDs and lifecycle states. It does not log prompts or replies. Harness error output is scrubbed for key-shaped text. |

## Harnesses

- **Claude Code** (`--harness claude_code`): working. See `docs/claude-code.md`.
- **Codex** (`--harness codex`): working through `codex app-server`. Point `--harness-bin` at the native `codex.exe` in the npm package (`node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe` on Windows). The `.cmd` shim also works but needs Node on PATH. See `docs/codex.md`.

Dedicated sessions (Phase 3) reuse the harness's own session, so an agent keeps its context between messages. The transcript is stored in the hub. On the bridge, output is kept only in the local outbox until the hub acknowledges it, and then deleted; the harness keeps its own session files.

## Known limits

- Each agent runs one request at a time. Further requests for it wait in order.
- Approvals wait up to 30 minutes, then are treated as denied.
- Work in progress when the bridge disconnects is reported as failed. It is not resumed.
- The Claude Code stream format was verified against Claude Code 2.1.x. A future CLI version could change it. The adapter ignores unknown event types rather than failing, but a changed format would stop replies from appearing. Re-verify after upgrading the CLI.
- The Codex protocol was verified against codex-cli 0.167.0 and its generated schema. Re-verify after upgrading.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check collab_bridge tests
```

The backend's contract test (`backend/tests/test_bridge_contract.py`) imports this package to check that the wire format matches the hub.
