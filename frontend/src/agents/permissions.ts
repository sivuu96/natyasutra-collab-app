/**
 * Client-side mirrors of the hub's agent rules, used to decide what to SHOW. The server enforces
 * every rule again; hiding a button is never the security boundary.
 */
import type { ProjectRole } from "../api/types";

const RANK: Record<ProjectRole, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };

interface AgentActor {
  userId: string;
  role: ProjectRole;
}

/** Attaching: the agent's owner (members and up), or an admin. */
export function canAttachAgent(actor: AgentActor, agentOwnerId: string): boolean {
  if (RANK[actor.role] < RANK.member) return false;
  return actor.userId === agentOwnerId || RANK[actor.role] >= RANK.admin;
}

/** Cancelling: whoever asked, the agent's owner, or an admin. */
export function canCancelInvocation(actor: AgentActor, requesterId: string | null, agentOwnerId: string): boolean {
  return actor.userId === requesterId || actor.userId === agentOwnerId || RANK[actor.role] >= RANK.admin;
}

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

export function isActive(status: string): boolean {
  return !TERMINAL_STATUSES.has(status);
}
