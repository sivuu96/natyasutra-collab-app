# Execution

Execution is how a queued run becomes a running container and how it is stopped. It has four parts: the outbox that records work to start, the one-use leases that let a run call back to the hub, the provider that talks to Cloud Run, and the sweeper that fails runs that stop responding.

Source: `backend/app/services/execution.py`, `execution_worker.py`, `leases.py`, `sweeper.py`, `providers/`.

## Outbox

Starting a run is never done inside a request. A mention or a session turn writes an `execution_outbox` row in the same transaction as the invocation. The worker picks rows up from there.

| Column | Meaning |
|---|---|
| `status` | `pending` (waiting), `claimed` (a worker is starting it), `done` (started, or stopped), `dead` (gave up). |
| `attempts`, `next_attempt_at` | Retry bookkeeping. |
| `lease_owner`, `lease_expires_at` | A worker claims a row for 120 seconds. If it dies, the claim expires and another worker takes the row. |
| `provider_execution_id` | The Cloud Run execution name, once it has started. |
| `cancel_sent_at` | When the stop request was sent. A stop is sent once. |

### The worker

`run_outbox_worker` runs inside the hub process, started in the application's lifespan. Every `EXECUTION_WORKER_INTERVAL_SECONDS` (default 5) it does two things in a worker thread:

1. `process_once`: claims due rows and, for each one, mints a lease and submits the run to the provider.
2. `sweep_runs`: fails stuck runs and stops executions that need it (below).

Only one hub instance may run the worker. The hub is deployed as a single instance for this reason.

### Retries

A failed submit goes back to `pending` with a delay that doubles from 10 seconds and is capped at 5 minutes. After five attempts the row becomes `dead`, and the invocation fails with `dispatch_failed`.

A run stays `queued` after a successful submit. Its status moves on only when its runtime exchanges its lease.

## One-use run leases

Each attempt gets a **lease**: a random 32-byte token that only the job receives. The hub stores its SHA-256 hash, not the token.

| Property | Value |
|---|---|
| Lifetime | 10 minutes (`RUN_LEASE_MINUTES`) |
| Use | Once. The exchange marks it used in one conditional update, so two exchanges cannot both succeed. |
| Re-mint | A new attempt revokes the older unused leases. Only the newest attempt can be exchanged. |
| Binding | After the provider returns an execution name, the lease is bound to it. |

The exchange (`POST /api/v1/internal/executions/exchange`) refuses, in this order:

1. The token is unknown: `invalid_lease` (401).
2. The lease was revoked: `lease_revoked` (401).
3. The lease expired: `lease_expired` (401).
4. The lease was already used: `lease_used` (409).
5. The invocation is finished: `run_not_active` (409).
6. The execution has not been bound yet: `execution_not_bound` (409). The runtime may retry.
7. The execution name does not match the bound one: `execution_mismatch` (409). The full resource name and its short last segment are both accepted, because Cloud Run gives the job its short name.
8. The agent's repository is not on the allow-list: `repository_not_allowed` (409). This is checked before the lease is used, so a configuration mistake does not waste the run's only credential.

When every check passes, the lease is marked used and the invocation moves from `queued` to `accepted`. The response includes a runtime token, valid for 15 minutes and bound to the invocation.

## Cloud Run provider

`CloudRunJobsProvider` (`providers/cloud_run.py`) is the only production provider. The other, `FakeExecutionProvider`, is for tests and local runs.

- **Start** (`submit`): calls `jobs:run` on the job with environment overrides `INVOCATION_ID`, `DEDUPE_KEY`, `HUB_BASE_URL` and `RUN_LEASE`. No other credentials are passed. The execution name comes from the operation's metadata.
- **Stop** (`cancel`): calls `executions:cancel`. An answer of 400, 404 or 409 means the execution has already finished or is gone, so it counts as stopped. Retrying would never succeed.
- **Credentials**: the hub authenticates as the dispatcher service account, using the key in `GCP_DISPATCHER_KEY`. The key is loaded once at startup and never logged.

Limitation: `jobs:run` has no idempotency key. A timed-out call can start a second execution. The lease prevents the second one from doing any work: its exchange finds the lease already bound to the first execution, or the lease already used.

## Stuck runs: the sweeper

`sweep_runs` (`services/sweeper.py`) runs on every worker pass. It checks invocations that are not finished:

| Reason | When | Result |
|---|---|---|
| `start_timeout` | Queued or accepted for more than 15 minutes with no activity | Failed: "The agent did not start in time." |
| `heartbeat_lost` | Running with no event or heartbeat for more than 5 minutes | Failed: "The agent stopped reporting progress." |
| `timed_out` | More than 40 minutes after creation, in any state | Failed: "The agent ran longer than the time limit." |

A failed run's outbox row becomes `done`, so it is never started later. For runs that are cancelled or failed and have an execution, the sweeper asks Cloud Run to stop the execution once (`cancel_sent_at`). It looks back two hours. Older runs are not worth another attempt.

The limits fit together: the runtime stops itself after 25 minutes (`RUN_TIMEOUT_SECONDS`), and the sweeper fails a run after 40 minutes at most.

## Cancelling

A person cancels with `POST /projects/{id}/invocations/{invocation}/cancel`. Anyone who asked for the run, its agent's owner, or a project admin may do this. The hub:

1. Marks the invocation `cancelled` at once, so the UI and any later event agree.
2. Lets the sweeper ask Cloud Run to stop the execution on its next pass.
3. The runtime learns of the cancel when its next event or heartbeat is refused with `run_finished`. It then stops Claude Code.

Cancelling a run that has already finished does nothing.

## Services instead of jobs (possible next step)

A Cloud Run job starts a fresh container for every run. Its Claude Code state is gone when the run ends, so a cloud session cannot resume a previous Claude session. The hub works around this by sending the session's earlier turns in each prompt (see [Backend](backend.md#sessions)).

If long sessions need real continuity, Cloud Run **services** could replace jobs. An instance can keep the session files between messages while it stays up. Cloud Run can still stop or replace an instance at any time, so replaying the transcript would remain the fallback. Before choosing this, weigh the cost of keeping an instance warm against per-run jobs, and decide where the session files would be stored. They can contain repository content the agent read, so they need the same retention and deletion rules as the transcript.

## Tests

`tests/test_execution_outbox.py`, `test_run_lease.py`, `test_sweeper.py`, `test_cloud_execution.py`, `test_repo_access.py`, and `test_run_events.py` cover the outbox states and retries, lease checks in order, execution-name matching, the repository allow-list, the sweeper's rules, cancellation, and the provider's requests and error handling.
