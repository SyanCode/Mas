"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { Backdrop, Logo } from "@/components/Shell";
import { Button, ErrorText, Input, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { LanguagePicker, useT } from "@/lib/i18n";

export default function SignupPage() {
  const t = useT();
  const [info, setInfo] = useState<{ open: boolean; code_required: boolean; invite: boolean | null } | null>(null);
  const [invite, setInvite] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    const legacyToken = new URLSearchParams(window.location.search).get("invite");
    const token = params.get("invite") || legacyToken;
    if (legacyToken) {
      // Keep old invitation links working, but remove the bearer token from
      // the request URL and browser history before making any API call.
      window.history.replaceState(null, "", `${window.location.pathname}#invite=${encodeURIComponent(token || "")}`);
    }
    setInvite(token);
    api<{ open: boolean; code_required: boolean; invite: boolean | null }>("/signup")
      .then(setInfo).catch(() => setInfo({ open: false, code_required: false, invite: null }));
  }, []);
  const invited = !!invite;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) {
      setError(t("The two passwords are different."));
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api("/signup", {
        method: "POST",
        body: { username, password, invite: invited ? invite : undefined, code: !invited && info?.code_required ? code : undefined },
      });
      window.location.href = "/cms/";
    } catch (err) {
      setError((err as ApiError).detail);
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
            <h1 className="text-xl font-semibold tracking-tight">{t("Create your account")}</h1>
            <p className="mt-1 text-sm text-zinc-500">{t("To edit the content of your website.")}</p>
          </div>
        </div>

        {info === null ? (
          <div className="flex justify-center"><Spinner /></div>
        ) : invite && !invited ? (
          <p className="text-center text-sm text-zinc-500">{t("This invitation link was already used or has expired. Ask your administrator for a new one.")}</p>
        ) : !invited && !info.open ? (
          <p className="text-center text-sm text-zinc-500">{t("Sign-up is closed. Ask the administrator of this server for an account.")}</p>
        ) : (
          <>
            <div className="space-y-3">
              <Input value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())} autoComplete="username" placeholder={t("Username (lowercase)")} autoFocus required />
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" placeholder={t("Password (10 characters or more)")} required />
              <Input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" placeholder={t("Password again")} required />
              {!invited && info.code_required && (
                <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder={t("Invitation code")} autoComplete="off" required />
              )}
            </div>
            <ErrorText>{error}</ErrorText>
            <Button variant="glow" type="submit" disabled={busy} className="!h-10 w-full text-sm">
              {busy ? <Spinner /> : null}{t("Create my account")}
            </Button>
            <p className="text-center text-xs text-zinc-500">
              {invited ? t("You were invited: your site is ready as soon as the account is created.") : t("Your administrator then gives you access to your site.")}
            </p>
          </>
        )}
        <p className="text-center text-xs text-zinc-500">
          {t("Already have an account?")} <Link href="/login/" className="font-medium text-indigo-500 hover:underline">{t("Sign in")}</Link>
        </p>
        <div className="flex justify-center"><LanguagePicker /></div>
      </form>
    </main>
  );
}
