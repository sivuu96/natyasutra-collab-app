import { describe, expect, it } from "vitest";
import {
  canAttachAgent,
  canCancelInvocation,
  isActive,
} from "./permissions";

const OWNER = "owner-user";
const OTHER = "other-user";

describe("agent permission mirrors", () => {
  it("requires member role to attach, and owner or admin to attach someone else's agent", () => {
    expect(canAttachAgent({ userId: OWNER, role: "viewer" }, OWNER)).toBe(false);
    expect(canAttachAgent({ userId: OWNER, role: "member" }, OWNER)).toBe(true);
    expect(canAttachAgent({ userId: OTHER, role: "member" }, OWNER)).toBe(false);
    expect(canAttachAgent({ userId: OTHER, role: "owner" }, OWNER)).toBe(true);
  });

  it("lets the requester cancel their own request", () => {
    expect(canCancelInvocation({ userId: OTHER, role: "member" }, OTHER, OWNER)).toBe(true);
    expect(canCancelInvocation({ userId: OTHER, role: "member" }, OWNER, OWNER)).toBe(false);
  });

  it("treats finished invocations as inactive", () => {
    expect(isActive("running")).toBe(true);
    expect(isActive("queued")).toBe(true);
    expect(isActive("completed")).toBe(false);
    expect(isActive("cancelled")).toBe(false);
  });
});
