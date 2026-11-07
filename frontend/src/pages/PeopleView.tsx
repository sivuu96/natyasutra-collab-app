import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Member, ProjectRole } from "../api/types";
import { Button, Card, EmptyState, ErrorText, Input, Label, Select, errorMessage } from "../components/ui";
import { useAuth } from "../auth/AuthContext";
import { useWorkspace } from "../project/workspaceContext";

const ROLES: ProjectRole[] = ["owner", "admin", "member", "viewer"];

export function PeopleView() {
  const { project, role, isModerator } = useWorkspace();
  const { user } = useAuth();
  const [members, setMembers] = useState<Member[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [newRole, setNewRole] = useState<ProjectRole>("member");

  const load = useCallback(async () => {
    setMembers(await api<Member[]>(`/projects/${project.id}/members`));
  }, [project.id]);

  useEffect(() => {
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [load]);

  // Mirrors the server rules: only owners grant or touch owner; admins grant up to admin.
  const assignableRoles = ROLES.filter((r) => (role === "owner" ? true : r !== "owner"));
  const canManage = (target: Member) => isModerator && (role === "owner" || target.role !== "owner");

  async function add(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api<Member>(`/projects/${project.id}/members`, { method: "POST", json: { email: email.trim(), role: newRole } });
      setEmail("");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function changeRole(target: Member, nextRole: ProjectRole) {
    setError(null);
    try {
      await api<Member>(`/projects/${project.id}/members/${target.user_id}`, {
        method: "PATCH",
        json: { role: nextRole },
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function remove(target: Member) {
    if (!window.confirm(`Remove ${target.display_name} from this project?`)) return;
    setError(null);
    try {
      await api<void>(`/projects/${project.id}/members/${target.user_id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">People</h2>
        <ErrorText>{error}</ErrorText>
        <Card className="p-0">
          {members.length === 0 ? (
            <EmptyState>No members.</EmptyState>
          ) : (
            <ul className="divide-y divide-stone-100">
              {members.map((m) => (
                <li key={m.user_id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                  <div>
                    <p className="font-medium">
                      {m.display_name} {m.user_id === user?.id && <span className="text-xs text-stone-500">(you)</span>}
                    </p>
                    <p className="text-xs text-stone-500">{m.email}</p>
                  </div>
                  {canManage(m) ? (
                    <div className="flex items-center gap-2">
                      <Select
                        aria-label={`Role of ${m.display_name}`}
                        value={m.role}
                        onChange={(e) => void changeRole(m, e.target.value as ProjectRole)}
                      >
                        {assignableRoles.map((r) => (
                          <option key={r} value={r}>
                            {r}
                          </option>
                        ))}
                      </Select>
                      <Button variant="ghost" onClick={() => void remove(m)}>
                        Remove
                      </Button>
                    </div>
                  ) : (
                    <span className="rounded bg-stone-100 px-2 py-1 text-xs text-stone-700">{m.role}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>

      {isModerator && (
        <Card className="h-fit">
          <h2 className="mb-3 text-sm font-semibold text-stone-700">Add a person</h2>
          <form onSubmit={add} className="space-y-3">
            <div>
              <Label htmlFor="member-email">Email</Label>
              <Input id="member-email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
              <p className="mt-1 text-xs text-stone-500">They must already have an account.</p>
            </div>
            <div>
              <Label htmlFor="member-role">Role</Label>
              <Select id="member-role" className="w-full" value={newRole}
                onChange={(e) => setNewRole(e.target.value as ProjectRole)}>
                {assignableRoles.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
            </div>
            <Button type="submit" className="w-full" disabled={!email.trim()}>
              Add to project
            </Button>
          </form>
        </Card>
      )}
    </div>
  );
}
