import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { RealtimeEnvelope } from "../api/realtime";

const listeners: Array<(event: RealtimeEnvelope) => void> = [];

vi.mock("../api/client", () => ({
  api: vi.fn(async (path: string) => {
    if (path.endsWith("/channels")) return [{ id: "c1", name: "general", topic: null }];
    return [];
  }),
  ApiError: class extends Error {},
}));

vi.mock("../project/workspaceContext", () => ({
  useWorkspace: () => ({
    project: { id: "p1", name: "Test" },
    subscribe: (listener: (event: RealtimeEnvelope) => void) => {
      listeners.push(listener);
      return () => undefined;
    },
    canWrite: true,
    isModerator: false,
  }),
}));

vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u1" } }),
}));

import { DiscussionsView } from "./DiscussionsView";

describe("progress line", () => {
  it("shows the agent's current stage when a progress frame arrives", async () => {
    render(<DiscussionsView />);
    await act(async () => {
      await Promise.resolve();
    });
    expect(listeners.length).toBeGreaterThan(0);
    act(() => {
      for (const listener of listeners) {
        listener({
          event_type: "agent.invocation.progress",
          sequence: undefined,
          payload: { invocation_id: "i1", agent_handle: "claude", stage: "Writing the reply" },
        } as RealtimeEnvelope);
      }
    });
    expect(screen.getByLabelText("Agent progress")).toBeTruthy();
    expect(screen.getByText(/Writing the reply/)).toBeTruthy();
  });
});
