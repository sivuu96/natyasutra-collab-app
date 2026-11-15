# Execution

Execution is how a queued run reaches the agent runtime. It has four parts: the outbox that records work to dispatch, the one-use leases that let a run call back to the hub, the provider that sends runs to the Cloud Run service, and the sweeper that fails runs that stop responding.

Source: `backend/app/services/execution.py`, `execution_worker.py`, `leases.py`, `sweeper.py`, `providers/`.

## Outbox

Dispatching a run is never done inside a request. A mention or a session turn writes an `execution_outbox` row in the same transaction as the invocation. The worker picks rows up from there.

| Column | Meaning |
|---|---|
| `status` | `pending` (waiting), `claimed` (a worker is dispatching it), `done` (dispatched), `dead` (gave up). |
| `attempts`, `next_attempt_at` | Retry bookkeeping. |
| `lease_owner`, `lease_expires_at` | A worker claims a row for 120 seconds. If it dies, the claim expires and another worker takes the row. |
| `completed_at`, `last_error` | When the row finished, and the last provider error, shortened. |

### The worker

`run_outbox_worker` runs inside the hub process, started in the application's lifespan. Every `EXECUTION_WORKER_INTERVAL_SECONDS` (default 5) it does two things in a worker thread:

1. `process_once`: claims due rows and, for each one, mints a lease and dispatches the run to the provider.
2. `sweep_runs`: fails stuck runs (below).

Only one hub instance may run the worker. The hub is deployed as a single instance for this reason.

### Retries

A failed dispatch goes back to `pending` with a delay that doubles from 10 seconds and is capped at 5 minutes. After five attempts the row becomes `dead`, and the invocation fails with `dispatch_failed`.

A run stays `queued` after a successful dispatch. Its status moves on only when its runtime exchanges its lease.

## One-use run leases

Each attempt gets a **lease**: a random 32-byte token that only the runtime receives, in the dispatch request. The hub stores its SHA-256 hash, not the token.

| Property | Value |
|---|---|
| Lifetime | 10 minutes (`RUN_LEASE_MINUTES`) |
| Use | Once. The exchange marks it used in one conditional update, so two exchanges cannot both succeed. |
| Re-mint | A new attempt revokes the older unused leases. Only the newest attempt can be exchanged. |
| Binding | When the dispatch is accepted, the lease is bound to the execution name the hub chose for it. |

The exchange (`POST /api/v1/internal/executions/exchange`) refuses, in this order:

1. The token is unknown: `invalid_lease` (401).
2. The lease was revoked: `lease_revoked` (401).
3. The lease expired: `lease_expired` (401).
4. The lease was already used: `lease_used` (409).
5. The invocation is finished: `run_not_active` (409).
6. The execution has not been bound yet: `execution_not_bound` (409). The runtime may retry.
7. The execution name does not match the bound one: `execution_mismatch` (409). Only the exact name is accepted.
8. The agent's repository is not on the allow-list: `repository_not_allowed` (409). This is checked before the lease is used, so a configuration mistake does not waste the run's only credential.

When every check passes, the lease is marked used and the invocation moves from `queued` to `accepted`. The response includes a runtime token, valid for 15 minutes and bound to the invocation, along with the prompt, the repository and branch, and for session turns the session ID, the new message and the previous turn's ID.

## Cloud Run service provider

`CloudRunServiceProvider` (`providers/cloud_run_service.py`) is the production provider. The other, `FakeExecutionProvider`, is for tests and local runs.

- **Dispatch** (`submit`): signs an identity token for the service URL with the dispatcher key, chooses an execution name (`service/<invocation>/<random>`), and returns it at once. The request itself is sent from a background thread, because the run lasts up to 25 minutes and the worker must not wait for it. The request body carries the execution name and the lease. Nothing else.
- **Retries**: a connection error, a 5xx or a 429 is retried up to three times, with a short pause. A 409 means the service already has that execution running, so it counts as delivered. Any other 4xx is logged and not retried.
- **Failure after the retries**: the sweeper fails the run after 15 minutes with "The agent did not start in time."
- **Credentials**: the hub holds the dispatcher key (`GCP_DISPATCHER_KEY`) and never sends it. The identity token is refreshed when it expires. The service's Cloud Run IAM check rejects any caller without the `run.invoker` role before the request reaches the runtime.
- **Stopping**: the provider has no stop call. A cancelled run is marked by the hub, and the runtime stops at its next heartbeat, when the hub refuses it with `run_finished`.

## Stuck runs: the sweeper

`sweep_runs` (`services/sweeper.py`) runs on every worker pass. It checks invocations that are not finished:

| Reason | When | Result |
|---|---|---|
| `start_timeout` | Queued or accepted for more than 15 minutes with no activity | Failed: "The agent did not start in time." |
| `heartbeat_lost` | Running with no event or heartbeat for more than 5 minutes | Failed: "The agent stopped reporting progress." |
| `timed_out` | More than 40 minutes after creation, in any state | Failed: "The agent ran longer than the time limit." |

A failed run's outbox row becomes `done`, so it is never dispatched later.

The limits fit together: the runtime stops itself after 25 minutes (`RUN_TIMEOUT_SECONDS`), and the sweeper fails a run after 40 minutes at most.

## Cancelling

A person cancels with `POST /projects/{id}/invocations/{invocation}/cancel`. Anyone who asked for the run, its agent's owner, or a project admin may do this. The hub:

1. Marks the invocation `cancelled` at once, so the UI and any later event agree.
2. Refuses the runtime's next event or heartbeat with `run_finished`. Within 30 seconds the runtime stops Claude Code.

Cancelling a run that has already finished does nothing.

## The warm instance

The runtime runs as a Cloud Run service with one instance kept running (`min-instances=1`). A run is sent to that instance, so it does not wait for a new container to start.

| Setting | Value | Why |
|---|---|---|
| Concurrency | 1 | One run per instance, so runs never share a clone or a process. |
| Maximum instances | 3 | Caps the cost. A fourth simultaneous run waits for an instance to free up, and fails if none does in time. |
| Minimum instances | 1 | The warm instance. Billed at the idle rate all the time. |
| Request timeout | 1800 seconds | Longer than the runtime's 25-minute limit. |
| Billing | Request-based (default) | CPU is charged while a run is in progress, not between runs. |

Each run gets its own temporary folder, clone and Claude Code process, and deletes them when it ends. Nothing is kept between runs.

**Continuity between messages.** A follow-up message gets the session's earlier turns in its prompt (see [Backend](backend.md#sessions)), not the Claude Code conversation. Resuming the Claude Code conversation on the same instance is deferred: it needs Cloud Run session affinity so that follow-ups reach the instance holding the conversation. See [Implementation plan](../../IMPLEMENTATION_PLAN.md#deferred).

## Tests

`tests/test_execution_outbox.py`, `test_run_lease.py`, `test_sweeper.py`, `test_cloud_execution.py`, `test_service_execution.py`, `test_repo_access.py`, and `test_run_events.py` cover the outbox states and retries, lease checks in order, exact execution-name matching, the repository allow-list, the sweeper's rules, the dispatch request and its retries, and the settings checks. The runtime's `test_service.py` covers the service's requests.
