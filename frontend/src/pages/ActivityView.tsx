import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { AuditEntry, ProjectEvent } from "../api/types";
import { Card, EmptyState, ErrorText, formatTime, errorMessage } from "../components/ui";
import { useWorkspace } from "../project/workspaceContext";

const LABELS: Record<string, string> = {
  "conversation.message.created": "posted a message",
  "conversation.message.updated": "edited or deleted a message",
  "conversation.channel.created": "created a channel",
  "task.created": "created a task",
  "task.updated": "updated a task",
  "decision.updated": "recorded or changed a decision",
  "project.seeded": "seeded development data",
};

export function ActivityView() {
  const { project, subscribe, isModerator } = useWorkspace();
  const [events, setEvents] = useState<ProjectEvent[]>([]);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const feed = await api<ProjectEvent[]>(`/projects/${project.id}/activity?limit=200`);
    setEvents([...feed].reverse());
    if (isModerator) setAudit(await api<AuditEntry[]>(`/projects/${project.id}/audit?limit=100`));
  }, [project.id, isModerator]);

  useEffect(() => {
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [load]);

  useEffect(() => {
    return subscribe((event: RealtimeEnvelope) => {
      if (event.event_type !== "realtime.heartbeat") void load();
    });
  }, [subscribe, load]);

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Recent activity</h2>
        <ErrorText>{error}</ErrorText>
        <Card className="p-0">
          {events.length === 0 ? (
            <EmptyState>No activity yet.</EmptyState>
          ) : (
            <ul className="divide-y divide-stone-100 text-sm">
              {events.map((e) => (
                <li key={e.event_id} className="flex justify-between gap-2 p-3">
                  <span>{LABELS[e.event_type] ?? e.event_type}</span>
                  <span className="shrink-0 text-xs text-stone-500">
                    #{e.sequence} · {formatTime(e.occurred_at)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {isModerator && (
        <section className="space-y-2">
          <h2 className="text-lg font-semibold">Audit log</h2>
          <p className="text-xs text-stone-500">Actions and outcomes only. Message content is never recorded here.</p>
          <Card className="p-0">
            {audit.length === 0 ? (
              <EmptyState>No audit entries.</EmptyState>
            ) : (
              <ul className="divide-y divide-stone-100 text-xs">
                {audit.map((a) => (
                  <li key={a.id} className="grid grid-cols-[auto_1fr] gap-x-3 p-2">
                    <time className="text-stone-500">{formatTime(a.occurred_at)}</time>
                    <span>
                      {a.action} · {a.outcome}
                      {a.target_type ? ` · ${a.target_type}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </section>
      )}
    </div>
  );
}
