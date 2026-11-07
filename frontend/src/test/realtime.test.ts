import { describe, expect, it, vi } from "vitest";
import { connectProject, NOT_AUTHORISED_CLOSE_CODE, socketUrl, type RealtimeEnvelope, type RealtimeStatus } from "../api/realtime";

/** A controllable stand-in for WebSocket. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  readonly url: string;
  closed = false;

  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  open() {
    this.onopen?.();
  }

  receive(envelope: RealtimeEnvelope) {
    this.onmessage?.({ data: JSON.stringify(envelope) });
  }

  drop(code = 1006) {
    this.onclose?.({ code });
  }
}

function setup(overrides: { backoff?: (n: number) => number; initialSince?: number } = {}) {
  FakeSocket.instances = [];
  const events: RealtimeEnvelope[] = [];
  const statuses: RealtimeStatus[] = [];
  const close = connectProject({
    projectId: "p1",
    onEvent: (e) => events.push(e),
    onStatus: (s) => statuses.push(s),
    createSocket: (url) => new FakeSocket(url) as unknown as WebSocket,
    backoff: overrides.backoff ?? (() => 0),
    initialSince: overrides.initialSince,
  });
  return { close, events, statuses, socket: () => FakeSocket.instances[FakeSocket.instances.length - 1] };
}

describe("socketUrl", () => {
  it("uses wss on https and includes the resume cursor", () => {
    const url = socketUrl("abc", 7, { protocol: "https:", host: "hub.example" } as Location);
    expect(url).toBe("wss://hub.example/api/v1/ws/projects/abc?since=7");
  });

  it("uses ws on http", () => {
    expect(socketUrl("abc", 0, { protocol: "http:", host: "localhost:5173" } as Location)).toMatch(/^ws:\/\//);
  });
});

describe("connectProject", () => {
  it("connects from the initial cursor and reports live status", () => {
    const { statuses, socket } = setup({ initialSince: 5 });
    expect(socket().url).toContain("since=5");
    socket().open();
    expect(statuses).toEqual(["connecting", "live"]);
  });

  it("delivers events and drops duplicates by sequence", () => {
    const { events, socket } = setup();
    socket().open();
    socket().receive({ event_type: "task.created", sequence: 1 });
    socket().receive({ event_type: "task.created", sequence: 1 });
    socket().receive({ event_type: "task.updated", sequence: 2 });
    expect(events.map((e) => e.sequence)).toEqual([1, 2]);
  });

  it("ignores heartbeats and malformed frames", () => {
    const { events, socket } = setup();
    socket().open();
    socket().receive({ event_type: "realtime.heartbeat" });
    socket().onmessage?.({ data: "not json" });
    expect(events).toEqual([]);
  });

  it("reconnects from the last seen sequence after a drop", () => {
    vi.useFakeTimers();
    try {
      const { socket, statuses } = setup();
      socket().open();
      socket().receive({ event_type: "task.created", sequence: 4 });
      socket().drop();
      expect(statuses.at(-1)).toBe("offline");
      vi.advanceTimersByTime(0);
      expect(socket().url).toContain("since=4");
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses the backoff function for retry delays", () => {
    vi.useFakeTimers();
    try {
      const delays: number[] = [];
      const { socket } = setup({ backoff: (attempt) => { delays.push(attempt); return 1000 * (attempt + 1); } });
      socket().drop();
      vi.advanceTimersByTime(1000);
      socket().drop();
      vi.advanceTimersByTime(2000);
      expect(delays).toEqual([0, 1]);
      expect(FakeSocket.instances).toHaveLength(3);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops retrying when the server refuses authorisation", () => {
    vi.useFakeTimers();
    try {
      const { socket, statuses } = setup();
      socket().drop(NOT_AUTHORISED_CLOSE_CODE);
      vi.advanceTimersByTime(60000);
      expect(FakeSocket.instances).toHaveLength(1);
      expect(statuses.at(-1)).toBe("stopped");
    } finally {
      vi.useRealTimers();
    }
  });

  it("closes the socket and stops reporting when the caller unsubscribes", () => {
    const { close, socket, statuses } = setup();
    socket().open();
    close();
    expect(socket().closed).toBe(true);
    expect(statuses.at(-1)).toBe("stopped");
  });
});
