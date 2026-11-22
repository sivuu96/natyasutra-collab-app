import type { ReactNode } from "react";
import { BrowserRouter, Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Button } from "./components/ui";
import { ActivityView } from "./pages/ActivityView";
import { AgentsView } from "./pages/AgentsView";
import { DecisionsView } from "./pages/DecisionsView";
import { DiscussionsView } from "./pages/DiscussionsView";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { LoginPage } from "./pages/LoginPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
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
  const location = useLocation();
  return (
    <div className="min-h-screen">
      <header className="flex items-center justify-between border-b border-stone-200 bg-white px-4 py-3">
        <Link to="/" className="font-semibold tracking-tight text-amber-900">
          NatyaSutra Hub
        </Link>
        {user && (
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-stone-600 sm:inline">{user.display_name}</span>
            <Button variant="secondary" onClick={() => void logout()}>
              Sign out
            </Button>
          </div>
        )}
      </header>
      {/* The sessions tab uses the full width, so the conversation has room. Other pages keep the normal width. */}
      <main className={`mx-auto p-4 ${location.pathname.endsWith("/sessions") ? "max-w-none" : "max-w-7xl"}`}>
        {children}
      </main>
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
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/" element={<ProjectsRoute />} />
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
