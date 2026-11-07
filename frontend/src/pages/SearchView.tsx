import { useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type { SearchResults } from "../api/types";
import { Button, Card, EmptyState, ErrorText, Input, Label, errorMessage } from "../components/ui";
import { useWorkspace } from "../project/workspaceContext";

export function SearchView() {
  const { project } = useWorkspace();
  const { projectId = "" } = useParams();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [results, setResults] = useState<SearchResults | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (submitted.length < 2) return;
    let cancelled = false;
    api<SearchResults>(`/projects/${project.id}/search?q=${encodeURIComponent(submitted)}`)
      .then((r) => {
        if (!cancelled) setResults(r);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [project.id, submitted]);

  function search(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setResults(null);
    setSubmitted(query.trim());
  }

  const empty = results && results.messages.length + results.tasks.length + results.decisions.length === 0;

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold">Search</h2>
      <form onSubmit={search} className="flex max-w-xl items-end gap-2">
        <div className="flex-1">
          <Label htmlFor="search-q">Search messages, tasks and decisions</Label>
          <Input id="search-q" value={query} minLength={2} maxLength={200} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Button type="submit" disabled={query.trim().length < 2}>
          Search
        </Button>
      </form>
      <ErrorText>{error}</ErrorText>
      {empty && <EmptyState>No results for “{submitted}”.</EmptyState>}
      {results && !empty && (
        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <h3 className="mb-2 text-sm font-semibold">Messages</h3>
            <ul className="space-y-2 text-sm">
              {results.messages.map((m) => (
                <li key={m.id}>
                  <Link className="text-amber-900 hover:underline" to={`/projects/${projectId}/discussions`}>
                    {m.snippet}
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <h3 className="mb-2 text-sm font-semibold">Tasks</h3>
            <ul className="space-y-2 text-sm">
              {results.tasks.map((t) => (
                <li key={t.id}>
                  <Link className="text-amber-900 hover:underline" to={`/projects/${projectId}/tasks`}>
                    {t.title}
                  </Link>{" "}
                  <span className="text-xs text-stone-500">({t.status})</span>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <h3 className="mb-2 text-sm font-semibold">Decisions</h3>
            <ul className="space-y-2 text-sm">
              {results.decisions.map((d) => (
                <li key={d.id}>
                  <Link className="text-amber-900 hover:underline" to={`/projects/${projectId}/decisions`}>
                    {d.title}
                  </Link>{" "}
                  <span className="text-xs text-stone-500">({d.status})</span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}
