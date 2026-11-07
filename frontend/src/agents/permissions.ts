/**
 * Client-side mirrors of the hub's agent rules, used to decide what to SHOW. The server enforces
 * every rule again; hiding a button is never the security boundary.
 */
import type { ProjectRole } from "../api/types";

const RANK: Record<ProjectRole, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };

export interface AgentActor {
  userId: string;
  role: ProjectRole;
}

/** Approvals belong to the agent's owner and to project admins. */
export function canDecideApproval(actor: AgentActor, agentOwnerId: string): boolean {
  return actor.userId === agentOwnerId || RANK[actor.role] >= RANK.admin;
}

/** Attaching: the agent's owner (members and up), or an admin. */
export function canAttachAgent(actor: AgentActor, agentOwnerId: string): boolean {
  if (RANK[actor.role] < RANK.member) return false;
  return actor.userId === agentOwnerId || RANK[actor.role] >= RANK.admin;
}

/** Detaching: the agent's owner, or an admin. */
export function canDetachAgent(actor: AgentActor, agentOwnerId: string): boolean {
  return actor.userId === agentOwnerId || RANK[actor.role] >= RANK.admin;
}

/** Cancelling: whoever asked, the agent's owner, or an admin. */
export function canCancelInvocation(actor: AgentActor, requesterId: string | null, agentOwnerId: string): boolean {
  return actor.userId === requesterId || actor.userId === agentOwnerId || RANK[actor.role] >= RANK.admin;
}

export const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled", "rejected"]);

export function isActive(status: string): boolean {
  return !TERMINAL_STATUSES.has(status);
}
