import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Button, Card, ErrorText, Input, Label, errorMessage } from "../components/ui";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api<{ detail: string }>("/auth/forgot-password", { method: "POST", json: { email: email.trim() } });
      setSent(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center p-4">
      <h1 className="mb-1 text-2xl font-semibold text-amber-900">Reset your password</h1>
      <p className="mb-6 text-sm text-stone-600">Enter your email and we'll send you a link to choose a new password.</p>
      <Card>
        {sent ? (
          <p className="text-sm text-stone-700">
            If that account exists, a reset link has been sent. The link works once and expires in 30 minutes.
          </p>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <ErrorText>{error}</ErrorText>
            <div>
              <Label htmlFor="forgot-email">Email</Label>
              <Input
                id="forgot-email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Sending…" : "Send reset link"}
            </Button>
          </form>
        )}
      </Card>
      <p className="mt-4 text-center text-sm text-stone-600">
        <Link to="/login" className="text-amber-800 underline">Back to sign in</Link>
      </p>
    </main>
  );
}
