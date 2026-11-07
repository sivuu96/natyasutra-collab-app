/**
 * Thin fetch wrapper for the hub API.
 *
 * - Sends cookies (session is HttpOnly; the browser attaches it, script never reads it).
 * - Echoes the ns_csrf cookie in X-CSRF-Token on every state-changing request (double-submit CSRF).
 * - Turns error responses into ApiError with the server's `detail` text.
 */

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const API_PREFIX = "/api/v1";

export function readCsrfToken(cookieString: string = document.cookie): string {
  const match = cookieString.match(/(?:^|;\s*)ns_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

export interface ApiRequest extends Omit<RequestInit, "body"> {
  json?: unknown;
}

function messageFor(status: number, data: unknown, fallback: string): string {
  if (data && typeof data === "object" && "detail" in data) {
    const detail = (data as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) return "Please check the highlighted fields.";
  }
  if (status === 429) return "Too many attempts. Please wait and try again.";
  return fallback || "Something went wrong";
}

export async function api<T>(path: string, init: ApiRequest = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  let body: BodyInit | undefined;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  if (method !== "GET" && method !== "HEAD") {
    headers.set("X-CSRF-Token", readCsrfToken());
  }

  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    method,
    headers,
    body,
    credentials: "same-origin",
  });

  if (response.status === 204) return undefined as T;

  const isJson = response.headers.get("content-type")?.includes("application/json") ?? false;
  const data: unknown = isJson ? await response.json() : null;
  if (!response.ok) {
    throw new ApiError(response.status, messageFor(response.status, data, response.statusText));
  }
  return data as T;
}
