# Runtime

The runtime is the container that performs one agent run. It starts, does one piece of work, reports to the hub, and exits. It has no credentials of its own: it gets a one-use lease from the hub, reads its secrets from Google Secret Manager, and sends everything it does back to the hub as events.

Source: `runtime/`. Image: `runtime/Dockerfile`. Python package: `runtime/collab_runtime/`. Tests: `runtime/tests/`.

## What is in the image

| Part | Detail |
|---|---|
| Base | `node:22-bookworm-slim` |
| Agent | Claude Code CLI, pinned by `ARG CLAUDE_CODE_VERSION` (currently `2.1.322`). Upgrade on purpose, after reading the release notes. |
| Tools | Python 3 in a virtual environment (`httpx`, `pyjwt`, `cryptography`, `google-cloud-secret-manager`), `git`, `ca-certificates` |
| User | `runner` (uid 10001), no login shell. The image holds no secrets. |
| Entry point | `python3 -m collab_runtime` |

Build from the repository root, so the image contains `runtime/`:

```powershell
docker build -f runtime/Dockerfile -t <region>-docker.pkg.dev/<project>/agent-runtime/collab-agent-runtime:<tag> .
```

## Configuration

The job gets these from its environment. The hub sends the per-run values; the rest are set on the job.

| Variable | Set by | Purpose |
|---|---|---|
| `INVOCATION_ID` | hub, per run | The run this execution is for. |
| `DEDUPE_KEY` | hub, per run | Identifies the attempt. |
| `HUB_BASE_URL` | hub, per run | Where to exchange the lease and send events. |
| `RUN_LEASE` | hub, per run | The one-use lease. Removed from the environment as soon as it is read. |
| `CLOUD_RUN_EXECUTION` | Cloud Run | The execution's name. The hub checks it against the lease. |
| `GCP_PROJECT_ID` | job | Project for Secret Manager. Default `natyasutra-agents`. |
| `ANTHROPIC_SECRET_NAME` | job | Default `anthropic-api-key`. |
| `GITHUB_KEY_SECRET_NAME` | job | Default `github-app-key`. |
| `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID` | job | Identify the GitHub App and its installation. |
| `CLAUDE_MODEL` | job | Default `claude-haiku-5-5`. |
| `CLAUDE_BINARY` | image | Default `claude`. |
| `RUN_TIMEOUT_SECONDS` | job | Default `1500` (25 minutes). |
| `HEARTBEAT_SECONDS` | job | Default `30`. |

## A run, step by step

1. **Read the lease** from `RUN_LEASE` and remove it from the environment. If it is missing, exit with code 2.
2. **Exchange the lease** with `POST /api/v1/internal/executions/exchange`, giving the execution name. The hub returns the prompt, the agent's handle, the repository and branch, and a runtime token. If the hub refuses, exit with code 3 (see [Execution](execution.md#one-use-run-leases)).
3. **Report `started`.** If the harness is not supported, report `failed` and exit.
4. **Read the Anthropic key** from Secret Manager. If that fails, report `failed` (`secret_unavailable`). The run never continues without its key.
5. **Get a GitHub token and clone.** The runtime signs a 10-minute JWT with the GitHub App key, asks GitHub for an installation token limited to the one repository with read-only contents access, and shallow-clones the branch (`--depth 50 --single-branch`). It passes the token with an extra header, not in the clone URL, and clears the credential helper. The clone goes to a temporary directory that is deleted when the run ends.
6. **Run Claude Code** in the clone:
   `claude --allowedTools Read Glob Grep --model <model> -p <prompt> --output-format stream-json --verbose`.
   Only `Read`, `Glob` and `Grep` are allowed. The agent cannot write files, run commands or reach the network through its tools. Its environment contains only the Anthropic key.
7. **Report progress** while it works: `Preparing the repository…`, `Running the agent`, and `Writing the reply`.
8. **Report the reply and finish**: `message`, then `completed`. On error, `failed` with a code and a short message.

The runtime sends a heartbeat every 30 seconds while the agent runs. A run that exceeds `RUN_TIMEOUT_SECONDS` is stopped. If the hub cancels the run, the runtime stops Claude Code at once. A SIGTERM from Cloud Run stops it too.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | The run finished and the hub has the result. |
| 1 | The run failed. A `failed` event was sent. |
| 2 | Configuration error: the lease is missing. Nothing was reported. |
| 3 | The hub refused the exchange (for example, the lease was used or expired). Nothing was reported, because there is no token to report with. The hub's run timeout will fail the run. |
| 4 | The hub could not be reached while reporting. |

## Events

Each event goes to `POST /api/v1/internal/executions/events` with the runtime token as a bearer credential. The body is:

```json
{ "sequence": 3, "type": "output", "text": "..." }
```

| Type | Fields | Effect in the hub |
|---|---|---|
| `started` | | Marks the run started. |
| `progress` | `text` | Shows a stage line above the message box. Not stored. |
| `heartbeat` | | Keeps the run alive for the sweeper. |
| `output` | `text` | Streams text to the viewers. Not stored. |
| `message` | `text` | Saves the reply: a channel message, or a session transcript entry. |
| `completed` | | Marks the run completed. |
| `failed` | `code`, `message` | Marks the run failed, with the code and message shown to the user. |

Rules:

- `sequence` starts at 0 and counts up by one. The hub stores each sequence number once, so a retried event is recognised and answered with `{"duplicate": true}`.
- A run that is already finished refuses further events, so a late event cannot change its result.
- Text is limited to 20,000 characters per event, and failure messages to 500.
- The runtime removes the Anthropic key from memory once the harness has finished, and it redacts the key and `sk-ant-…` patterns from anything it sends.

## Security

- The runtime trusts nothing it receives except the hub's answer to its lease. Its token expires after 15 minutes and works only for its own invocation.
- The GitHub token covers one repository and only reads it. The App's private key is in Secret Manager, never in the image or the environment.
- The service account of the job can read the two secrets and write logs, and nothing else. See [deployment](../deployment.md#3-google-cloud-setup-agent-runs).
- stderr from Claude Code is written to a temporary file, and only its last 300 characters are reported, after redaction.

## Limits

- One run is one container. The clone is shallow, and the working copy is deleted at the end.
- The agent has no memory between runs. Sessions give it earlier turns in the prompt; see [Backend](backend.md#sessions).
- The agent cannot change the repository, open pull requests, or use the network beyond the Anthropic API.
- Only `claude_code` is supported. Any other harness reports `unsupported_harness`.

## Tests

```powershell
cd runtime
..\backend\.venv\Scripts\python.exe -m pytest -q
```

The tests use a fake hub and a fake Claude Code binary. They cover the exchange, event ordering and the duplicate answer, redaction, cancellation stopping the agent, timeouts, missing secrets, the repository checks, and the command line passed to Claude Code.
