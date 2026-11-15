import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { Button, Card, ErrorText, Input, Label, errorMessage } from "../components/ui";

const MIN_LENGTH = 12;

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (password.length < MIN_LENGTH) {
      setError(`The new password must be at least ${MIN_LENGTH} characters.`);
      return;
    }
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      await api<void>("/auth/reset-password", { method: "POST", json: { token, password } });
      setDone(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center p-4">
      <h1 className="mb-1 text-2xl font-semibold text-amber-900">Choose a new password</h1>
      <Card>
        {!token ? (
          <p className="text-sm text-red-700">This link is incomplete. Request a new reset link.</p>
        ) : done ? (
          <p className="text-sm text-stone-700">
            Your password has been changed. Every other sign-in has ended.{" "}
            <Link to="/login" className="text-amber-800 underline">Sign in</Link>
          </p>
        ) : (
          <form onSubmit={submit} className="space-y-4">
            <ErrorText>{error}</ErrorText>
            <div>
              <Label htmlFor="reset-password">New password</Label>
              <Input id="reset-password" type="password" autoComplete="new-password" required value={password}
                onChange={(e) => setPassword(e.target.value)} />
            </div>
            <div>
              <Label htmlFor="reset-confirm">Confirm new password</Label>
              <Input id="reset-confirm" type="password" autoComplete="new-password" required value={confirm}
                onChange={(e) => setConfirm(e.target.value)} />
            </div>
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Saving…" : "Set new password"}
            </Button>
          </form>
        )}
      </Card>
      {token && !done && (
        <p className="mt-4 text-center text-sm text-stone-600">
          The link expires 30 minutes after it was sent. <Link to="/forgot-password" className="text-amber-800 underline">Request a new one</Link>
        </p>
      )}
    </main>
  );
}
