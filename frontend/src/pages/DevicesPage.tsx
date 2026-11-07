import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { Device, PairingCode } from "../api/types";
import { Button, Card, EmptyState, ErrorText, formatTime, Input, Label, errorMessage } from "../components/ui";

export function DevicesPage() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [pairing, setPairing] = useState<PairingCode | null>(null);
  const [machineName, setMachineName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setDevices(await api<Device[]>("/devices"));
  }, []);

  useEffect(() => {
    load().catch((err: unknown) => setError(errorMessage(err)));
  }, [load]);

  async function createCode() {
    setError(null);
    try {
      setPairing(await api<PairingCode>("/devices/pairing-codes", { method: "POST" }));
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function revoke(device: Device) {
    if (!window.confirm(`Revoke “${device.name}”? Its agents go offline immediately and it cannot reconnect.`)) return;
    setError(null);
    try {
      await api<void>(`/devices/${device.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const hub = window.location.origin;
  const command = pairing
    ? `collab-bridge pair --hub ${hub} --code ${pairing.code} --name "${machineName.trim() || "My laptop"}"`
    : "";

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <section className="space-y-3">
        <h1 className="text-xl font-semibold">Connect a machine</h1>
        <p className="text-sm text-stone-600">
          Your agents run on your own computer, using your own Claude Code or Codex login. This hub only
          coordinates them. It never receives your provider credentials.
        </p>
        <ErrorText>{error}</ErrorText>
        <Card className="space-y-3">
          <div>
            <Label htmlFor="machine-name">Machine name</Label>
            <Input id="machine-name" maxLength={120} value={machineName} placeholder="e.g. Work laptop"
              onChange={(e) => setMachineName(e.target.value)} />
          </div>
          <Button onClick={() => void createCode()}>Create one-time pairing code</Button>
          {pairing && (
            <div className="space-y-2 rounded-md bg-amber-50 p-3">
              <p className="text-xs text-stone-600">
                Code (single use, expires {formatTime(pairing.expires_at)}):
              </p>
              <p className="font-mono text-2xl tracking-widest text-amber-900" data-testid="pairing-code">
                {pairing.code}
              </p>
              <p className="text-xs text-stone-600">Run this on the machine, from the bridge folder:</p>
              <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-white p-2 text-xs">{command}</pre>
              <p className="text-xs text-stone-500">
                Then add an agent with <code>collab-bridge agent add</code> and start it with <code>collab-bridge run</code>.
              </p>
            </div>
          )}
        </Card>
      </section>

      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Your machines</h2>
        <Card className="p-0">
          {devices.length === 0 ? (
            <EmptyState>No machines paired yet.</EmptyState>
          ) : (
            <ul className="divide-y divide-stone-100">
              {devices.map((device) => (
                <li key={device.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                  <div>
                    <p className="font-medium">{device.name}</p>
                    <p className="text-xs text-stone-500">
                      Paired {formatTime(device.created_at)}
                      {device.last_seen_at ? ` · last seen ${formatTime(device.last_seen_at)}` : ""}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {device.revoked_at ? (
                      <span className="rounded-full bg-red-50 px-2 py-0.5 text-xs text-red-800">revoked</span>
                    ) : (
                      <>
                        <span
                          className={`rounded-full px-2 py-0.5 text-xs ${device.online ? "bg-emerald-100 text-emerald-800" : "bg-stone-200 text-stone-700"}`}
                        >
                          {device.online ? "online" : "offline"}
                        </span>
                        <Button variant="ghost" onClick={() => void revoke(device)}>
                          Revoke
                        </Button>
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </section>
    </div>
  );
}
