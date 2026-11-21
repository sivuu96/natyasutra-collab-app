export interface User {
  id: string;
  email: string;
  display_name: string;
}

export type ProjectRole = "owner" | "admin" | "member" | "viewer";

export interface Project {
  id: string;
  team_id: string;
  name: string;
  slug: string;
  description: string | null;
  role: ProjectRole | null;
  latest_sequence?: number | null;
}

export interface Team {
  id: string;
  name: string;
  slug: string;
}

export interface Member {
  user_id: string;
  email: string;
  display_name: string;
  role: ProjectRole;
  handle: string | null;
}

export interface MemberCandidate {
  user_id: string;
  email: string;
  display_name: string;
}

export interface Channel {
  id: string;
  project_id: string;
  name: string;
  topic: string | null;
}

export interface Message {
  id: string;
  project_id: string;
  channel_id: string;
  parent_message_id: string | null;
  author_user_id: string | null;
  author_display_name: string | null;
  author_agent_handle?: string | null;
  body: string;
  created_at: string;
  edited_at: string | null;
  deleted_at: string | null;
}

export type TaskStatus = "backlog" | "todo" | "in_progress" | "in_review" | "blocked" | "done" | "cancelled";
export type TaskPriority = "low" | "medium" | "high" | "urgent";

export interface Task {
  id: string;
  project_id: string;
  title: string;
  description: string | null;
  acceptance_criteria: string | null;
  status: TaskStatus;
  priority: TaskPriority;
  creator_user_id: string | null;
  assignee_user_id: string | null;
  due_date: string | null;
  created_at: string;
  updated_at: string;
}

export type DecisionStatus = "proposed" | "accepted" | "rejected" | "superseded";

export interface Decision {
  id: string;
  project_id: string;
  title: string;
  statement: string;
  context: string | null;
  options: Record<string, unknown>[];
  rationale: string | null;
  consequences: string | null;
  status: DecisionStatus;
  proposer_user_id: string | null;
  owner_user_id: string | null;
  current_revision: number;
  created_at: string;
  updated_at: string;
}

export interface DecisionRevision {
  revision_no: number;
  snapshot: Record<string, unknown>;
  changed_by_user_id: string | null;
  created_at: string;
}

export interface ProjectEvent {
  event_id: string;
  event_type: string;
  sequence: number;
  occurred_at: string;
  actor_user_id: string | null;
  payload: Record<string, unknown>;
}

export interface AuditEntry {
  id: string;
  occurred_at: string;
  action: string;
  actor_user_id: string | null;
  target_type: string | null;
  target_id: string | null;
  outcome: string;
  request_id: string | null;
}

export interface ProjectAgent {
  agent_id: string;
  handle: string;
  display_name: string;
  owner_user_id: string;
  owner_display_name: string;
}

export interface MyAgent {
  id: string;
  handle: string;
  display_name: string;
  repository_full_name: string | null;
  default_ref: string | null;
  revoked: boolean;
}

type InvocationStatus = "queued" | "accepted" | "running" | "completed" | "failed" | "cancelled";

export interface RunFile {
  id: string;
  name: string;
  size_bytes: number;
}

export interface Invocation {
  id: string;
  agent_id: string;
  agent_handle: string;
  channel_id: string | null;
  session_id: string | null;
  requested_by_user_id: string | null;
  status: InvocationStatus;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
  files: RunFile[];
}

export interface AgentSession {
  id: string;
  project_id: string;
  agent_id: string;
  agent_handle: string;
  title: string;
  visibility: "private" | "project";
  status: "active" | "closed";
  owner_user_id: string;
  owner_display_name: string;
  is_owner: boolean;
  can_send: boolean;
  can_delete: boolean;
  last_activity_at: string | null;
  created_at: string;
}

export interface SessionMessage {
  id: string;
  role: "user" | "agent";
  author_display_name: string | null;
  agent_handle: string | null;
  body: string;
  invocation_id: string | null;
  created_at: string;
  files: RunFile[];
}

export interface SessionTurn {
  invocation_id: string;
  status: InvocationStatus;
  error_code: string | null;
}

export interface SearchResults {
  messages: { id: string; channel_id: string; snippet: string }[];
  tasks: { id: string; title: string; status: TaskStatus }[];
  decisions: { id: string; title: string; status: DecisionStatus }[];
}
