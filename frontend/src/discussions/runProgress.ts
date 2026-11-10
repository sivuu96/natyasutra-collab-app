// Tracks what each running agent is doing, from realtime events. Keyed by invocation id.

export interface RunProgress {
  invocationId: string;
  agentHandle: string;
  stage: string;
  /** When the last update arrived (ms since epoch). Used to hide runs that stopped reporting. */
  updatedAt: number;
}

/** A run with no update for this long is no longer shown. The hub sweeps stuck runs within minutes. */
export const STALE_AFTER_MS = 6 * 60 * 1000;

export type RunProgressMap = Record<string, RunProgress>;

const FINISHED = new Set([
  "agent.invocation.completed",
  "agent.invocation.failed",
  "agent.invocation.cancelled",
]);

/** Apply one realtime event. Returns the same object when nothing changed. */
export function applyRunEvent(
  state: RunProgressMap,
  event: { event_type?: unknown; payload?: unknown },
  now: number = Date.now(),
): RunProgressMap {
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  const invocationId = typeof payload.invocation_id === "string" ? payload.invocation_id : null;
  if (!invocationId) return state;

  if (event.event_type === "agent.invocation.progress") {
    const agentHandle = typeof payload.agent_handle === "string" ? payload.agent_handle : "agent";
    const stage = typeof payload.stage === "string" ? payload.stage : "";
    return { ...state, [invocationId]: { invocationId, agentHandle, stage, updatedAt: now } };
  }
  if (typeof event.event_type === "string" && FINISHED.has(event.event_type) && invocationId in state) {
    const next = { ...state };
    delete next[invocationId];
    return next;
  }
  return state;
}

/** Runs that are still reporting, newest first. */
export function activeRuns(state: RunProgressMap, now: number = Date.now()): RunProgress[] {
  return Object.values(state)
    .filter((run) => now - run.updatedAt < STALE_AFTER_MS)
    .sort((a, b) => b.updatedAt - a.updatedAt);
}
