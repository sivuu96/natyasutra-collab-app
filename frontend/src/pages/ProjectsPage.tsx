import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Project, Team } from "../api/types";
import { Button, Card, EmptyState, ErrorText, Input, Label, Select, TextArea, errorMessage } from "../components/ui";

export function ProjectsPage() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [teams, setTeams] = useState<Team[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [teamName, setTeamName] = useState("");
  const [form, setForm] = useState({ teamId: "", name: "", description: "" });

  async function refresh() {
    const [projectList, teamList] = await Promise.all([api<Project[]>("/projects"), api<Team[]>("/teams")]);
    setProjects(projectList);
    setTeams(teamList);
    setForm((current) => ({ ...current, teamId: current.teamId || teamList[0]?.id || "" }));
  }

  useEffect(() => {
    refresh().catch((err: unknown) => setError(errorMessage(err)));
  }, []);

  async function createTeam(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const team = await api<Team>("/teams", { method: "POST", json: { name: teamName.trim() } });
      setTeamName("");
      setForm((current) => ({ ...current, teamId: team.id }));
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function createProject(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api<Project>(`/teams/${form.teamId}/projects`, {
        method: "POST",
        json: { name: form.name.trim(), description: form.description.trim() || null },
      });
      setForm((current) => ({ ...current, name: "", description: "" }));
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
      <section className="space-y-3">
        <h1 className="text-xl font-semibold">Your projects</h1>
        <ErrorText>{error}</ErrorText>
        {projects === null ? (
          <p className="text-sm text-stone-500">Loading…</p>
        ) : projects.length === 0 ? (
          <EmptyState>You are not in any project yet. Create one, or ask a project admin to add you.</EmptyState>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2">
            {projects.map((p) => (
              <li key={p.id}>
                <Link to={`/projects/${p.id}`} className="block h-full">
                  <Card className="h-full transition-colors hover:border-amber-400">
                    <h2 className="font-semibold text-stone-900">{p.name}</h2>
                    {p.description && <p className="mt-1 text-sm text-stone-600">{p.description}</p>}
                    <p className="mt-3 text-xs uppercase tracking-wide text-stone-500">Your role: {p.role}</p>
                  </Card>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <aside className="space-y-4">
        {teams.length === 0 ? (
          <Card>
            <h2 className="mb-2 text-sm font-semibold text-stone-700">Create a team first</h2>
            <form onSubmit={createTeam} className="space-y-2">
              <Label htmlFor="team-name">Team name</Label>
              <Input id="team-name" required value={teamName} onChange={(e) => setTeamName(e.target.value)} />
              <Button type="submit" className="w-full" disabled={!teamName.trim()}>
                Create team
              </Button>
            </form>
          </Card>
        ) : (
          <Card>
            <h2 className="mb-3 text-sm font-semibold text-stone-700">New project</h2>
            <form onSubmit={createProject} className="space-y-3">
              <div>
                <Label htmlFor="project-team">Team</Label>
                <Select id="project-team" className="w-full" value={form.teamId}
                  onChange={(e) => setForm({ ...form, teamId: e.target.value })}>
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="project-name">Name</Label>
                <Input id="project-name" required maxLength={120} value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="project-desc">Description</Label>
                <TextArea id="project-desc" rows={3} maxLength={2000} value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })} />
              </div>
              <Button type="submit" className="w-full" disabled={!form.name.trim() || !form.teamId}>
                Create project
              </Button>
            </form>
          </Card>
        )}
      </aside>
    </div>
  );
}
