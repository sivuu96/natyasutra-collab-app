import { describe, expect, it } from "vitest";
import {
  canAttachAgent,
  canCancelInvocation,
  canDecideApproval,
  canDetachAgent,
  isActive,
} from "./permissions";

const OWNER = "owner-user";
const OTHER = "other-user";

describe("agent permission mirrors", () => {
  it("lets the agent owner and admins decide approvals, but not other members", () => {
    expect(canDecideApproval({ userId: OWNER, role: "member" }, OWNER)).toBe(true);
    expect(canDecideApproval({ userId: OTHER, role: "admin" }, OWNER)).toBe(true);
    expect(canDecideApproval({ userId: OTHER, role: "member" }, OWNER)).toBe(false);
    expect(canDecideApproval({ userId: OTHER, role: "viewer" }, OWNER)).toBe(false);
  });

  it("requires member role to attach, and owner or admin to attach someone else's agent", () => {
    expect(canAttachAgent({ userId: OWNER, role: "viewer" }, OWNER)).toBe(false);
    expect(canAttachAgent({ userId: OWNER, role: "member" }, OWNER)).toBe(true);
    expect(canAttachAgent({ userId: OTHER, role: "member" }, OWNER)).toBe(false);
    expect(canAttachAgent({ userId: OTHER, role: "owner" }, OWNER)).toBe(true);
  });

  it("restricts detaching to owner or admin", () => {
    expect(canDetachAgent({ userId: OTHER, role: "member" }, OWNER)).toBe(false);
    expect(canDetachAgent({ userId: OWNER, role: "member" }, OWNER)).toBe(true);
  });

  it("lets the requester cancel their own request", () => {
    expect(canCancelInvocation({ userId: OTHER, role: "member" }, OTHER, OWNER)).toBe(true);
    expect(canCancelInvocation({ userId: OTHER, role: "member" }, OWNER, OWNER)).toBe(false);
  });

  it("treats finished invocations as inactive", () => {
    expect(isActive("running")).toBe(true);
    expect(isActive("awaiting_approval")).toBe(true);
    expect(isActive("completed")).toBe(false);
    expect(isActive("rejected")).toBe(false);
  });
});
