"use client";

import { useState, type FormEvent } from "react";
import { Button, ErrorText, Input } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

const MESSAGES: Record<string, string> = {
  invalid: "Wrong username or password.",
  otp_invalid: "Wrong or already used code. Wait for the next one.",
};

function safeNext(): string {
  const next = new URLSearchParams(window.location.search).get("next") || "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

export default function LoginPage() {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [otp, setOtp] = useState("");
  const [needOtp, setNeedOtp] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/login", { method: "POST", body: { username, password, otp: needOtp ? otp : undefined } });
      window.location.href = safeNext();
    } catch (err) {
      const detail = (err as ApiError).detail;
      if (detail === "otp_required") {
        setNeedOtp(true);
      } else {
        setError(MESSAGES[detail] ?? detail);
        if (detail === "otp_invalid") setOtp("");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-zinc-200 bg-white p-6 shadow-sm dark:border-zinc-800 dark:bg-zinc-900">
        <div>
          <h1 className="text-lg font-semibold">mas</h1>
          <p className="text-sm text-zinc-500">{needOtp ? "Enter the code from your authenticator app." : "Sign in to manage this server."}</p>
        </div>
        {!needOtp ? (
          <>
            <label className="block space-y-1 text-sm">
              <span>Username</span>
              <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
            </label>
            <label className="block space-y-1 text-sm">
              <span>Password</span>
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus required />
            </label>
          </>
        ) : (
          <label className="block space-y-1 text-sm">
            <span>6-digit code</span>
            <Input
              autoFocus
              value={otp}
              onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              placeholder="123456"
              className="mono text-center text-lg tracking-[0.4em]"
              required
            />
          </label>
        )}
        <ErrorText>{error}</ErrorText>
        <Button variant="primary" type="submit" disabled={busy} className="w-full">
          {busy ? "…" : needOtp ? "Verify" : "Sign in"}
        </Button>
        {needOtp && (
          <button type="button" className="w-full text-xs text-zinc-500 hover:underline" onClick={() => { setNeedOtp(false); setOtp(""); setError(""); }}>
            Back
          </button>
        )}
      </form>
    </main>
  );
}
