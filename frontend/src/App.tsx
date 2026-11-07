import type { ReactNode } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Button } from "./components/ui";
import { ActivityView } from "./pages/ActivityView";
import { AgentsView } from "./pages/AgentsView";
import { DecisionsView } from "./pages/DecisionsView";
import { DevicesPage } from "./pages/DevicesPage";
import { DiscussionsView } from "./pages/DiscussionsView";
import { LoginPage } from "./pages/LoginPage";
import { PeopleView } from "./pages/PeopleView";
import { SessionsView } from "./pages/SessionsView";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProjectWorkspace } from "./pages/ProjectWorkspace";
import { RegisterPage } from "./pages/RegisterPage";
import { SearchView } from "./pages/SearchView";
import { TasksView } from "./pages/TasksView";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <p className="p-8 text-sm text-stone-500">Loading…</p>;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

function Shell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-stone-200 bg-white px-4 py-3">
        <Link to="/" className="font-semibold tracking-tight text-amber-900">
          NatyaSutra Hub
        </Link>
        {user && (
          <div className="flex items-center gap-3 text-sm">
            <Link to="/devices" className="text-amber-800 hover:underline">
              Devices
            </Link>
            <span className="hidden text-stone-600 sm:inline">{user.display_name}</span>
            <Button variant="secondary" onClick={() => void logout()}>
              Sign out
            </Button>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-7xl p-4">{children}</main>
    </div>
  );
}

function ProjectsRoute() {
  return (
    <RequireAuth>
      <Shell>
        <ProjectsPage />
      </Shell>
    </RequireAuth>
  );
}

function WorkspaceRoute() {
  return (
    <RequireAuth>
      <Shell>
        <ProjectWorkspace />
      </Shell>
    </RequireAuth>
  );
}

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/" element={<ProjectsRoute />} />
          <Route
            path="/devices"
            element={
              <RequireAuth>
                <Shell>
                  <DevicesPage />
                </Shell>
              </RequireAuth>
            }
          />
          <Route path="/projects/:projectId" element={<WorkspaceRoute />}>
            <Route index element={<Navigate to="discussions" replace />} />
            <Route path="discussions" element={<DiscussionsView />} />
            <Route path="tasks" element={<TasksView />} />
            <Route path="decisions" element={<DecisionsView />} />
            <Route path="agents" element={<AgentsView />} />
            <Route path="sessions" element={<SessionsView />} />
            <Route path="activity" element={<ActivityView />} />
            <Route path="people" element={<PeopleView />} />
            <Route path="search" element={<SearchView />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
