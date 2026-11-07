/**
 * Live output for agent sessions: deltas accumulate per invocation until the final message arrives.
 * Pure functions so the rules can be tested without rendering.
 */

export type StreamingText = Record<string, string>;

export function appendDelta(state: StreamingText, invocationId: string, text: string): StreamingText {
  return { ...state, [invocationId]: (state[invocationId] ?? "") + text };
}

/** The persisted message replaces the streamed draft for the same invocation. */
export function clearInvocation(state: StreamingText, invocationId: string): StreamingText {
  if (!(invocationId in state)) return state;
  const next = { ...state };
  delete next[invocationId];
  return next;
}

/** Deltas are only shown for the session on screen; other sessions' output is ignored here. */
export function deltaForSession(
  event: { payload: Record<string, unknown>; session_id?: string | null },
  sessionId: string,
): { invocationId: string; text: string } | null {
  if (event.session_id !== sessionId) return null;
  const invocationId = event.payload.invocation_id;
  const text = event.payload.text;
  if (typeof invocationId !== "string" || typeof text !== "string") return null;
  return { invocationId, text };
}
