import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { AgentSession, Invocation, ProjectAgent, SessionMessage, SessionTurn } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, Input, Label, Select, TextArea, errorMessage } from "../components/ui";
import { canCancelInvocation, isActive } from "../agents/permissions";
import { appendDelta, clearInvocation, deltaForSession, type StreamingText } from "../sessions/streaming";
import { useWorkspace } from "../project/workspaceContext";
import { RunFiles } from "../components/RunFiles";

export function SessionsView() {
  const { project, role, subscribe, canWrite } = useWorkspace();
  const { user } = useAuth();
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [agents, setAgents] = useState<ProjectAgent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<SessionMessage[]>([]);
  const [requests, setRequests] = useState<Invocation[]>([]);
  const [streaming, setStreaming] = useState<StreamingText>({});
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ agentId: "", title: "", visibility: "private" as "private" | "project" });

  const selected = useMemo(() => sessions.find((s) => s.id === selectedId) ?? null, [sessions, selectedId]);

  const loadSessions = useCallback(async () => {
    const list = await api<AgentSession[]>(`/projects/${project.id}/sessions`);
    setSessions(list);
    setSelectedId((current) => current ?? list[0]?.id ?? null);
  }, [project.id]);

  const loadSelected = useCallback(async () => {
    if (!selectedId) {
      setMessages([]);
      setRequests([]);
      return;
    }
    const [transcript, invocations] = await Promise.all([
      api<SessionMessage[]>(`/projects/${project.id}/sessions/${selectedId}/messages?limit=200`),
      api<Invocation[]>(`/projects/${project.id}/invocations?limit=50`),
    ]);
    setMessages(transcript);
    setRequests(invocations.filter((i) => i.session_id === selectedId));
  }, [project.id, selectedId]);

  useEffect(() => {
    loadSessions().catch((err: unknown) => setError(errorMessage(err)));
    api<ProjectAgent[]>(`/projects/${project.id}/agents`)
      .then((list) => {
        setAgents(list);
        setForm((current) => ({ ...current, agentId: current.agentId || list[0]?.agent_id || "" }));
      })
      .catch(() => setAgents([]));
  }, [loadSessions, project.id]);

  useEffect(() => {
    loadSelected().catch((err: unknown) => setError(errorMessage(err)));
  }, [loadSelected]);

  useEffect(() => {
    return subscribe((event: RealtimeEnvelope) => {
      const type = event.event_type;
      if (type.startsWith("session.") && type !== "session.message.created") void loadSessions();
      if (type === "session.message.created") {
        void loadSelected();
        const invocationId = (event.payload as { invocation_id?: string }).invocation_id;
        if (invocationId) setStreaming((s) => clearInvocation(s, invocationId));
      }
      if (type === "agent.output.delta" && selectedId) {
        const delta = deltaForSession(
          { payload: event.payload as Record<string, unknown>, session_id: event.session_id as string | null },
          selectedId,
        );
        if (delta) setStreaming((s) => appendDelta(s, delta.invocationId, delta.text));
      }
      if (type.startsWith("agent.invocation.") && type !== "agent.invocation.created") void loadSelected();
    });
  }, [subscribe, loadSessions, loadSelected, selectedId]);

  // Requests are already filtered to this session; the newest one is the current turn.
  const latestTurn = requests[0];
  const busy = latestTurn ? isActive(latestTurn.status) : false;
  const canSend = !!selected && selected.is_owner && selected.status === "active" && !busy && canWrite;

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await api<AgentSession>(`/projects/${project.id}/sessions`, {
        method: "POST",
        json: { agent_id: form.agentId, title: form.title.trim(), visibility: form.visibility },
      });
      setForm((current) => ({ ...current, title: "" }));
      setSelectedId(created.id);
      await loadSessions();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    if (!selected || !draft.trim()) return;
    setError(null);
    try {
      await api<SessionTurn>(`/projects/${project.id}/sessions/${selected.id}/turns`, {
        method: "POST",
        json: { body: draft.trim() },
      });
      setDraft("");
      await loadSelected();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function cancelTurn() {
    if (!selected || !latestTurn) return;
    setError(null);
    try {
      await api(`/projects/${project.id}/invocations/${latestTurn.id}/cancel`, { method: "POST" });
      await loadSelected();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function setVisibility(visibility: "private" | "project") {
    if (!selected) return;
    setError(null);
    try {
      await api<AgentSession>(`/projects/${project.id}/sessions/${selected.id}/visibility`, {
        method: "POST",
        json: { visibility },
      });
      await loadSessions();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function close() {
    if (!selected || !window.confirm("Close this session? No more turns can be sent.")) return;
    setError(null);
    try {
      await api<AgentSession>(`/projects/${project.id}/sessions/${selected.id}/close`, { method: "POST" });
      await loadSessions();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const streamingForSession = selected ? Object.entries(streaming).filter(([id]) => requests.some((r) => r.id === id)) : [];

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr_16rem]">
      <aside className="space-y-3">
        <h2 className="text-sm font-semibold text-stone-700">Sessions</h2>
        <Card className="p-0">
          {sessions.length === 0 ? (
            <EmptyState>No sessions you can see yet.</EmptyState>
          ) : (
            <ul className="divide-y divide-stone-100">
              {sessions.map((s) => (
                <li key={s.id}>
                  <button
                    onClick={() => setSelectedId(s.id)}
                    aria-current={s.id === selectedId ? "true" : undefined}
                    className={`w-full p-3 text-left text-sm ${s.id === selectedId ? "bg-amber-50" : "hover:bg-stone-50"}`}
                  >
                    <span className="block font-medium">{s.title}</span>
                    <span className="text-xs text-stone-500">
                      @{s.agent_handle} · {s.visibility === "private" ? "private" : "shared"}
                      {s.status === "closed" ? " · closed" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        {canWrite && agents.length > 0 && (
          <Card className="space-y-2">
            <h3 className="text-xs font-semibold uppercase text-stone-500">New session</h3>
            <form onSubmit={create} className="space-y-2">
              <div>
                <Label htmlFor="session-agent">Agent</Label>
                <Select id="session-agent" className="w-full" value={form.agentId}
                  onChange={(e) => setForm({ ...form, agentId: e.target.value })}>
                  {agents.map((a) => (
                    <option key={a.agent_id} value={a.agent_id}>
                      @{a.handle}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="session-title">Title</Label>
                <Input id="session-title" required maxLength={120} value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="session-visibility">Visibility</Label>
                <Select id="session-visibility" className="w-full" value={form.visibility}
                  onChange={(e) => setForm({ ...form, visibility: e.target.value as "private" | "project" })}>
                  <option value="private">Private (only me)</option>
                  <option value="project">Shared with the project</option>
                </Select>
              </div>
              <Button type="submit" className="w-full" disabled={!form.title.trim() || !form.agentId}>
                Start session
              </Button>
            </form>
          </Card>
        )}
      </aside>

      <section className="flex min-h-[60vh] flex-col gap-3">
        <ErrorText>{error}</ErrorText>
        {!selected ? (
          <Card>
            <EmptyState>Start a session with an attached agent to have a private, back-and-forth conversation.</EmptyState>
          </Card>
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-lg font-semibold">{selected.title}</h2>
                <p className="text-xs text-stone-500">
                  @{selected.agent_handle} · owned by {selected.owner_display_name}
                  {selected.status === "closed" ? " · closed" : ""}
                </p>
              </div>
              {selected.is_owner && selected.status === "active" && (
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => void setVisibility(selected.visibility === "private" ? "project" : "private")}>
                    {selected.visibility === "private" ? "Share with project" : "Make private"}
                  </Button>
                  <Button variant="ghost" onClick={() => void close()}>
                    Close
                  </Button>
                </div>
              )}
            </div>
            {selected.visibility === "project" && (
              <p className="text-xs text-stone-500">Shared: every project member can read this transcript and watch it live.</p>
            )}
            <Card className="flex-1 space-y-3 overflow-y-auto">
              {messages.length === 0 && streamingForSession.length === 0 && (
                <EmptyState>No messages yet.</EmptyState>
              )}
              {messages.map((m) => (
                <div key={m.id} className="text-sm">
                  <p className="text-xs text-stone-500">
                    {m.role === "agent" ? `@${m.agent_handle ?? "agent"}` : (m.author_display_name ?? "You")} ·{" "}
                    {formatTime(m.created_at)}
                  </p>
                  <p className="whitespace-pre-wrap">{m.body}</p>
                  <RunFiles projectId={project.id} invocationId={m.invocation_id} files={m.files} />
                </div>
              ))}
              {streamingForSession.map(([id, text]) => (
                <div key={id} className="text-sm italic text-stone-600" aria-live="polite">
                  <p className="text-xs not-italic text-stone-500">@{selected.agent_handle} is replying…</p>
                  <p className="whitespace-pre-wrap">{text}</p>
                </div>
              ))}
            </Card>
            {busy && latestTurn && (
              <div className="flex items-center justify-between text-xs text-stone-600">
                <span>Turn in progress: {latestTurn.status.replace("_", " ")}</span>
                {canCancelInvocation({ userId: user?.id ?? "", role }, latestTurn.requested_by_user_id, "") && (
                  <button className="text-red-700 hover:underline" onClick={() => void cancelTurn()}>
                    Cancel turn
                  </button>
                )}
              </div>
            )}
            {selected.is_owner && selected.status === "active" ? (
              <form onSubmit={send} className="space-y-2">
                <Label htmlFor="session-composer">Message the agent</Label>
                <TextArea id="session-composer" rows={3} maxLength={10000} value={draft}
                  onChange={(e) => setDraft(e.target.value)} disabled={busy} />
                <div className="flex justify-end">
                  <Button type="submit" disabled={!canSend || !draft.trim()}>
                    {busy ? "Waiting for the agent…" : "Send"}
                  </Button>
                </div>
              </form>
            ) : (
              <p className="text-sm text-stone-500">
                {selected.status === "closed"
                  ? "This session is closed."
                  : "Only the session owner can send messages. You can read the transcript."}
              </p>
            )}
          </>
        )}
      </section>

      <aside className="space-y-2">
        <h2 className="text-sm font-semibold text-stone-700">About sessions</h2>
        <Card className="space-y-2 text-xs text-stone-600">
          <p>
            A session is one conversation. Each message you send continues it, and the agent sees the earlier turns
            when it replies.
          </p>
          <p>Starting a new session starts a new conversation with no earlier turns.</p>
          <p>Private sessions are visible only to you. Sharing lets project members read and watch, but not write.</p>
          <p>Each message starts a run on the agent’s cloud runtime. The reply appears in the transcript when it finishes.</p>
          {canWrite && <p>Mentions in channels are one-off requests; sessions are conversations.</p>}
        </Card>
        {requests.length > 0 && <p className="text-xs text-stone-400">{requests.length} recent requests</p>}
      </aside>
    </div>
  );
}
