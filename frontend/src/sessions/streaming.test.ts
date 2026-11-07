import { describe, expect, it } from "vitest";
import { appendDelta, clearInvocation, deltaForSession } from "./streaming";

describe("streaming output", () => {
  it("accumulates deltas per invocation", () => {
    let state = appendDelta({}, "inv-1", "Hel");
    state = appendDelta(state, "inv-1", "lo");
    state = appendDelta(state, "inv-2", "other");
    expect(state).toEqual({ "inv-1": "Hello", "inv-2": "other" });
  });

  it("drops the draft once the persisted message arrives", () => {
    const state = clearInvocation({ "inv-1": "Hello", "inv-2": "x" }, "inv-1");
    expect(state).toEqual({ "inv-2": "x" });
    expect(clearInvocation(state, "missing")).toBe(state);
  });

  it("only uses deltas that belong to the session on screen", () => {
    const delta = {
      session_id: "s-1",
      payload: { invocation_id: "inv-9", text: "partial" },
    };
    expect(deltaForSession(delta, "s-1")).toEqual({ invocationId: "inv-9", text: "partial" });
    expect(deltaForSession(delta, "s-2")).toBeNull();
  });

  it("ignores channel deltas and malformed payloads", () => {
    expect(deltaForSession({ session_id: null, payload: { invocation_id: "i", text: "x" } }, "s-1")).toBeNull();
    expect(deltaForSession({ session_id: "s-1", payload: { invocation_id: 7, text: "x" } }, "s-1")).toBeNull();
  });
});
