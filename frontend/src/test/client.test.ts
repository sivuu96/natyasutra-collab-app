import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, readCsrfToken } from "../api/client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

describe("readCsrfToken", () => {
  it("reads the ns_csrf cookie among others", () => {
    expect(readCsrfToken("a=1; ns_csrf=abc%2Bdef; b=2")).toBe("abc+def");
  });

  it("returns an empty string when the cookie is absent", () => {
    expect(readCsrfToken("a=1")).toBe("");
  });

  it("does not match a cookie whose name merely ends in ns_csrf", () => {
    expect(readCsrfToken("xns_csrf=evil")).toBe("");
  });
});

describe("api", () => {
  afterEach(() => {
    Object.defineProperty(document, "cookie", { value: "", writable: true, configurable: true });
  });

  it("sends the CSRF header on state-changing requests only", async () => {
    Object.defineProperty(document, "cookie", { value: "ns_csrf=token123", writable: true, configurable: true });
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await api("/projects");
    await api("/teams", { method: "POST", json: { name: "x" } });

    const [, getInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [postUrl, postInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(new Headers(getInit.headers).has("X-CSRF-Token")).toBe(false);
    expect(postUrl).toBe("/api/v1/teams");
    expect(new Headers(postInit.headers).get("X-CSRF-Token")).toBe("token123");
    expect(new Headers(postInit.headers).get("Content-Type")).toBe("application/json");
    expect(postInit.body).toBe(JSON.stringify({ name: "x" }));
    expect(postInit.credentials).toBe("same-origin");
  });

  it("throws ApiError carrying the server detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Invalid email or password" })));
    const failure = await api("/auth/login", { method: "POST", json: {} }).catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(401);
    expect((failure as ApiError).message).toBe("Invalid email or password");
  });

  it("explains validation failures without dumping server internals", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, { detail: [{ loc: ["body"], msg: "x" }] })));
    await expect(api("/teams")).rejects.toThrow("Please check the highlighted fields.");
  });

  it("explains rate limiting", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 429 })));
    await expect(api("/auth/login")).rejects.toThrow("Too many attempts");
  });

  it("returns undefined for 204 responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api("/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });
});
