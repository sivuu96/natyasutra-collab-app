/**
 * Project realtime connection with resume-from-sequence and exponential backoff with jitter.
 *
 * Behaviour:
 * - Connects to /api/v1/ws/projects/{id}?since=<last sequence seen>.
 * - Server replays missed events, then streams live ones. Duplicates are dropped by sequence.
 * - Closes with 1008 (not authorised) => stop; do not retry.
 * - Any other drop => reconnect with backoff (1s doubling, capped at 30s, full jitter).
 */

export type RealtimeStatus = "connecting" | "live" | "offline" | "stopped";

export interface RealtimeEnvelope {
  event_type: string;
  sequence?: number;
  [key: string]: unknown;
}

interface RealtimeOptions {
  projectId: string;
  onEvent: (event: RealtimeEnvelope) => void;
  onStatus?: (status: RealtimeStatus) => void;
  /** Injectable for tests. */
  createSocket?: (url: string) => WebSocket;
  /** Injectable for tests. Returns a delay in ms for the given retry attempt (0-based). */
  backoff?: (attempt: number) => number;
  initialSince?: number;
}

export const NOT_AUTHORISED_CLOSE_CODE = 1008;
const BASE_DELAY_MS = 1000;
const MAX_DELAY_MS = 30000;

function defaultBackoff(attempt: number): number {
  const ceiling = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  return Math.floor(Math.random() * ceiling);
}

export function socketUrl(projectId: string, since: number, location: Location = window.location): string {
  const scheme = location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${location.host}/api/v1/ws/projects/${encodeURIComponent(projectId)}?since=${since}`;
}

export function connectProject(options: RealtimeOptions): () => void {
  const createSocket = options.createSocket ?? ((url: string) => new WebSocket(url));
  const backoff = options.backoff ?? defaultBackoff;
  let lastSequence = options.initialSince ?? 0;
  let attempt = 0;
  let stopped = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const setStatus = (status: RealtimeStatus) => options.onStatus?.(status);

  const open = () => {
    if (stopped) return;
    setStatus("connecting");
    socket = createSocket(socketUrl(options.projectId, lastSequence));

    socket.onopen = () => {
      attempt = 0;
      setStatus("live");
    };

    socket.onmessage = (message: MessageEvent<string>) => {
      let envelope: RealtimeEnvelope;
      try {
        envelope = JSON.parse(message.data) as RealtimeEnvelope;
      } catch {
        return; // ignore malformed frames rather than crash the view
      }
      if (envelope.event_type === "realtime.heartbeat") return;
      if (envelope.event_type === "realtime.resync_required") {
        socket?.close();
        return;
      }
      if (typeof envelope.sequence === "number") {
        if (envelope.sequence <= lastSequence) return; // duplicate after reconnect
        lastSequence = envelope.sequence;
      }
      options.onEvent(envelope);
    };

    socket.onclose = (event: CloseEvent) => {
      socket = null;
      if (stopped) return;
      if (event.code === NOT_AUTHORISED_CLOSE_CODE) {
        stopped = true;
        setStatus("stopped");
        return;
      }
      setStatus("offline");
      const delay = backoff(attempt);
      attempt += 1;
      timer = setTimeout(open, delay);
    };
  };

  open();

  return () => {
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
    socket?.close();
    setStatus("stopped");
  };
}
