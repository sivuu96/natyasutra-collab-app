import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { Decision, DecisionRevision, DecisionStatus } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, Input, Label, TextArea, errorMessage } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { useWorkspace } from "../project/workspaceContext";

const STATUS_STYLE: Record<DecisionStatus, string> = {
  proposed: "bg-stone-200 text-stone-800",
  accepted: "bg-emerald-100 text-emerald-800",
  rejected: "bg-red-100 text-red-800",
  superseded: "bg-stone-100 text-stone-500 line-through",
};

export function DecisionsView() {
  const { project, subscribe, canWrite, isModerator } = useWorkspace();
  const { user } = useAuth();
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [selected, setSelected] = useState<Decision | null>(null);
  const [revisions, setRevisions] = useState<DecisionRevision[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ title: "", statement: "", context: "", rationale: "" });

  const load = useCallback(async () => {
    setDecisions(await api<Decision[]>(`/projects/${project.id}/decisions`));
  }, [project.id]);

  const openDecision = useCallback(
    async (id: string) => {
      const [decision, history] = await Promise.all([
        api<Decision>(`/projects/${project.id}/decisions/${id}`),
        api<DecisionRevision[]>(`/projects/${project.id}/decisions/${id}/revisions`),
      ]);
      setSelected(decision);
      setRevisions(history);
    },
    [project.id],
  );

  useEffect(() => {
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [load]);

  useEffect(() => {
    return subscribe((event: RealtimeEnvelope) => {
      if (event.event_type === "decision.updated") {
        void load();
        if (selected && event.payload && (event.payload as { decision_id?: string }).decision_id === selected.id) {
          void openDecision(selected.id);
        }
      }
    });
  }, [subscribe, load, openDecision, selected]);

  // Only the decision owner or a project admin can accept/reject (server enforces this too).
  const canDecide = selected !== null && (isModerator || selected.owner_user_id === user?.id);

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await api<Decision>(`/projects/${project.id}/decisions`, {
        method: "POST",
        json: {
          title: form.title.trim(),
          statement: form.statement.trim(),
          context: form.context.trim() || null,
          rationale: form.rationale.trim() || null,
        },
      });
      setForm({ title: "", statement: "", context: "", rationale: "" });
      await load();
      await openDecision(created.id);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function setStatus(status: DecisionStatus) {
    if (!selected) return;
    setError(null);
    try {
      await api<Decision>(`/projects/${project.id}/decisions/${selected.id}`, { method: "PATCH", json: { status } });
      await load();
      await openDecision(selected.id);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Decisions</h2>
        <ErrorText>{error}</ErrorText>
        <Card className="p-0">
          {decisions.length === 0 ? (
            <EmptyState>No decisions recorded yet.</EmptyState>
          ) : (
            <ul className="divide-y divide-stone-100">
              {decisions.map((d) => (
                <li key={d.id}>
                  <button
                    className={`flex w-full items-center justify-between gap-2 p-3 text-left hover:bg-stone-50 ${selected?.id === d.id ? "bg-amber-50" : ""}`}
                    onClick={() => void openDecision(d.id)}
                  >
                    <span className="font-medium text-stone-900">{d.title}</span>
                    <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[d.status]}`}>{d.status}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {selected && (
          <Card className="space-y-3">
            <div className="flex items-start justify-between gap-2">
              <h3 className="text-base font-semibold">{selected.title}</h3>
              <span className={`rounded px-2 py-0.5 text-xs ${STATUS_STYLE[selected.status]}`}>{selected.status}</span>
            </div>
            <p className="whitespace-pre-wrap text-sm">{selected.statement}</p>
            {selected.context && (
              <div>
                <h4 className="text-xs font-semibold uppercase text-stone-500">Context</h4>
                <p className="whitespace-pre-wrap text-sm">{selected.context}</p>
              </div>
            )}
            {selected.rationale && (
              <div>
                <h4 className="text-xs font-semibold uppercase text-stone-500">Rationale</h4>
                <p className="whitespace-pre-wrap text-sm">{selected.rationale}</p>
              </div>
            )}
            {canWrite && selected.status === "proposed" && canDecide && (
              <div className="flex gap-2">
                <Button onClick={() => void setStatus("accepted")}>Accept</Button>
                <Button variant="danger" onClick={() => void setStatus("rejected")}>
                  Reject
                </Button>
              </div>
            )}
            {selected.status === "proposed" && !canDecide && (
              <p className="text-xs text-stone-500">Awaiting the decision owner or a project admin.</p>
            )}
            <details>
              <summary className="cursor-pointer text-sm text-amber-800">Revision history ({revisions.length})</summary>
              <ol className="mt-2 space-y-1 text-xs text-stone-600">
                {revisions.map((r) => (
                  <li key={r.revision_no}>
                    Rev {r.revision_no} · {formatTime(r.created_at)} · status {String(r.snapshot.status)}
                  </li>
                ))}
              </ol>
            </details>
          </Card>
        )}
      </section>

      {canWrite && (
        <Card className="h-fit">
          <h2 className="mb-3 text-sm font-semibold text-stone-700">Propose a decision</h2>
          <form onSubmit={create} className="space-y-3">
            <div>
              <Label htmlFor="decision-title">Title</Label>
              <Input id="decision-title" required maxLength={200} value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="decision-statement">Decision statement</Label>
              <TextArea id="decision-statement" required rows={3} value={form.statement}
                onChange={(e) => setForm({ ...form, statement: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="decision-context">Context</Label>
              <TextArea id="decision-context" rows={2} value={form.context}
                onChange={(e) => setForm({ ...form, context: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="decision-rationale">Rationale</Label>
              <TextArea id="decision-rationale" rows={2} value={form.rationale}
                onChange={(e) => setForm({ ...form, rationale: e.target.value })} />
            </div>
            <Button type="submit" className="w-full" disabled={!form.title.trim() || !form.statement.trim()}>
              Record as proposed
            </Button>
            <p className="text-xs text-stone-500">Decisions start as proposed. Nothing is accepted automatically.</p>
          </form>
        </Card>
      )}
    </div>
  );
}
