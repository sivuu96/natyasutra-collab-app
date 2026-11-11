import { useCallback, useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { api } from "../api/client";
import type { Member, MemberCandidate, ProjectRole } from "../api/types";
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
  const [candidates, setCandidates] = useState<MemberCandidate[]>([]);
  const [highlight, setHighlight] = useState(0);
  const [picked, setPicked] = useState<string | null>(null);

  const load = useCallback(async () => {
    setMembers(await api<Member[]>(`/projects/${project.id}/members`));
  }, [project.id]);

  useEffect(() => {
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [load]);

  // Suggestions for the email field: the same listbox pattern as @mentions, fed by people who share a project.
  useEffect(() => {
    const term = email.trim();
    if (term.length < 2 || term === picked) {
      setCandidates([]);
      return;
    }
    const timer = setTimeout(() => {
      api<MemberCandidate[]>(`/projects/${project.id}/member-candidates?q=${encodeURIComponent(term)}`)
        .then((list) => {
          setCandidates(list);
          setHighlight(0);
        })
        .catch(() => setCandidates([]));
    }, 200);
    return () => clearTimeout(timer);
  }, [email, picked, project.id]);

  function pickCandidate(candidate: MemberCandidate) {
    setEmail(candidate.email);
    setPicked(candidate.email);
    setCandidates([]);
  }

  function onEmailKey(event: KeyboardEvent<HTMLInputElement>) {
    if (candidates.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlight((i) => (i + 1) % candidates.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlight((i) => (i - 1 + candidates.length) % candidates.length);
    } else if (event.key === "Enter" || event.key === "Tab") {
      event.preventDefault();
      pickCandidate(candidates[Math.min(highlight, candidates.length - 1)]);
    } else if (event.key === "Escape") {
      setCandidates([]);
    }
  }

  // Mirrors the server rules: only owners grant or touch owner; admins grant up to admin.
  const assignableRoles = ROLES.filter((r) => (role === "owner" ? true : r !== "owner"));
  const canManage = (target: Member) => isModerator && (role === "owner" || target.role !== "owner");

  async function add(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api<Member>(`/projects/${project.id}/members`, { method: "POST", json: { email: email.trim(), role: newRole } });
      setEmail("");
      setPicked(null);
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
              <div className="relative">
                {candidates.length > 0 && (
                  <ul
                    role="listbox"
                    aria-label="Suggested people"
                    className="absolute left-0 right-0 top-full z-10 mt-1 overflow-hidden rounded-md border border-stone-200 bg-white text-sm shadow-lg"
                  >
                    {candidates.map((candidate, index) => (
                      <li key={candidate.user_id} role="option" aria-selected={index === highlight}>
                        <button
                          type="button"
                          onMouseDown={(e) => {
                            e.preventDefault(); // keep focus in the field
                            pickCandidate(candidate);
                          }}
                          className={`flex w-full flex-col px-3 py-1.5 text-left ${index === highlight ? "bg-amber-100" : "hover:bg-stone-100"}`}
                        >
                          <span className="font-medium text-stone-900">{candidate.display_name}</span>
                          <span className="truncate text-xs text-stone-500">{candidate.email}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
                <Input
                  id="member-email"
                  type="email"
                  required
                  autoComplete="off"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value);
                    setPicked(null);
                  }}
                  onKeyDown={onEmailKey}
                />
              </div>
              <p className="mt-1 text-xs text-stone-500">They must already have an account. Type two letters to see people you share a project with.</p>
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
