import { describe, expect, it } from "vitest";
import { activeMention, applyMention, suggestMentions } from "./mentions";
import { activeRuns, applyRunEvent, STALE_AFTER_MS, type RunProgressMap } from "./runProgress";

describe("activeMention", () => {
  it("finds the token the caret is in", () => {
    expect(activeMention("hi @cla", 7)).toEqual({ start: 3, query: "cla" });
  });

  it("finds a bare @ right after typing it", () => {
    expect(activeMention("@", 1)).toEqual({ start: 0, query: "" });
  });

  it("ignores email addresses and completed mentions", () => {
    expect(activeMention("mail me at a@b", 14)).toBeNull();
    expect(activeMention("@claude ok", 10)).toBeNull();
  });

  it("returns null with no @ before the caret", () => {
    expect(activeMention("hello there", 11)).toBeNull();
  });
});

describe("suggestMentions", () => {
  const agents = [
    { handle: "claude", displayName: "Claude", kind: "agent" as const },
    { handle: "codex", displayName: "Codex", kind: "agent" as const },
    { handle: "claude-review", displayName: "Review", kind: "agent" as const },
  ];

  it("matches by prefix, case-insensitively, sorted", () => {
    expect(suggestMentions(agents, "CL").map((a) => a.handle)).toEqual(["claude", "claude-review"]);
  });

  it("suggests people alongside agents", () => {
    const all = [...agents, { handle: "clara", displayName: "Clara", kind: "person" as const }];
    expect(suggestMentions(all, "cl").map((c) => `${c.kind}:${c.handle}`)).toEqual([
      "agent:claude",
      "agent:claude-review",
      "person:clara",
    ]);
  });

  it("returns everything for an empty query", () => {
    expect(suggestMentions(agents, "")).toHaveLength(3);
  });
});

describe("applyMention", () => {
  it("replaces the partial token and moves the caret after the handle", () => {
    const mention = activeMention("ask @cl please", 7)!;
    expect(applyMention("ask @cl please", mention, 7, "claude")).toEqual({
      text: "ask @claude please",
      caret: 11,
    });
    const end = activeMention("@cl", 3)!;
    expect(applyMention("@cl", end, 3, "claude")).toEqual({ text: "@claude ", caret: 8 });
  });
});

describe("applyRunEvent", () => {
  it("shows the latest stage and clears the run when it finishes", () => {
    let state: RunProgressMap = {};
    state = applyRunEvent(state, {
      event_type: "agent.invocation.progress",
      payload: { invocation_id: "i1", agent_handle: "claude", stage: "Running the agent" },
    });
    expect(state.i1.stage).toBe("Running the agent");

    state = applyRunEvent(state, { event_type: "agent.invocation.completed", payload: { invocation_id: "i1" } });
    expect(state).toEqual({});
  });

  it("ignores events without an invocation", () => {
    const state: RunProgressMap = {};
    expect(applyRunEvent(state, { event_type: "agent.invocation.progress", payload: {} })).toBe(state);
  });
});

describe("activeRuns", () => {
  it("hides runs that stopped reporting", () => {
    const now = 1_000_000;
    let state: RunProgressMap = {};
    state = applyRunEvent(state, { event_type: "agent.invocation.progress", payload: { invocation_id: "a", agent_handle: "claude", stage: "x" } }, now);
    expect(activeRuns(state, now + 1000)).toHaveLength(1);
    expect(activeRuns(state, now + STALE_AFTER_MS + 1)).toHaveLength(0);
  });
});
