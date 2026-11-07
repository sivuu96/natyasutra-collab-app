import { createContext, useContext } from "react";
import type { RealtimeEnvelope, RealtimeStatus } from "../api/realtime";
import type { Project, ProjectRole } from "../api/types";

export interface WorkspaceState {
  project: Project;
  role: ProjectRole;
  status: RealtimeStatus;
  /** Subscribe to live project events. Returns an unsubscribe function. */
  subscribe: (listener: (event: RealtimeEnvelope) => void) => () => void;
  canWrite: boolean;
  isModerator: boolean;
}

export const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function useWorkspace(): WorkspaceState {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used inside a project workspace");
  return context;
}

export function roleAtLeast(role: ProjectRole, minimum: ProjectRole): boolean {
  const rank: Record<ProjectRole, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };
  return rank[role] >= rank[minimum];
}
