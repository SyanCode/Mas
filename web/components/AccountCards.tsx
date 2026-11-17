"use client";

// The account of whoever is signed in: profile, appearance, two-factor and password.
// Shown in the server settings for administrators and in the CMS for everyone else.
import { useEffect, useState, type FormEvent } from "react";
import { Avatar, myAvatarUrl, shrinkPicture } from "@/components/Avatar";
import { useSession } from "@/components/Shell";
import { ModePicker, ThemePicker } from "@/components/ThemePicker";
import { Badge, Button, Card, ErrorText, Input, Label, Spinner, Textarea } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";


export function PasswordForm() {
  const t = useT();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg("");
    setError("");
    if (next !== confirm) return setError(t("The two passwords differ."));
    try {
      await api("/account/password", { method: "POST", body: { current, new: next } });
      setCurrent(""); setNext(""); setConfirm("");
      setMsg(t("Password changed. Other sessions were signed out."));
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Card title={t("Password")}>
      <form onSubmit={submit} className="max-w-sm space-y-3 text-sm">
        <Input type="password" placeholder={t("Current password")} value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        <Input type="password" placeholder={t("New password (10+ characters)")} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} required />
        <Input type="password" placeholder={t("Confirm new password")} value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
        <ErrorText>{error}</ErrorText>
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <Button variant="primary" type="submit">{t("Change password")}</Button>
      </form>
    </Card>
  );
}

interface Setup { secret: string; uri: string; qr_svg: string }

export function TwoFactor({ enabled }: { enabled: boolean }) {
  const t = useT();
  const [step, setStep] = useState<"idle" | "password" | "scan" | "disable" | "regen" | "codes">("idle");
  const [codes, setCodes] = useState<string[]>([]);
  const [remaining, setRemaining] = useState<number | null>(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  function reset() {
    setStep("idle"); setPassword(""); setCode(""); setSetup(null); setError("");
  }

  useEffect(() => {
    if (enabled) api<{ remaining: number }>("/account/recovery").then((r) => setRemaining(r.remaining)).catch(() => {});
  }, [enabled]);

  async function regenerate(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const r = await api<{ recovery_codes: string[] }>("/account/recovery", { method: "POST", body: { password, code } });
      reset();
      setCodes(r.recovery_codes);
      setStep("codes");
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function start(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      setSetup(await api<Setup>("/account/totp/setup", { method: "POST", body: { password, code: enabled ? code : undefined } }));
      setCode("");
      setStep("scan");
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function enable(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      const r = await api<{ recovery_codes: string[] }>("/account/totp/enable", { method: "POST", body: { code } });
      reset();
      setDone(t("Two-factor authentication is on. You will be asked for a code at each sign-in."));
      setCodes(r.recovery_codes);
      setStep("codes");
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function disable(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/account/totp/disable", { method: "POST", body: { password, code } });
      reset();
      window.location.reload();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  const codeInput = (
    <Input
      value={code}
      onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
      inputMode="numeric"
      autoComplete="one-time-code"
      placeholder={t("6-digit code")}
      className="mono tracking-[0.3em]"
      required
    />
  );

  return (
    <Card title={t("Two-factor authentication")} actions={enabled ? <Badge tone="emerald">{t("on")}</Badge> : <Badge tone="amber">{t("off")}</Badge>}>
      <div className="max-w-md space-y-3 text-sm">
        {done && <p className="text-emerald-700 dark:text-emerald-400">{done}</p>}

        {step === "idle" && (
          <>
            <p className="text-zinc-600 dark:text-zinc-400">
              {enabled
                ? t("Sign-in requires a code from your authenticator app. Lost your phone? Run `sudo mas user 2fa-reset` on the server.")
                : t("Protect the dashboard with a 6-digit code from Google Authenticator, Aegis, 1Password, Bitwarden…")}
            </p>
            {enabled && remaining !== null && <p className="text-xs text-zinc-500">{t("{n} recovery codes left", { n: remaining })}</p>}
            {enabled ? (
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => setStep("password")}>{t("Replace authenticator")}</Button>
                <Button onClick={() => setStep("regen")}>{t("New recovery codes")}</Button>
                <Button variant="danger" onClick={() => setStep("disable")}>{t("Disable")}</Button>
              </div>
            ) : (
              <Button variant="primary" onClick={() => setStep("password")}>{t("Set up 2FA")}</Button>
            )}
          </>
        )}

        {step === "password" && (
          <form onSubmit={start} className="space-y-3">
            <p>{enabled ? t("Confirm with your password and the current code of your authenticator.") : t("Confirm your password to continue.")}</p>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus required />
            {enabled && codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="primary" type="submit">{t("Continue")}</Button><Button type="button" onClick={reset}>{t("Cancel")}</Button></div>
          </form>
        )}

        {step === "scan" && setup && (
          <form onSubmit={enable} className="space-y-3">
            <p>1. {t("Scan this QR code with your authenticator app.")}</p>
            <div className="inline-block rounded-md bg-white p-2" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
            <p className="text-xs text-zinc-500">
              {t("Or type this key:")} <code className="select-all break-all">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
            </p>
            <p>2. {t("Enter the code it shows.")}</p>
            {codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="primary" type="submit">{t("Enable")}</Button><Button type="button" onClick={reset}>{t("Cancel")}</Button></div>
          </form>
        )}

        {step === "codes" && (
          <div className="space-y-3">
            <p>{t("Save these recovery codes. Each works once if you lose your phone; they are not shown again.")}</p>
            <pre className="select-all rounded-md bg-zinc-100 p-3 font-mono dark:bg-zinc-800">{codes.join("\n")}</pre>
            <Button variant="primary" onClick={() => window.location.reload()}>{t("I saved them")}</Button>
          </div>
        )}

        {step === "regen" && (
          <form onSubmit={regenerate} className="space-y-3">
            <p>{t("Confirm with your password and a current code.")}</p>
            <Input type="password" placeholder={t("Password")} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            {codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="primary" type="submit">{t("New recovery codes")}</Button><Button type="button" onClick={reset}>{t("Cancel")}</Button></div>
          </form>
        )}

        {step === "disable" && (
          <form onSubmit={disable} className="space-y-3">
            <p>{t("Confirm with your password and a current code.")}</p>
            <Input type="password" placeholder={t("Password")} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            {codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="danger" type="submit">{t("Disable 2FA")}</Button><Button type="button" onClick={reset}>{t("Cancel")}</Button></div>
          </form>
        )}
      </div>
    </Card>
  );
}

export function ProfileCard() {
  const t = useT();
  const session = useSession();
  const [displayName, setDisplayName] = useState("");
  const [nickname, setNickname] = useState("");
  const [bio, setBio] = useState("");
  const [picture, setPicture] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!session) return;
    setDisplayName(session.display_name ?? ""); setNickname(session.nickname ?? ""); setBio(session.bio ?? "");
    setPicture(myAvatarUrl(session));
  }, [session]);

  type Profile = { has_avatar: boolean; avatar_v: number };
  const avatarOf = (r: Profile) => (r.has_avatar ? `/api/account/avatar?v=${r.avatar_v}` : null);

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(""); setMsg(""); setBusy(true);
    try {
      await api("/account/profile", { method: "PUT", body: { display_name: displayName, nickname, bio } });
      setMsg(t("Saved."));
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  async function choose(file: File | undefined) {
    if (!file) return;
    setError(""); setMsg(""); setBusy(true);
    try {
      const data_url = await shrinkPicture(file);
      const r = await api<Profile>("/account/avatar", { method: "PUT", body: { data_url } });
      setPicture(avatarOf(r));
      setTimeout(() => window.location.reload(), 600);
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : t("This file is not a picture the browser can read."));
    } finally {
      setBusy(false);
    }
  }

  async function removePicture() {
    setError(""); setBusy(true);
    try {
      await api("/account/avatar", { method: "DELETE" });
      setPicture(null);
      setTimeout(() => window.location.reload(), 300);
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  const shown = nickname || displayName || session?.username || "";
  return (
    <Card title={t("Profile")}>
      <form onSubmit={save} className="max-w-md space-y-4 text-sm">
        <div className="flex items-center gap-4">
          <Avatar name={shown || "?"} src={picture} size={72} />
          <div className="space-y-2">
            <div className="flex flex-wrap gap-2">
              <label className="inline-flex cursor-pointer items-center rounded-lg border border-zinc-300 px-3 py-1.5 hover:bg-zinc-100 dark:border-white/10 dark:hover:bg-white/[0.06]">
                {t("Choose a picture")}
                <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" disabled={busy} onChange={(e) => { choose(e.target.files?.[0]); e.target.value = ""; }} />
              </label>
              {picture && <Button type="button" variant="ghost" disabled={busy} onClick={removePicture}>{t("Remove")}</Button>}
            </div>
            <p className="text-xs text-zinc-500">{t("PNG, JPEG or WebP. It is cropped to a square and shrunk here, in your browser.")}</p>
          </div>
        </div>
        <Label title={t("Full name")}>
          <Input value={displayName} maxLength={80} onChange={(e) => setDisplayName(e.target.value)} placeholder={t("Jane Doe")} autoComplete="name" />
        </Label>
        <Label title={t("Nickname")} hint={t("Shown first, in the menu and in lists. Letters, digits, spaces and . _ ' -")}>
          <Input value={nickname} maxLength={32} onChange={(e) => setNickname(e.target.value)} placeholder={session?.username} autoComplete="nickname" />
        </Label>
        <Label title={t("About")} hint={t("A few words about you (300 characters).")}>
          <Textarea rows={3} value={bio} maxLength={300} onChange={(e) => setBio(e.target.value)} />
        </Label>
        <p className="text-xs text-zinc-500">{t("Your login stays {name}.", { name: session?.username ?? "" })}</p>
        <ErrorText>{error}</ErrorText>
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <Button variant="primary" type="submit" disabled={busy}>{busy ? <Spinner /> : null}{t("Save")}</Button>
      </form>
    </Card>
  );
}

export function AppearanceCard() {
  const t = useT();
  return (
    <Card title={t("Appearance")} actions={<div className="w-56"><ModePicker /></div>}>
      <p className="mb-4 text-sm text-zinc-500">{t("Each theme has its colors and its own layout of the apps and services. The choice is kept in this browser.")}</p>
      <ThemePicker size="full" />
    </Card>
  );
}
