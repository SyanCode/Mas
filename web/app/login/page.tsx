"use client";

import { useState, type FormEvent } from "react";
import { Backdrop, Logo } from "@/components/Shell";
import { Button, ErrorText, Input, Spinner } from "@/components/ui";
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
    <main className="relative flex min-h-screen items-center justify-center overflow-hidden p-4">
      <Backdrop />
      <div className="pointer-events-none absolute left-1/2 top-1/2 h-[520px] w-[520px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-gradient-to-tr from-indigo-600/30 via-sky-500/20 to-fuchsia-500/30 blur-3xl" />
      <form onSubmit={submit} className="glass ring-gradient relative w-full max-w-sm animate-fade-up space-y-5 rounded-3xl p-8 shadow-[0_40px_120px_-30px_rgba(99,102,241,0.6)] dark:bg-zinc-950/60">
        <div className="flex flex-col items-center gap-3 text-center">
          <Logo className="h-12 w-12" />
          <div>
            <h1 className="text-xl font-semibold tracking-tight">{needOtp ? "Two-factor check" : <>Welcome to <span className="text-gradient">mas</span></>}</h1>
            <p className="mt-1 text-sm text-zinc-500">{needOtp ? "Enter the 6-digit code from your authenticator app." : "Your server, mapped and deployed."}</p>
          </div>
        </div>

        {!needOtp ? (
          <div className="space-y-3">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" placeholder="Username" required />
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" placeholder="Password" autoFocus required />
          </div>
        ) : (
          <Input
            autoFocus
            value={otp}
            onChange={(e) => setOtp(e.target.value.replace(/\D/g, "").slice(0, 6))}
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            placeholder="••••••"
            className="mono h-14 text-center text-2xl tracking-[0.6em]"
            required
          />
        )}
        <ErrorText>{error}</ErrorText>
        <Button variant="glow" type="submit" disabled={busy} className="!h-10 w-full text-sm">
          {busy ? <Spinner /> : null}{needOtp ? "Verify" : "Sign in"}
        </Button>
        {needOtp && (
          <button type="button" className="w-full text-xs text-zinc-500 hover:underline" onClick={() => { setNeedOtp(false); setOtp(""); setError(""); }}>
            ← Back
          </button>
        )}
      </form>
    </main>
  );
}
