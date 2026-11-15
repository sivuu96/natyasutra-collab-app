# Runtime

The runtime is the service that performs agent runs. It runs as a Cloud Run service with one warm instance. The hub sends it one invocation at a time; the runtime does the work, reports to the hub, and returns. It has no credentials of its own: it gets a one-use lease from the hub with each request, reads its secrets from Google Secret Manager, and sends everything it does back to the hub as events.

Source: `runtime/`. Image: `runtime/Dockerfile`. Python package: `runtime/collab_runtime/`. Tests: `runtime/tests/`.

## What is in the image

| Part | Detail |
|---|---|
| Base | `node:22-bookworm-slim` |
| Agent | Claude Code CLI, pinned by `ARG CLAUDE_CODE_VERSION` (currently `2.1.322`). Upgrade on purpose, after reading the release notes. |
| Tools | Python 3 in a virtual environment (`httpx`, `pyjwt`, `cryptography`, `google-cloud-secret-manager`), `git`, `ca-certificates` |
| User | `runner` (uid 10001), no login shell. The image holds no secrets. |
| Entry point | `python3 -m collab_runtime`, which starts the HTTP service on `$PORT` |

Build from the repository root, so the image contains `runtime/`:

```powershell
docker build -f runtime/Dockerfile -t <region>-docker.pkg.dev/<project>/agent-runtime/collab-agent-runtime:<tag> .
```

## The HTTP service

| Endpoint | Purpose |
|---|---|
| `POST /runs` | Run one invocation. The body is `{"execution_name": "...", "lease": "..."}`. The request stays open until the run ends, then the response is `200 {"exit_code": n}`. |
| `GET /healthz` | Returns `200`. |

- Cloud Run allows only the hub's dispatcher account to call it (`run.invoker`). The service does not check tokens itself.
- The service runs at concurrency 1, so one request is one run. A second request for an execution that is already running here gets `409 already_running`.
- A request that is missing either field, or is not JSON, gets `400`. Request bodies are never logged, because they contain the lease.

## Configuration

The service gets these from its environment. The hub sends the per-run values in the request; the rest are set on the service.

| Variable | Set by | Purpose |
|---|---|---|
| `HUB_BASE_URL` | service | Where to exchange the lease and send events. Required. |
| `PORT` | Cloud Run | The port to listen on. Default `8080`. |
| `GCP_PROJECT_ID` | service | Project for Secret Manager. Default `natyasutra-agents`. |
| `ANTHROPIC_SECRET_NAME` | service | Default `anthropic-api-key`. |
| `GITHUB_KEY_SECRET_NAME` | service | Default `github-app-key`. |
| `GITHUB_APP_ID`, `GITHUB_INSTALLATION_ID` | service | Identify the GitHub App and its installation. |
| `CLAUDE_MODEL` | service | Default `claude-haiku-5-5`. |
| `CLAUDE_BINARY` | image | Default `claude`. |
| `RUN_TIMEOUT_SECONDS` | service | Default `1500` (25 minutes). |
| `HEARTBEAT_SECONDS` | service | Default `30`. |

## A run, step by step

1. **Receive the request** and claim the execution name. A second request for the same name is refused with `409`.
2. **Exchange the lease** with `POST /api/v1/internal/executions/exchange`, giving the execution name. The hub returns the prompt, the agent's handle, the repository and branch, and a runtime token. For a session turn it also returns the session ID, the new message and the previous turn's ID. If the hub refuses, the run stops with exit code 3 (see [Execution](execution.md#one-use-run-leases)).
3. **Report `started`.** If the harness is not supported, report `failed` and stop.
4. **Read the Anthropic key** from Secret Manager. If that fails, report `failed` (`secret_unavailable`). The run never continues without its key.
5. **Get a GitHub token and clone.** The runtime signs a 10-minute JWT with the GitHub App key, asks GitHub for an installation token limited to the one repository with read-only contents access, and shallow-clones the branch (`--depth 50 --single-branch`). It passes the token with an extra header, not in the clone URL, and clears the credential helper. The clone goes to a temporary directory that is deleted when the run ends.
6. **Run Claude Code** in the clone:
   `claude --allowedTools Read Glob Grep --model <model> -p <prompt> --output-format stream-json --verbose`.
   Only `Read`, `Glob` and `Grep` are allowed. The agent cannot write files, run commands or reach the network through its tools. Its environment contains only the Anthropic key.
7. **Take files out of the reply.** The agent cannot write files. It returns a file as a block in its reply, `<file name="plan.md">…</file>`, which the runtime reads after the run. Each good file is sent as a `file` event, and the block is removed from the reply. At most five files per reply; refused files are noted in the reply text. Every prompt begins with these instructions.
8. **Report progress** while it works: `Preparing the repository…`, `Running the agent`, and `Writing the reply`.
9. **Report the reply and finish**: `message`, then `completed`. On error, `failed` with a code and a short message.
10. **Delete the temporary directory** and return the response.

The runtime sends a heartbeat every 30 seconds while the agent runs. A run that exceeds `RUN_TIMEOUT_SECONDS` is stopped. If the hub cancels the run, the runtime stops Claude Code at its next heartbeat.

Each run logs `timing stage=<name> ms=<n>` lines, measured from the start of the run: `start`, `exchanged`, `secret_read`, `repo_ready`, `agent_started`, `first_output`, `agent_finished` and `completed`. They are in the service's Cloud Logging entries.

### Response codes

The response is `200` once the run has ended, whatever its outcome. The `exit_code` says how it ended:

| Code | Meaning |
|---|---|
| 0 | The run finished and the hub has the result. |
| 1 | The run failed. A `failed` event was sent. |
| 3 | The hub refused the exchange (for example, the lease was used or expired). Nothing was reported, because there is no token to report with. The hub's sweeper will fail the run. |
| 4 | The hub could not be reached while reporting. |

The hub does not read `exit_code`. It is for the logs.

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
| `file` | `name`, `content` | Keeps a text file the agent returned. Sent before the reply. Not shown as a message. |
| `message` | `text` | Saves the reply: a channel message, or a session transcript entry. |
| `completed` | | Marks the run completed. |
| `failed` | `code`, `message` | Marks the run failed, with the code and message shown to the user. |

Rules:

- `sequence` starts at 0 and counts up by one. The hub stores each sequence number once, so a retried event is recognised and answered with `{"duplicate": true}`.
- A run that is already finished refuses further events, so a late event cannot change its result.
- Text is limited to 20,000 characters per event, and failure messages to 500.
- A file's name may use letters, digits, dots, dashes and underscores, starting with a letter or digit, up to 120 characters. Its content is at most 100 KB.
- The runtime removes the Anthropic key from memory once the harness has finished, and it redacts the key and `sk-ant-…` patterns from anything it sends.

## Security

- The runtime trusts nothing it receives except the hub's answer to its lease. Its token expires after 15 minutes and works only for its own invocation.
- The GitHub token covers one repository and only reads it. The App's private key is in Secret Manager, never in the image or the environment.
- The service's account (`agent-runtime`) can read the two secrets and write logs, and nothing else. See [deployment](../deployment.md#34-service-accounts-and-roles).
- stderr from Claude Code is written to a temporary file, and only its last 300 characters are reported, after redaction.

## Limits

- One request is one run. The clone is shallow, and the working copy is deleted at the end.
- A run is cut off if Cloud Run stops its instance in the middle of it. The runtime does not handle that signal. The hub's sweeper fails the run after about 5 minutes without a heartbeat.
- The agent has no memory between runs. Sessions give it earlier turns in the prompt; see [Backend](backend.md#sessions).
- The agent cannot change the repository, open pull requests, or use the network beyond the Anthropic API.
- Only `claude_code` is supported. Any other harness reports `unsupported_harness`.

## Tests

```powershell
cd runtime
..\backend\.venv\Scripts\python.exe -m pytest -q
```

The tests use a fake hub and a fake Claude Code binary. They cover the exchange, event ordering and the duplicate answer, redaction, cancellation stopping the agent, timeouts, missing secrets, the repository checks, the command line passed to Claude Code, and the HTTP service's requests, including the duplicate refusal and the rule that request bodies are never logged.
