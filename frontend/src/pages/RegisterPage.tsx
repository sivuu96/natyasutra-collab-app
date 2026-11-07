import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { Button, Card, ErrorText, Input, Label, errorMessage } from "../components/ui";

const MIN_PASSWORD = 12; // must match the backend (MIN_PASSWORD_LENGTH)

export function RegisterPage() {
  const { user, register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ displayName: "", email: "", password: "" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(form.email.trim(), form.displayName.trim(), form.password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center p-4">
      <h1 className="mb-6 text-2xl font-semibold text-amber-900">Create your account</h1>
      <Card>
        <form onSubmit={submit} className="space-y-4">
          <ErrorText>{error}</ErrorText>
          <div>
            <Label htmlFor="reg-name">Display name</Label>
            <Input id="reg-name" required maxLength={120} autoComplete="name" value={form.displayName}
              onChange={(e) => setForm({ ...form, displayName: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="reg-email">Email</Label>
            <Input id="reg-email" type="email" required autoComplete="email" value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="reg-password">Password</Label>
            <Input id="reg-password" type="password" required minLength={MIN_PASSWORD} autoComplete="new-password"
              value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            <p className="mt-1 text-xs text-stone-500">At least {MIN_PASSWORD} characters.</p>
          </div>
          <Button type="submit" className="w-full" disabled={busy}>
            {busy ? "Creating account…" : "Create account"}
          </Button>
        </form>
      </Card>
      <p className="mt-4 text-center text-sm text-stone-600">
        Already registered? <Link to="/login" className="text-amber-800 underline">Sign in</Link>
      </p>
    </main>
  );
}
