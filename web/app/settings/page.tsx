"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Shell, useSession } from "@/components/Shell";
import { Badge, Button, Card, ErrorText, Input } from "@/components/ui";
import { api, ApiError, type DnsStatus, type GitHubStatus } from "@/lib/api";

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
      setSetup(await api<Setup>("/account/totp/setup", { method: "POST", body: { password } }));
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
            <p>Confirm your password to continue.</p>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus required />
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
  const [endpoint, setEndpoint] = useState("ovh-eu");
  const [ak, setAk] = useState("");
  const [as, setAs] = useState("");
  const [ck, setCk] = useState("");
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

  async function connect(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMsg("");
    try {
      const r = await api<{ zones: string[] }>("/dns/ovh", {
        method: "POST", body: { endpoint, application_key: ak, application_secret: as, consumer_key: ck },
      });
      setAk(""); setAs(""); setCk("");
      setMsg(`Connected: ${r.zones.length} domain(s) (${r.zones.join(", ")}).`);
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

  async function disconnect() {
    await api("/dns/ovh", { method: "DELETE" }).catch(() => {});
    setMsg("");
    load();
  }

  return (
    <Card title="Domains" actions={status?.provider ? <Badge tone="emerald">OVH connected</Badge> : <Badge>not connected</Badge>}>
      <div className="max-w-xl space-y-4 text-sm">
        {msg && <p className="text-emerald-700 dark:text-emerald-400">{msg}</p>}
        <ErrorText>{error}</ErrorText>

        <form onSubmit={saveIps} className="space-y-2">
          <p className="font-medium">This server&apos;s public address</p>
          <p className="text-xs text-zinc-500">
            New domains point here.{" "}
            {status && !status.ipv4 && status.suggested_ipv4 && <>Suggested from your existing domains: <code>{status.suggested_ipv4}</code>.</>}
          </p>
          <div className="flex flex-wrap gap-2">
            <Input value={ipv4} onChange={(e) => setIpv4(e.target.value.trim())} placeholder="IPv4, e.g. 93.184.216.34" className="max-w-[14rem]" />
            <Input value={ipv6} onChange={(e) => setIpv6(e.target.value.trim())} placeholder="IPv6 (optional)" className="max-w-[16rem]" />
            <Button type="submit">Save</Button>
          </div>
        </form>

        {status?.provider ? (
          <div className="space-y-2">
            <p>
              OVH account <code>{status.account?.nichandle}</code> ({status.endpoint}). Mas can create sub-domains in
              your zones and link them to apps.
            </p>
            <Button variant="danger" onClick={disconnect}>Disconnect</Button>
          </div>
        ) : (
          <form onSubmit={connect} className="space-y-2">
            <p className="font-medium">Connect OVH</p>
            <ol className="list-decimal space-y-1 pl-5 text-zinc-600 dark:text-zinc-400">
              <li>
                <select value={endpoint} onChange={(e) => setEndpoint(e.target.value)} className="mr-2 rounded border border-zinc-300 bg-transparent px-1 py-0.5 dark:border-zinc-700">
                  <option value="ovh-eu">OVH Europe</option>
                  <option value="ovh-ca">OVH Canada</option>
                  <option value="ovh-us">OVH US</option>
                </select>
                {status && (
                  <a href={status.token_urls[endpoint]} target="_blank" rel="noreferrer" className="font-medium underline">
                    Create an API token ↗
                  </a>
                )}{" "}
                (rights are pre-filled: read your domains, create/update DNS records; never delete). Validity: Unlimited.
              </li>
              <li>Paste the three keys OVH shows:</li>
            </ol>
            <Input value={ak} onChange={(e) => setAk(e.target.value.trim())} placeholder="Application key" required />
            <Input type="password" value={as} onChange={(e) => setAs(e.target.value.trim())} placeholder="Application secret" required />
            <Input type="password" value={ck} onChange={(e) => setCk(e.target.value.trim())} placeholder="Consumer key" required />
            <Button variant="primary" type="submit" disabled={busy}>{busy ? "Checking…" : "Connect"}</Button>
            <p className="text-xs text-zinc-500">Keys are verified with OVH, then stored encrypted on this server.</p>
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
      <h1 className="text-xl font-semibold">Settings</h1>
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
