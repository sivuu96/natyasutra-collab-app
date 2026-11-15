import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth/AuthContext";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { Invocation, MyAgent, ProjectAgent } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, errorMessage } from "../components/ui";
import { canAttachAgent, canCancelInvocation, isActive } from "../agents/permissions";
import { useWorkspace } from "../project/workspaceContext";
import { RunFiles } from "../components/RunFiles";

const STATUS_STYLE: Record<string, string> = {
  queued: "bg-stone-200 text-stone-800",
  accepted: "bg-sky-100 text-sky-800",
  running: "bg-amber-100 text-amber-900",
  completed: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800",
  cancelled: "bg-stone-100 text-stone-600",
};

export function AgentsView() {
  const { project, role, subscribe } = useWorkspace();
  const { user } = useAuth();
  const [attached, setAttached] = useState<ProjectAgent[]>([]);
  const [mine, setMine] = useState<MyAgent[]>([]);
  const [invocations, setInvocations] = useState<Invocation[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const [agents, myAgents, requests] = await Promise.all([
      api<ProjectAgent[]>(`/projects/${project.id}/agents`),
      api<MyAgent[]>("/me/agents"),
      api<Invocation[]>(`/projects/${project.id}/invocations?limit=30`),
    ]);
    setAttached(agents);
    setMine(myAgents);
    setInvocations(requests);
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
          <ErrorText>{error}</ErrorText>
          <Card className="p-0">
            {attached.length === 0 ? (
              <EmptyState>No agents attached yet.</EmptyState>
            ) : (
              <ul className="divide-y divide-stone-100">
                {attached.map((agent) => (
                  <li key={agent.agent_id} className="p-3">
                    <p className="font-medium">
                      {agent.display_name} <span className="font-mono text-xs text-stone-500">@{agent.handle}</span>
                    </p>
                    <p className="text-xs text-stone-500">Owned by {agent.owner_display_name}</p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </section>

        {mine.length > 0 && (
          <section className="space-y-2">
            <h2 className="text-sm font-semibold text-stone-700">Your agents</h2>
            <Card className="p-0">
              <ul className="divide-y divide-stone-100">
                {mine.map((agent) => {
                  const isAttached = attached.some((a) => a.agent_id === agent.id);
                  return (
                    <li key={agent.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                      <div>
                        <p className="font-medium">
                          {agent.display_name} <span className="font-mono text-xs text-stone-500">@{agent.handle}</span>
                        </p>
                        {agent.repository_full_name && (
                          <p className="text-xs text-stone-500">
                            {agent.repository_full_name} @ {agent.default_ref ?? "main"}
                          </p>
                        )}
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
            </Card>
          </section>
        )}
      </div>

      <div className="space-y-4">
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Recent runs</h2>
          <Card className="p-0">
            {invocations.length === 0 ? (
              <EmptyState>No runs yet. Mention an attached agent in a channel to start one.</EmptyState>
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
                        <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[inv.status] ?? ""}`}>{inv.status}</span>
                      </div>
                      {inv.error_message && <p className="text-xs text-red-700">{inv.error_message}</p>}
                      <RunFiles projectId={project.id} invocationId={inv.id} files={inv.files} />
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
