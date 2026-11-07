import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { connectProject, type RealtimeEnvelope, type RealtimeStatus } from "../api/realtime";
import type { Project } from "../api/types";
import { ErrorText, errorMessage } from "../components/ui";
import { roleAtLeast, WorkspaceContext, type WorkspaceState } from "../project/workspaceContext";

const TABS = [
  { to: "discussions", label: "Discussions" },
  { to: "tasks", label: "Tasks" },
  { to: "decisions", label: "Decisions" },
  { to: "agents", label: "Agents" },
  { to: "sessions", label: "Sessions" },
  { to: "activity", label: "Activity" },
  { to: "people", label: "People" },
  { to: "search", label: "Search" },
];

const STATUS_LABEL: Record<RealtimeStatus, string> = {
  connecting: "Connecting…",
  live: "Live",
  offline: "Reconnecting…",
  stopped: "Disconnected",
};

export function ProjectWorkspace() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [status, setStatus] = useState<RealtimeStatus>("connecting");
  const listeners = useRef(new Set<(event: RealtimeEnvelope) => void>());

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    api<Project>(`/projects/${projectId}`)
      .then((loaded) => {
        if (!cancelled) setProject(loaded);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadError(error instanceof ApiError && error.status === 404 ? "Project not found." : errorMessage(error));
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // Connect once the project is known, resuming from the latest sequence the server reported.
  const sinceSequence = project?.latest_sequence ?? 0;
  useEffect(() => {
    if (!project) return;
    const close = connectProject({
      projectId,
      initialSince: sinceSequence,
      onStatus: setStatus,
      onEvent: (event) => listeners.current.forEach((listener) => listener(event)),
    });
    return close;
    // sinceSequence is captured once per project load; reconnects use the live cursor internally.
  }, [project?.id, projectId]);

  const value = useMemo<WorkspaceState | null>(() => {
    if (!project || !project.role) return null;
    const role = project.role;
    return {
      project,
      role,
      status,
      subscribe: (listener) => {
        listeners.current.add(listener);
        return () => {
          listeners.current.delete(listener);
        };
      },
      canWrite: roleAtLeast(role, "member"),
      isModerator: roleAtLeast(role, "admin"),
    };
  }, [project, status]);

  if (loadError) {
    return (
      <div className="space-y-3">
        <ErrorText>{loadError}</ErrorText>
        <NavLink to="/" className="text-sm text-amber-800 underline">
          Back to projects
        </NavLink>
      </div>
    );
  }
  if (!value) return <p className="text-sm text-stone-500">Loading project…</p>;

  return (
    <WorkspaceContext.Provider value={value}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <NavLink to="/" className="text-xs text-stone-500 hover:underline">
              ← Projects
            </NavLink>
            <h1 className="text-xl font-semibold text-stone-900">{project?.name}</h1>
          </div>
          <span
            className={`rounded-full px-2 py-1 text-xs ${status === "live" ? "bg-emerald-100 text-emerald-800" : "bg-stone-200 text-stone-700"}`}
            role="status"
            aria-live="polite"
          >
            {STATUS_LABEL[status]}
          </span>
        </div>
        <nav aria-label="Project sections" className="flex flex-wrap gap-1 border-b border-stone-200">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              className={({ isActive }) =>
                `-mb-px border-b-2 px-3 py-2 text-sm ${isActive ? "border-amber-700 font-medium text-amber-900" : "border-transparent text-stone-600 hover:text-stone-900"}`
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
        <Outlet />
      </div>
    </WorkspaceContext.Provider>
  );
}
