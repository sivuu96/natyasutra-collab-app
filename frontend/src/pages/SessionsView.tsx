import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type UIEvent } from "react";
import { useAuth } from "../auth/AuthContext";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { AgentSession, Invocation, ProjectAgent, SessionMessage, SessionTurn, Workspace } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, Input, Label, Select, TextArea, errorMessage } from "../components/ui";
import { canCancelInvocation, isActive } from "../agents/permissions";
import { appendDelta, clearInvocation, deltaForSession, type StreamingText } from "../sessions/streaming";
import { useWorkspace } from "../project/workspaceContext";
import { Markdown } from "../components/Markdown";
import { RunFiles } from "../components/RunFiles";
import { WorkspaceTree } from "../components/WorkspaceTree";

const MAX_MESSAGE_CHARS = 10000;
const STATUS_LABEL: Record<string, string> = {
  queued: "Waiting to start",
  accepted: "Waiting to start",
  running: "Working on it",
};

export function SessionsView() {
  const { project, role, subscribe, canWrite } = useWorkspace();
  const { user } = useAuth();
  const [sessions, setSessions] = useState<AgentSession[]>([]);
  const [agents, setAgents] = useState<ProjectAgent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<SessionMessage[]>([]);
  const [requests, setRequests] = useState<Invocation[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [streaming, setStreaming] = useState<StreamingText>({});
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ agentId: "", title: "", visibility: "private" as "private" | "project" });
  const transcriptRef = useRef<HTMLDivElement>(null);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);

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
      setWorkspace(null);
      return;
    }
    const [transcript, invocations, folder] = await Promise.all([
      api<SessionMessage[]>(`/projects/${project.id}/sessions/${selectedId}/messages?limit=200`),
      api<Invocation[]>(`/projects/${project.id}/invocations?limit=50`),
      // The folder panel is secondary: if it cannot load, the conversation still shows.
      api<Workspace | null>(`/projects/${project.id}/sessions/${selectedId}/workspace`).catch(() => null),
    ]);
    setMessages(transcript);
    setRequests(invocations.filter((i) => i.session_id === selectedId));
    setWorkspace(folder);
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

  // Keep the newest message in view, unless the reader has scrolled up to read older ones.
  useEffect(() => {
    const el = transcriptRef.current;
    if (el && pinnedToBottom) el.scrollTop = el.scrollHeight;
  }, [messages, streaming, pinnedToBottom]);

  useEffect(() => {
    setPinnedToBottom(true);
  }, [selectedId]);

  function onTranscriptScroll(event: UIEvent<HTMLDivElement>) {
    const el = event.currentTarget;
    setPinnedToBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
  }

  // Requests are already filtered to this session; the newest one is the current turn.
  const latestTurn = requests[0];
  const busy = latestTurn ? isActive(latestTurn.status) : false;
  const canSend = !!selected && selected.can_send && selected.status === "active" && !busy && canWrite;
  const streamingForSession = selected ? Object.entries(streaming).filter(([id]) => requests.some((r) => r.id === id)) : [];
  // A turn that failed or was cancelled leaves no reply in the transcript, so its request is shown next to the message.
  const endedWithoutReply = new Map(
    requests.filter((r) => r.status === "failed" || r.status === "cancelled").map((r) => [r.id, r]),
  );

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

  // Sends one message as a new turn. Returns true when the agent accepted it.
  async function submitTurn(body: string): Promise<boolean> {
    if (!selected || !body.trim()) return false;
    setError(null);
    try {
      await api<SessionTurn>(`/projects/${project.id}/sessions/${selected.id}/turns`, {
        method: "POST",
        json: { body: body.trim() },
      });
      await loadSelected();
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    }
  }

  async function sendDraft() {
    if (!canSend || !draft.trim()) return;
    if (await submitTurn(draft)) setDraft("");
  }

  function send(event: FormEvent) {
    event.preventDefault();
    void sendDraft();
  }

  // Enter sends; Shift+Enter starts a new line.
  function onComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void sendDraft();
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

  async function remove() {
    if (!selected || !window.confirm("Delete this session? Its conversation will no longer be shown to anyone.")) return;
    setError(null);
    try {
      await api<void>(`/projects/${project.id}/sessions/${selected.id}`, { method: "DELETE" });
      setSelectedId(null);
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

  function retry(invocationId: string | null) {
    const original = messages.find((m) => m.role === "user" && m.invocation_id === invocationId);
    if (original) void submitTurn(original.body);
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[16rem_1fr_18rem]">
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

      <section className="flex min-h-[70vh] flex-col gap-3">
        {!selected ? (
          <Card>
            <EmptyState>Start a session with an attached agent to have a private, back-and-forth conversation.</EmptyState>
          </Card>
        ) : (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-stone-200 pb-2">
              <div>
                <h2 className="text-lg font-semibold">{selected.title}</h2>
                <p className="text-xs text-stone-500">
                  Conversation with @{selected.agent_handle} · started by {selected.owner_display_name}
                  {selected.status === "closed" ? " · closed" : ""}
                </p>
              </div>
              {(selected.is_owner && selected.status === "active") || selected.can_delete ? (
                <div className="flex gap-2">
                  {selected.is_owner && selected.status === "active" && (
                    <Button variant="secondary" onClick={() => void setVisibility(selected.visibility === "private" ? "project" : "private")}>
                      {selected.visibility === "private" ? "Share with project" : "Make private"}
                    </Button>
                  )}
                  {selected.is_owner && selected.status === "active" && (
                    <Button variant="ghost" onClick={() => void close()}>
                      Close
                    </Button>
                  )}
                  {selected.can_delete && (
                    <Button variant="ghost" onClick={() => void remove()}>
                      Delete
                    </Button>
                  )}
                </div>
              ) : null}
            </div>
            {selected.visibility === "project" && (
              <p className="text-xs text-stone-500">Shared: every project member can read this conversation and watch it live.</p>
            )}

            <div
              ref={transcriptRef}
              onScroll={onTranscriptScroll}
              className="flex-1 space-y-4 overflow-y-auto rounded-lg bg-stone-50 p-4"
              style={{ maxHeight: "65vh" }}
            >
              {messages.length === 0 && streamingForSession.length === 0 && (
                <EmptyState>No messages yet. Say what you need, and @{selected.agent_handle} will reply here.</EmptyState>
              )}

              {messages.map((m) => {
                const isAgent = m.role === "agent";
                // Your own messages sit on the right. Messages from other members of a shared session sit on the left.
                const mine = !isAgent && (m.author_display_name === null || m.author_display_name === user?.display_name);
                const label = isAgent ? `@${m.agent_handle ?? "agent"}` : mine ? "You" : (m.author_display_name ?? "Member");
                const failed = m.invocation_id ? endedWithoutReply.get(m.invocation_id) : undefined;
                return (
                  <div key={m.id}>
                    <div className={mine ? "flex flex-col items-end" : "flex flex-col items-start"}>
                      <p className="mb-1 text-xs text-stone-500">
                        {label} · {formatTime(m.created_at)}
                      </p>
                      <div
                        className={
                          mine
                            ? "max-w-[80%] rounded-2xl rounded-br-sm bg-amber-100 px-4 py-2 text-stone-900 shadow-sm"
                            : isAgent
                              ? "max-w-[85%] rounded-2xl rounded-bl-sm border border-stone-200 bg-white px-4 py-3 text-stone-900 shadow-sm"
                              : "max-w-[80%] rounded-2xl rounded-bl-sm bg-stone-100 px-4 py-2 text-stone-900 shadow-sm"
                        }
                      >
                        {isAgent ? (
                          <Markdown text={m.body} />
                        ) : (
                          <p className="whitespace-pre-wrap text-sm">{m.body}</p>
                        )}
                      </div>
                      {isAgent && <RunFiles projectId={project.id} invocationId={m.invocation_id} files={m.files} />}
                    </div>

                    {failed && (
                      <div className="mt-2 flex justify-end">
                        <div
                          role="alert"
                          className="max-w-[85%] rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-900"
                        >
                          <p className="font-medium">
                            {failed.status === "cancelled" ? "This reply was cancelled." : "The agent could not finish this reply."}
                          </p>
                          {failed.error_message && <p className="mt-1 text-xs text-red-800">{failed.error_message}</p>}
                          {canSend && (
                            <button
                              className="mt-2 text-sm font-medium text-red-900 underline hover:no-underline"
                              onClick={() => retry(m.invocation_id)}
                            >
                              Try again
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}

              {streamingForSession.map(([id, text]) => (
                <div key={id} className="flex flex-col items-start">
                  <p className="mb-1 text-xs text-stone-500" aria-live="polite">
                    @{selected.agent_handle} · writing
                    <span className="ml-1 animate-pulse">…</span>
                  </p>
                  <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-dashed border-stone-300 bg-white px-4 py-3 text-stone-900">
                    <Markdown text={text} />
                  </div>
                </div>
              ))}
            </div>

            {busy && latestTurn && (
              <div role="status" className="flex items-center justify-between text-xs text-stone-600">
                <span>
                  {STATUS_LABEL[latestTurn.status] ?? latestTurn.status}
                  <span className="ml-1 animate-pulse">…</span>
                </span>
                {canCancelInvocation({ userId: user?.id ?? "", role }, latestTurn.requested_by_user_id, "") && (
                  <button className="text-red-700 hover:underline" onClick={() => void cancelTurn()}>
                    Cancel
                  </button>
                )}
              </div>
            )}

            <ErrorText>{error}</ErrorText>

            {selected.can_send && selected.status === "active" ? (
              <form onSubmit={send} className="space-y-1 rounded-lg border border-stone-200 bg-white p-3 shadow-sm">
                <Label htmlFor="session-composer">Message @{selected.agent_handle}</Label>
                <TextArea
                  id="session-composer"
                  rows={3}
                  maxLength={MAX_MESSAGE_CHARS}
                  value={draft}
                  placeholder="Type a message. Enter sends, Shift+Enter adds a new line."
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={onComposerKey}
                  disabled={busy}
                />
                <div className="flex items-center justify-between">
                  <span className="text-xs text-stone-500">
                    {busy ? "Wait for the reply before sending another message." : `${draft.length}/${MAX_MESSAGE_CHARS}`}
                  </span>
                  <Button type="submit" disabled={!canSend || !draft.trim()}>
                    {busy ? "Waiting for the agent…" : "Send"}
                  </Button>
                </div>
              </form>
            ) : (
              <p className="text-sm text-stone-500">
                {selected.status === "closed"
                  ? "This conversation is closed."
                  : "Only the session owner and project owners can send messages here. You can read the conversation."}
              </p>
            )}

            <details className="text-xs text-stone-500">
              <summary className="cursor-pointer select-none hover:text-stone-700">About sessions</summary>
              <div className="mt-2 space-y-1 rounded-md bg-stone-50 p-3">
                <p>Each message continues this conversation, and the agent sees the earlier messages when it replies.</p>
                <p>Starting a new session starts a conversation with no earlier messages.</p>
                <p>Private sessions are visible only to you. Sharing lets project members read and watch, but not write.</p>
                <p>Each message starts a run on the agent’s cloud runtime. The reply appears here when it finishes.</p>
                {canWrite && <p>Mentions in channels are one-off requests; sessions are conversations.</p>}
              </div>
            </details>
          </>
        )}
      </section>

      <aside className="space-y-3">{selected && <WorkspaceTree workspace={workspace} />}</aside>
    </div>
  );
}
