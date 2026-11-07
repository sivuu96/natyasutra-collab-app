import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { Approval, Invocation, MyAgent, ProjectAgent } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, errorMessage } from "../components/ui";
import {
  canAttachAgent,
  canCancelInvocation,
  canDecideApproval,
  canDetachAgent,
  isActive,
} from "../agents/permissions";
import { useWorkspace } from "../project/workspaceContext";

const STATUS_STYLE: Record<string, string> = {
  queued: "bg-stone-200 text-stone-800",
  delivered: "bg-sky-100 text-sky-800",
  accepted: "bg-sky-100 text-sky-800",
  running: "bg-amber-100 text-amber-900",
  awaiting_approval: "bg-violet-100 text-violet-900",
  completed: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
  cancelled: "bg-stone-100 text-stone-600",
  rejected: "bg-red-50 text-red-700",
};

export function AgentsView() {
  const { project, role, subscribe } = useWorkspace();
  const { user } = useAuth();
  const [attached, setAttached] = useState<ProjectAgent[]>([]);
  const [mine, setMine] = useState<MyAgent[]>([]);
  const [invocations, setInvocations] = useState<Invocation[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [agents, myAgents, requests, pending] = await Promise.all([
      api<ProjectAgent[]>(`/projects/${project.id}/agents`),
      api<MyAgent[]>("/me/agents"),
      api<Invocation[]>(`/projects/${project.id}/invocations?limit=30`),
      api<Approval[]>(`/projects/${project.id}/approvals?status=pending`),
    ]);
    setAttached(agents);
    setMine(myAgents);
    setInvocations(requests);
    setApprovals(pending);
  }, [project.id]);

  useEffect(() => {
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [load]);

  useEffect(() => {
    return subscribe((event: RealtimeEnvelope) => {
      if (event.event_type.startsWith("agent.") && event.event_type !== "agent.output.delta") {
        void load();
      }
    });
  }, [subscribe, load]);

  const actor = { userId: user?.id ?? "", role };

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Agents in this project</h2>
          <p className="text-xs text-stone-500">
            An agent runs on its owner’s machine, in a folder the owner approved. Mention it with @handle in a channel.
          </p>
          <ErrorText>{error}</ErrorText>
          <Card className="p-0">
            {attached.length === 0 ? (
              <EmptyState>No agents attached yet.</EmptyState>
            ) : (
              <ul className="divide-y divide-stone-100">
                {attached.map((agent) => (
                  <li key={agent.agent_id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                    <div>
                      <p className="font-medium">
                        {agent.display_name} <span className="font-mono text-xs text-stone-500">@{agent.handle}</span>
                      </p>
                      <p className="text-xs text-stone-500">
                        {agent.harness} · owned by {agent.owner_display_name}
                        {agent.description ? ` · ${agent.description}` : ""}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs ${agent.online ? "bg-emerald-100 text-emerald-800" : "bg-stone-200 text-stone-700"}`}
                      >
                        {agent.online ? "online" : "offline"}
                      </span>
                      {canDetachAgent(actor, agent.owner_user_id) && (
                        <Button variant="ghost" onClick={() => void run(() => api(`/projects/${project.id}/agents/${agent.agent_id}`, { method: "DELETE" }))}>
                          Detach
                        </Button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-semibold text-stone-700">Your agents</h2>
          <Card className="p-0">
            {mine.length === 0 ? (
              <EmptyState>
                You have no agents. Pair a machine on the Devices page, then add an agent with the bridge CLI.
              </EmptyState>
            ) : (
              <ul className="divide-y divide-stone-100">
                {mine.map((agent) => {
                  const isAttached = attached.some((a) => a.agent_id === agent.id);
                  return (
                    <li key={agent.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                      <div>
                        <p className="font-medium">
                          {agent.display_name} <span className="font-mono text-xs text-stone-500">@{agent.handle}</span>
                        </p>
                        <p className="text-xs text-stone-500">
                          on {agent.device_name} · {agent.revoked ? "revoked" : agent.online ? "online" : "offline"}
                        </p>
                      </div>
                      {!agent.revoked && !isAttached && canAttachAgent(actor, user?.id ?? "") && (
                        <Button
                          variant="secondary"
                          onClick={() =>
                            void run(() => api(`/projects/${project.id}/agents`, { method: "POST", json: { agent_id: agent.id } }))
                          }
                        >
                          Offer to this project
                        </Button>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </section>
      </div>

      <div className="space-y-4">
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Approvals waiting for a decision</h2>
          {approvals.length === 0 ? (
            <Card>
              <EmptyState>Nothing is waiting for approval.</EmptyState>
            </Card>
          ) : (
            approvals.map((approval) => {
              const agent = attached.find((a) => a.agent_id === approval.agent_id);
              const allowed = agent ? canDecideApproval(actor, agent.owner_user_id) : role === "owner" || role === "admin";
              return (
                <Card key={approval.id} className="space-y-2">
                  <p className="text-sm">
                    <span className="font-mono">@{approval.agent_handle}</span> wants to use{" "}
                    <strong>{approval.tools.join(", ")}</strong>
                  </p>
                  {approval.detail && <p className="text-xs text-stone-600">{approval.detail}</p>}
                  <p className="text-xs text-stone-500">Requested {formatTime(approval.created_at)}</p>
                  {allowed ? (
                    <div className="flex gap-2">
                      <Button onClick={() => void run(() => api(`/projects/${project.id}/approvals/${approval.id}`, { method: "POST", json: { decision: "approved" } }))}>
                        Approve
                      </Button>
                      <Button variant="danger" onClick={() => void run(() => api(`/projects/${project.id}/approvals/${approval.id}`, { method: "POST", json: { decision: "denied" } }))}>
                        Deny
                      </Button>
                    </div>
                  ) : (
                    <p className="text-xs text-stone-500">Only the agent’s owner or a project admin can decide.</p>
                  )}
                </Card>
              );
            })
          )}
        </section>

        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Recent requests</h2>
          <Card className="p-0">
            {invocations.length === 0 ? (
              <EmptyState>No requests yet. Mention an attached agent in a channel to start one.</EmptyState>
            ) : (
              <ul className="divide-y divide-stone-100 text-sm">
                {invocations.map((inv) => {
                  const agent = attached.find((a) => a.agent_id === inv.agent_id);
                  const ownerId = agent?.owner_user_id ?? "";
                  const cancellable = isActive(inv.status) && canCancelInvocation(actor, inv.requested_by_user_id, ownerId);
                  return (
                    <li key={inv.id} className="space-y-1 p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-mono text-xs">@{inv.agent_handle}</span>
                        <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[inv.status] ?? ""}`}>
                          {inv.status.replace("_", " ")}
                        </span>
                      </div>
                      {inv.error_message && <p className="text-xs text-red-700">{inv.error_message}</p>}
                      {inv.status === "rejected" && inv.error_code === "agent_offline" && (
                        <p className="text-xs text-stone-600">The agent was offline, so nothing ran. It was not queued.</p>
                      )}
                      <div className="flex items-center justify-between text-xs text-stone-500">
                        <span>{formatTime(inv.created_at)}</span>
                        {cancellable && (
                          <button
                            className="text-red-700 hover:underline"
                            onClick={() =>
                              void run(() => api(`/projects/${project.id}/invocations/${inv.id}/cancel`, { method: "POST" }))
                            }
                          >
                            Cancel
                          </button>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
        </section>
      </div>
    </div>
  );
}
