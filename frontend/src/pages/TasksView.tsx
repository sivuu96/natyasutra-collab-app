import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { RealtimeEnvelope } from "../api/realtime";
import type { Member, Task, TaskPriority, TaskStatus } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, Input, Label, Select, TextArea, errorMessage } from "../components/ui";
import { useWorkspace } from "../project/workspaceContext";

const STATUSES: TaskStatus[] = ["backlog", "todo", "in_progress", "in_review", "blocked", "done", "cancelled"];
const PRIORITIES: TaskPriority[] = ["low", "medium", "high", "urgent"];

export function TasksView() {
  const { project, subscribe, canWrite } = useWorkspace();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [filter, setFilter] = useState<"all" | TaskStatus>("all");
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ title: "", description: "", acceptance_criteria: "", priority: "medium", assignee: "" });

  const load = useCallback(async () => {
    const query = filter === "all" ? "" : `?status=${filter}`;
    setTasks(await api<Task[]>(`/projects/${project.id}/tasks${query}`));
  }, [project.id, filter]);

  useEffect(() => {
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [load]);

  useEffect(() => {
    api<Member[]>(`/projects/${project.id}/members`).then(setMembers).catch(() => setMembers([]));
  }, [project.id]);

  useEffect(() => {
    return subscribe((event: RealtimeEnvelope) => {
      if (event.event_type === "task.created" || event.event_type === "task.updated") void load();
    });
  }, [subscribe, load]);

  const nameOf = (id: string | null) => members.find((m) => m.user_id === id)?.display_name ?? "Unassigned";

  async function create(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api<Task>(`/projects/${project.id}/tasks`, {
        method: "POST",
        json: {
          title: form.title.trim(),
          description: form.description.trim() || null,
          acceptance_criteria: form.acceptance_criteria.trim() || null,
          priority: form.priority,
          assignee_user_id: form.assignee || null,
        },
      });
      setForm({ title: "", description: "", acceptance_criteria: "", priority: "medium", assignee: "" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function update(task: Task, changes: Partial<Pick<Task, "status" | "priority">>) {
    setError(null);
    try {
      await api<Task>(`/projects/${project.id}/tasks/${task.id}`, { method: "PATCH", json: changes });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Tasks</h2>
          <div className="flex items-center gap-2">
            <Label htmlFor="task-filter">Status</Label>
            <Select id="task-filter" value={filter} onChange={(e) => setFilter(e.target.value as "all" | TaskStatus)}>
              <option value="all">All</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {s.replace("_", " ")}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <ErrorText>{error}</ErrorText>
        <Card className="p-0">
          {tasks.length === 0 ? (
            <EmptyState>No tasks match this view.</EmptyState>
          ) : (
            <ul className="divide-y divide-stone-100">
              {tasks.map((task) => (
                <li key={task.id} className="flex flex-col gap-2 p-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-stone-900">{task.title}</p>
                    <p className="text-xs text-stone-500">
                      Assigned: {nameOf(task.assignee_user_id)} · Updated {formatTime(task.updated_at)}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Select
                      aria-label={`Status of ${task.title}`}
                      value={task.status}
                      disabled={!canWrite}
                      onChange={(e) => void update(task, { status: e.target.value as TaskStatus })}
                    >
                      {STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {s.replace("_", " ")}
                        </option>
                      ))}
                    </Select>
                    <Select
                      aria-label={`Priority of ${task.title}`}
                      value={task.priority}
                      disabled={!canWrite}
                      onChange={(e) => void update(task, { priority: e.target.value as TaskPriority })}
                    >
                      {PRIORITIES.map((p) => (
                        <option key={p} value={p}>
                          {p}
                        </option>
                      ))}
                    </Select>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {canWrite && (
        <Card className="h-fit">
          <h2 className="mb-3 text-sm font-semibold text-stone-700">New task</h2>
          <form onSubmit={create} className="space-y-3">
            <div>
              <Label htmlFor="task-title">Title</Label>
              <Input id="task-title" required maxLength={200} value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="task-desc">Description</Label>
              <TextArea id="task-desc" rows={3} value={form.description}
                onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div>
              <Label htmlFor="task-ac">Acceptance criteria</Label>
              <TextArea id="task-ac" rows={2} value={form.acceptance_criteria}
                onChange={(e) => setForm({ ...form, acceptance_criteria: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <Label htmlFor="task-priority">Priority</Label>
                <Select id="task-priority" className="w-full" value={form.priority}
                  onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                  {PRIORITIES.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="task-assignee">Assignee</Label>
                <Select id="task-assignee" className="w-full" value={form.assignee}
                  onChange={(e) => setForm({ ...form, assignee: e.target.value })}>
                  <option value="">Unassigned</option>
                  {members.map((m) => (
                    <option key={m.user_id} value={m.user_id}>
                      {m.display_name}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
            <Button type="submit" className="w-full" disabled={!form.title.trim()}>
              Create task
            </Button>
          </form>
        </Card>
      )}
    </div>
  );
}
