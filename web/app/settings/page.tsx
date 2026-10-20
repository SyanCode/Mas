"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Shell, useSession } from "@/components/Shell";
import { Badge, Button, Card, ErrorText, Input, Label, Notice, PageHeader, Select, Spinner } from "@/components/ui";
import { api, ApiError, type DnsStatus, type GitHubStatus, type ProviderInfo } from "@/lib/api";

function PasswordForm() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg("");
    setError("");
    if (next !== confirm) return setError("The two passwords differ.");
    try {
      await api("/account/password", { method: "POST", body: { current, new: next } });
      setCurrent(""); setNext(""); setConfirm("");
      setMsg("Password changed. Other sessions were signed out.");
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  return (
    <Card title="Password">
      <form onSubmit={submit} className="max-w-sm space-y-3 text-sm">
        <Input type="password" placeholder="Current password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        <Input type="password" placeholder="New password (10+ characters)" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={10} required />
        <Input type="password" placeholder="Confirm new password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required />
        <ErrorText>{error}</ErrorText>
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <Button variant="primary" type="submit">Change password</Button>
      </form>
    </Card>
  );
}

interface Setup { secret: string; uri: string; qr_svg: string }

function TwoFactor({ enabled }: { enabled: boolean }) {
  const [step, setStep] = useState<"idle" | "password" | "scan" | "disable">("idle");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState<Setup | null>(null);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  function reset() {
    setStep("idle"); setPassword(""); setCode(""); setSetup(null); setError("");
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
      await api("/account/totp/enable", { method: "POST", body: { code } });
      reset();
      setDone("Two-factor authentication is on. You will be asked for a code at each sign-in.");
      setTimeout(() => window.location.reload(), 1500);
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
      placeholder="6-digit code"
      className="mono tracking-[0.3em]"
      required
    />
  );

  return (
    <Card title="Two-factor authentication" actions={enabled ? <Badge tone="emerald">on</Badge> : <Badge tone="amber">off</Badge>}>
      <div className="max-w-md space-y-3 text-sm">
        {done && <p className="text-emerald-700 dark:text-emerald-400">{done}</p>}

        {step === "idle" && (
          <>
            <p className="text-zinc-600 dark:text-zinc-400">
              {enabled
                ? "Sign-in requires a code from your authenticator app. Lost your phone? Run `sudo mas user 2fa-reset` on the server."
                : "Protect the dashboard with a 6-digit code from Google Authenticator, Aegis, 1Password, Bitwarden…"}
            </p>
            {enabled ? (
              <div className="flex gap-2">
                <Button onClick={() => setStep("password")}>Replace authenticator</Button>
                <Button variant="danger" onClick={() => setStep("disable")}>Disable</Button>
              </div>
            ) : (
              <Button variant="primary" onClick={() => setStep("password")}>Set up 2FA</Button>
            )}
          </>
        )}

        {step === "password" && (
          <form onSubmit={start} className="space-y-3">
            <p>{enabled ? "Confirm with your password and the current code of your authenticator." : "Confirm your password to continue."}</p>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus required />
            {enabled && codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="primary" type="submit">Continue</Button><Button type="button" onClick={reset}>Cancel</Button></div>
          </form>
        )}

        {step === "scan" && setup && (
          <form onSubmit={enable} className="space-y-3">
            <p>1. Scan this QR code with your authenticator app.</p>
            <div className="inline-block rounded-md bg-white p-2" dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
            <p className="text-xs text-zinc-500">
              Or type this key: <code className="select-all break-all">{setup.secret.match(/.{1,4}/g)?.join(" ")}</code>
            </p>
            <p>2. Enter the code it shows.</p>
            {codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="primary" type="submit">Enable</Button><Button type="button" onClick={reset}>Cancel</Button></div>
          </form>
        )}

        {step === "disable" && (
          <form onSubmit={disable} className="space-y-3">
            <p>Confirm with your password and a current code.</p>
            <Input type="password" placeholder="Password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
            {codeInput}
            <ErrorText>{error}</ErrorText>
            <div className="flex gap-2"><Button variant="danger" type="submit">Disable 2FA</Button><Button type="button" onClick={reset}>Cancel</Button></div>
          </form>
        )}
      </div>
    </Card>
  );
}

function GitHubCard() {
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [org, setOrg] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const load = () => api<GitHubStatus>("/github/status").then(setStatus).catch((e: ApiError) => setError(e.detail));
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (code && state) {
      // Back from GitHub after creating the App (manifest flow).
      window.history.replaceState(null, "", "/settings/");
      api<{ slug: string }>("/github/manifest/complete", { method: "POST", body: { code, state } })
        .then((r) => setMsg(`GitHub App “${r.slug}” created. Last step: install it on your repositories.`))
        .catch((e: ApiError) => setError(e.detail))
        .finally(load);
    } else {
      load();
    }
  }, []);

  async function create() {
    setError("");
    try {
      const r = await api<{ action: string; manifest: string }>("/github/manifest", { method: "POST", body: { org: org || null } });
      const form = document.createElement("form");
      form.method = "post";
      form.action = r.action;
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "manifest";
      input.value = r.manifest;
      form.appendChild(input);
      document.body.appendChild(form);
      form.submit();
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }

  return (
    <Card title="GitHub" actions={status?.configured ? <Badge tone="emerald">connected</Badge> : <Badge>not connected</Badge>}>
      <div className="max-w-xl space-y-3 text-sm">
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <ErrorText>{error}</ErrorText>
        {status && !status.configured && (
          <>
            <p className="text-zinc-600 dark:text-zinc-400">
              Mas deploys from GitHub through a private GitHub App (read-only access to the repositories you choose, push
              webhooks). One click creates it on your account; GitHub sends its keys back to Mas.
            </p>
            <label className="block space-y-1">
              <span className="text-zinc-500">Organization (optional, empty = your personal account)</span>
              <Input value={org} onChange={(e) => setOrg(e.target.value.trim())} placeholder="my-org" className="max-w-xs" />
            </label>
            <Button variant="primary" onClick={create}>Create GitHub App</Button>
          </>
        )}
        {status?.configured && (
          <>
            <p>
              App <code>{status.slug}</code> (id {status.app_id}).{" "}
              {status.installations.length
                ? <>Installed on {status.installations.map((i) => i.account).join(", ")}.</>
                : <span className="text-amber-700 dark:text-amber-400">Not installed on any account yet.</span>}
            </p>
            {status.install_url && (
              <a href={status.install_url} target="_blank" rel="noreferrer" className="inline-block rounded-md border border-zinc-300 px-3 py-1.5 font-medium hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800">
                Install / choose repositories ↗
              </a>
            )}
            <p className="text-xs text-zinc-500">Webhook URL: <code>{status.webhook_url}</code></p>
            {status.error && <ErrorText>{status.error}</ErrorText>}
          </>
        )}
      </div>
    </Card>
  );
}

function DomainsCard() {
  const [status, setStatus] = useState<DnsStatus | null>(null);
  const [adding, setAdding] = useState<ProviderInfo | null>(null);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [ipv4, setIpv4] = useState("");
  const [ipv6, setIpv6] = useState("");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api<DnsStatus>("/dns/status").then((s) => {
    setStatus(s);
    setIpv4(s.ipv4 ?? s.suggested_ipv4 ?? "");
    setIpv6(s.ipv6 ?? "");
  }).catch((e: ApiError) => setError(e.detail));
  useEffect(() => { load(); }, []);

  function start(p: ProviderInfo) {
    setAdding(p);
    setError("");
    setMsg("");
    setCreds(Object.fromEntries(p.fields.filter((f) => f.options.length).map((f) => [f.name, f.options[0]])));
  }

  async function connect(e: FormEvent) {
    e.preventDefault();
    if (!adding) return;
    setBusy(true);
    setError("");
    try {
      const r = await api<{ account: string; zones: string[] }>("/dns/providers", { method: "POST", body: { type: adding.type, credentials: creds } });
      setMsg(`${adding.label} connected (${r.account}): ${r.zones.length} domain(s).`);
      setAdding(null);
      setCreds({});
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  async function saveIps(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/dns/settings", { method: "PUT", body: { ipv4: ipv4 || null, ipv6: ipv6 || null } });
      setMsg("Saved: new DNS records will point to this address.");
      load();
    } catch (err) {
      setError((err as ApiError).detail);
    }
  }

  async function disconnect(type: string) {
    await api(`/dns/providers/${type}`, { method: "DELETE" }).catch(() => {});
    load();
  }

  const connected = new Set(status?.providers.map((p) => p.type));
  const tokenUrl = adding ? adding.token_urls[creds.endpoint ?? "default"] ?? Object.values(adding.token_urls)[0] : null;

  return (
    <Card title="Domains" actions={<Badge tone={status?.providers.length ? "emerald" : "zinc"}>{status?.providers.length ?? 0} registrar(s)</Badge>}>
      <div className="space-y-6 text-sm">
        <Notice>{msg}</Notice>
        <ErrorText>{error}</ErrorText>

        <form onSubmit={saveIps} className="space-y-2">
          <div className="font-medium">This server&apos;s public address</div>
          <p className="text-xs text-zinc-500">
            New domains point here.{" "}
            {status && !status.ipv4 && status.suggested_ipv4 && <>Detected from your existing domains: <code>{status.suggested_ipv4}</code>.</>}
          </p>
          <div className="flex flex-wrap gap-2">
            <Input value={ipv4} onChange={(e) => setIpv4(e.target.value.trim())} placeholder="IPv4" className="mono max-w-[13rem]" />
            <Input value={ipv6} onChange={(e) => setIpv6(e.target.value.trim())} placeholder="IPv6 (optional)" className="mono max-w-[17rem]" />
            <Button type="submit">Save</Button>
          </div>
        </form>

        {status && status.providers.length > 0 && (
          <div className="space-y-2">
            <div className="font-medium">Connected</div>
            {status.providers.map((p) => (
              <div key={p.type} className="flex items-center justify-between rounded-xl border border-zinc-200 px-3 py-2 dark:border-white/[0.08]">
                <span className="flex items-center gap-2"><span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]" />{p.label} <span className="text-xs text-zinc-500">{p.account}</span></span>
                <Button variant="ghost" onClick={() => disconnect(p.type)}>Disconnect</Button>
              </div>
            ))}
          </div>
        )}

        <div className="space-y-2">
          <div className="font-medium">Connect a registrar</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {status?.catalog.map((p) => (
              <button
                key={p.type}
                type="button"
                onClick={() => start(p)}
                className={`glass rounded-xl px-3 py-3 text-left transition hover:border-indigo-400/50 ${adding?.type === p.type ? "ring-2 ring-indigo-500/50" : ""}`}
              >
                <div className="font-medium">{p.label}</div>
                <div className="text-xs text-zinc-500">{connected.has(p.type) ? "connected · replace keys" : "API token"}</div>
              </button>
            ))}
          </div>
        </div>

        {adding && (
          <form onSubmit={connect} className="space-y-3 rounded-xl border border-indigo-400/30 bg-indigo-500/[0.04] p-4">
            <div className="font-medium">{adding.label}</div>
            <p className="text-xs text-zinc-500">
              {adding.help}{" "}
              {tokenUrl && <a href={tokenUrl} target="_blank" rel="noreferrer" className="font-medium text-indigo-500 hover:underline">Open the token page ↗</a>}
            </p>
            {adding.fields.map((f) => (
              <Label key={f.name} title={f.label}>
                {f.options.length ? (
                  <Select value={creds[f.name] ?? ""} onChange={(e) => setCreds({ ...creds, [f.name]: e.target.value })}>
                    {f.options.map((o) => <option key={o}>{o}</option>)}
                  </Select>
                ) : (
                  <Input type={f.secret ? "password" : "text"} value={creds[f.name] ?? ""} onChange={(e) => setCreds({ ...creds, [f.name]: e.target.value.trim() })} required autoComplete="off" />
                )}
              </Label>
            ))}
            <div className="flex gap-2">
              <Button variant="glow" type="submit" disabled={busy}>{busy ? <Spinner /> : null}Verify &amp; connect</Button>
              <Button type="button" variant="ghost" onClick={() => setAdding(null)}>Cancel</Button>
            </div>
            <p className="text-xs text-zinc-500">Keys are checked with {adding.label}, then stored encrypted on this server. Mas never deletes DNS records.</p>
          </form>
        )}
      </div>
    </Card>
  );
}

function Settings() {
  const session = useSession();
  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Configuration" title="Settings" subtitle="Domains, GitHub and account security." />
      <DomainsCard />
      <GitHubCard />
      <TwoFactor enabled={!!session?.totp_enabled} />
      <PasswordForm />
      <p className="text-xs text-zinc-500">Mas {session?.version} · signed in as {session?.username}</p>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Shell>
      <Settings />
    </Shell>
  );
}
