"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { Badge, Button, Card, ErrorText, PageHeader, Spinner, Stat } from "@/components/ui";
import { api, ApiError, timeAgo } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

export type DepVuln = { id: string; cve: string[]; summary: string; severity: string; fixed: string | null; url: string };
export type DepFinding = { ecosystem: string; name: string; version: string; direct: boolean; severity: string; upgrade_to: string | null; vulns: DepVuln[] };
export type DepUpdate = {
  state: string; at: string | null; sha: string | null; error: string | null; release_id: number | null;
  done?: { name: string; version: string; now: string }[]; manual?: { name: string; version: string; ecosystem: string; to: string | null; why: string; code?: string }[]; pr?: { url: string } | null;
};
export type DepApp = {
  app: string; name: string; domain: string | null; at: string | null; packages: number; lockfiles: string[];
  error: string | null; findings: DepFinding[]; update: DepUpdate | null;
};
export type DepPayload = { settings: { enabled: boolean; auto: string; last_run: string | null }; apps: DepApp[] };

const SEVERITY_TONE: Record<string, "red" | "amber" | "sky" | "zinc"> = { critical: "red", high: "red", moderate: "amber", low: "sky", unknown: "zinc" };
const SEVERITY_LABEL: Record<string, string> = {
  critical: msg("critical"), high: msg("high"), moderate: msg("moderate"), low: msg("low"), unknown: msg("unknown"),
};
const UPDATE_STATE: Record<string, { label: string; tone: "emerald" | "sky" | "red" }> = {
  deploying: { label: msg("upgrade committed, deploying"), tone: "sky" },
  done: { label: msg("upgraded and deployed"), tone: "emerald" },
  pr: { label: msg("pull request opened"), tone: "sky" },
  reverted: { label: msg("upgrade undone: the release failed"), tone: "red" },
  failed: { label: msg("automatic upgrade failed"), tone: "red" },
};
const MODES: { id: string; label: string; hint: string }[] = [
  { id: "push", label: msg("Automatic"), hint: msg("Commit and deploy the fix (undone if the release fails)") },
  { id: "pr", label: msg("Pull request"), hint: msg("Open a pull request") },
  { id: "off", label: msg("Tell me only"), hint: msg("Off: only tell me") },
];

const REASON: Record<string, string> = {
  nofix: msg("no fixed version published"),
  ecosystem: msg("{eco} packages are not upgraded automatically"),
  major: msg("the fix is in a new major version ({v})"),
  blocked: msg("the automatic upgrade failed, not retried before a week"),
};

/** The last dependency check of every app, reloaded on demand. */
export function useDependencies() {
  const [data, setData] = useState<DepPayload | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<DepPayload>("/dependencies").then(setData).catch((e: ApiError) => setError(e.detail));
  }, []);
  useEffect(load, [load]);
  return { data, setData, error, setError, load };
}

export function vulnerableCount(data: DepPayload | null): { packages: number; apps: number; serious: number } {
  const apps = (data?.apps ?? []).filter((a) => a.findings.length);
  const all = apps.flatMap((a) => a.findings);
  return { packages: all.length, apps: apps.length, serious: all.filter((f) => f.direct || f.severity === "critical" || f.severity === "high").length };
}

function UpdateLine({ update }: { update: DepUpdate | null }) {
  const t = useT();
  if (!update || !UPDATE_STATE[update.state]) return null;
  const s = UPDATE_STATE[update.state];
  return (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <Badge tone={s.tone}>{t(s.label)}</Badge>
      {update.done?.length ? <span className="text-zinc-500">{update.done.map((d) => `${d.name} ${d.version} → ${d.now}`).join(", ")}</span> : null}
      {update.pr?.url && <a className="text-indigo-600 hover:underline dark:text-indigo-400" href={update.pr.url} target="_blank" rel="noreferrer">{t("open the pull request")} →</a>}
      {update.at && <span className="text-zinc-400">{timeAgo(update.at)}</span>}
      {update.error && update.state !== "done" && <p className="w-full whitespace-pre-wrap text-zinc-500">{update.error.slice(0, 400)}</p>}
    </div>
  );
}

function FindingRow({ f }: { f: DepFinding }) {
  const t = useT();
  return (
    <li className="flex flex-wrap items-start gap-x-3 gap-y-1 py-2.5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="mono text-sm font-medium">{f.name}</span>
          <span className="mono text-xs text-zinc-500">{f.version}</span>
          <Badge tone={SEVERITY_TONE[f.severity] ?? "zinc"}>{t(SEVERITY_LABEL[f.severity] ?? f.severity)}</Badge>
          {!f.direct && <span className="text-xs text-zinc-400">{t("sub-dependency")}</span>}
        </div>
        {f.vulns[0]?.summary && <p className="mt-0.5 text-xs text-zinc-600 dark:text-zinc-400">{f.vulns[0].summary}</p>}
        <p className="mt-0.5 flex flex-wrap gap-x-2 text-xs">
          {f.vulns.slice(0, 6).map((v) => (
            <a key={v.id} href={v.url} target="_blank" rel="noreferrer" className="mono text-zinc-500 hover:text-indigo-600 hover:underline dark:hover:text-indigo-400">{v.cve[0] ?? v.id}</a>
          ))}
          {f.vulns.length > 6 && <span className="text-zinc-400">+{f.vulns.length - 6}</span>}
        </p>
      </div>
      <div className="shrink-0 text-right text-xs">
        {f.upgrade_to
          ? <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 font-medium text-emerald-700 dark:text-emerald-300">→ {f.upgrade_to}</span>
          : <span className="text-zinc-500">{t("no fix yet")}</span>}
      </div>
    </li>
  );
}

function AppBlock({ a, busy, onUpgrade, link = true }: { a: DepApp; busy: boolean; onUpgrade: (id: string) => void; link?: boolean }) {
  const t = useT();
  const fixable = a.findings.some((f) => f.upgrade_to) && a.update?.state !== "deploying";
  return (
    <div className="glass animate-fade-up rounded-2xl border-l-2 border-l-rose-500 p-5">
      <div className="flex flex-wrap items-center gap-2">
        {link ? <Link href={`/apps/view/?id=${encodeURIComponent(a.app)}`} className="font-semibold hover:underline">{a.name}</Link> : <span className="flex items-center gap-1.5 font-semibold"><ShieldAlert className="size-4 text-rose-500" />{t("Dependencies")}</span>}
        {link && a.domain && <span className="text-xs text-zinc-500">{a.domain}</span>}
        <Badge tone="red">{t("{n} vulnerable", { n: a.findings.length })}</Badge>
        {fixable && (
          <Button className="ml-auto" variant="primary" disabled={busy} onClick={() => onUpgrade(a.app)}>
            {busy ? <Spinner /> : null}{t("Upgrade now")}
          </Button>
        )}
      </div>
      <div className="mt-2"><UpdateLine update={a.update} /></div>
      <ul className="mt-1 divide-y divide-zinc-200/70 dark:divide-white/[0.06]">
        {a.findings.map((f) => <FindingRow key={f.name + f.version} f={f} />)}
      </ul>
      {a.update?.manual?.length ? (
        <div className="mt-2 rounded-lg bg-amber-500/10 p-2 text-xs text-amber-800 dark:text-amber-300">
          <p className="font-medium">{t("Left to a person or the robot:")}</p>
          {a.update.manual.map((m) => <p key={m.name}>{m.name} {m.version}: {m.code && REASON[m.code] ? t(REASON[m.code], { eco: m.ecosystem, v: m.to ?? "" }) : m.why}</p>)}
        </div>
      ) : null}
      {a.error && <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">{a.error}</p>}
    </div>
  );
}

async function upgradeApp(appId: string): Promise<DepPayload> {
  return api<DepPayload>(`/dependencies/update?app_id=${encodeURIComponent(appId)}`, { method: "POST" });
}

/** The page: every app, what is vulnerable, what Mas did about it. */
export function DependenciesView() {
  const t = useT();
  const { data, setData, error, setError } = useDependencies();
  const [busy, setBusy] = useState("");

  async function run(key: string, fn: () => Promise<DepPayload | { enabled: boolean; auto: string }>) {
    setError(""); setBusy(key);
    try {
      const r = await fn();
      if ("apps" in r) setData(r);
      else setData((d) => d && { ...d, settings: { ...d.settings, ...r } });
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }
  const save = (body: { enabled?: boolean; auto?: string }) =>
    run("settings", () => api<{ enabled: boolean; auto: string }>("/dependencies/settings", { method: "PUT", body }));

  const count = vulnerableCount(data);
  const checked = (data?.apps ?? []).filter((a) => a.at);
  const clean = checked.filter((a) => !a.findings.length);
  const moving = (data?.apps ?? []).filter((a) => a.update?.state === "deploying" || a.update?.state === "pr").length;
  const lastRun = data?.settings.last_run;

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow={t("Security")}
        title={t("Dependencies")}
        subtitle={<>{t("Known vulnerabilities in the packages installed with each app, from the public OSV database, and their automatic upgrade.")}{" "}{lastRun ? t("Checked {when}.", { when: timeAgo(lastRun) }) : ""}</>}
        actions={<Button variant="primary" onClick={() => run("scan", () => api<DepPayload>("/dependencies/scan", { method: "POST" }))} disabled={!!busy}>{busy === "scan" ? <Spinner /> : null}{t("Check now")}</Button>}
      />
      <ErrorText>{error}</ErrorText>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={t("Vulnerable packages")} value={count.packages} tone={count.packages ? "red" : "emerald"} hint={count.serious ? t("{n} serious", { n: count.serious }) : undefined} />
        <Stat label={t("Apps affected")} value={<>{count.apps}<span className="text-lg text-zinc-500">/{checked.length}</span></>} tone={count.apps ? "amber" : "emerald"} />
        <Stat label={t("Upgrades in progress")} value={moving} tone={moving ? "sky" : "zinc"} />
        <Stat label={t("Packages checked")} value={checked.reduce((n, a) => n + a.packages, 0)} tone="violet" />
      </div>

      <Card title={t("Automatic upgrades")} actions={data && !data.settings.enabled ? <Badge tone="zinc">{t("check off")}</Badge> : null}>
        <div className="space-y-3 text-sm">
          <div className="glass inline-flex flex-wrap rounded-xl p-1">
            {MODES.map((m) => (
              <button key={m.id} type="button" disabled={!data || !!busy} onClick={() => save({ auto: m.id })}
                className={`rounded-lg px-3 py-1.5 text-xs transition ${data?.settings.auto === m.id ? "bg-zinc-900 text-white shadow dark:bg-white dark:text-zinc-900" : "text-zinc-500 hover:text-zinc-900 dark:hover:text-white"}`}>
                {t(m.label)}
              </button>
            ))}
          </div>
          <p className="text-zinc-600 dark:text-zinc-400">{t(MODES.find((m) => m.id === data?.settings.auto)?.hint ?? "")}</p>
          <p className="text-xs text-zinc-500">{t("Only security fixes within the same major version, computed without running any package script. A major upgrade is left to you or the robot.")}</p>
          <label className="flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400">
            <input type="checkbox" checked={data?.settings.enabled ?? true} disabled={!data || !!busy} onChange={(e) => save({ enabled: e.target.checked })} />
            {t("Check the dependencies every day")} <span className="text-zinc-400">({t("only package names and versions are sent to osv.dev")})</span>
          </label>
        </div>
      </Card>

      {!data && !error && <p className="text-sm text-zinc-500"><Spinner /> {t("Loading…")}</p>}

      {data && count.packages === 0 && checked.length > 0 && (
        <div className="flex items-center gap-3 rounded-2xl border border-emerald-400/30 bg-emerald-500/10 p-5 text-emerald-800 dark:text-emerald-300">
          <ShieldCheck className="size-6 shrink-0" />
          <p className="text-sm">{t("No known vulnerability in the installed packages.")}</p>
        </div>
      )}

      <div className="space-y-3">
        {(data?.apps ?? []).filter((a) => a.findings.length).map((a) => (
          <AppBlock key={a.app} a={a} busy={busy === a.app} onUpgrade={(id) => run(id, () => upgradeApp(id))} />
        ))}
      </div>

      {data && (clean.length > 0 || data.apps.some((a) => !a.at)) && (
        <Card title={t("Up to date")}>
          <ul className="space-y-1.5 text-sm">
            {clean.map((a) => (
              <li key={a.app} className="flex flex-wrap items-center gap-2">
                <ShieldCheck className="size-4 text-emerald-500" />
                <Link href={`/apps/view/?id=${encodeURIComponent(a.app)}`} className="font-medium hover:underline">{a.name}</Link>
                <span className="text-xs text-zinc-500">{t("{n} packages", { n: a.packages })} · {a.lockfiles.join(", ") || t("no lock file")}</span>
                {a.update && <UpdateLine update={a.update} />}
              </li>
            ))}
            {data.apps.filter((a) => !a.at).map((a) => (
              <li key={a.app} className="flex items-center gap-2 text-zinc-500">
                <span className="inline-block size-4" />{a.name} <span className="text-xs">{t("not checked yet")}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}

/** On the overview: said loudly when something is vulnerable, nothing otherwise. */
export function DependencyAlert() {
  const t = useT();
  const { data } = useDependencies();
  const count = vulnerableCount(data);
  if (!count.packages) return null;
  return (
    <Link href="/dependencies/" className="group flex items-center gap-3 rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-rose-800 transition hover:bg-rose-500/15 dark:text-rose-300">
      <ShieldAlert className="size-6 shrink-0" />
      <div className="min-w-0 flex-1 text-sm">
        <p className="font-semibold">{t("{n} vulnerable package(s) in {apps} app(s)", { n: count.packages, apps: count.apps })}</p>
        <p className="text-xs opacity-80">{data?.settings.auto === "off" ? t("Automatic upgrades are off: upgrade them from the Dependencies page.") : t("Mas upgrades them automatically; see what was done and what is left.")}</p>
      </div>
      <span className="text-sm font-medium group-hover:underline">{t("See the dependencies")} →</span>
    </Link>
  );
}

/** On the page of an app: its own packages. */
export function AppDependenciesCard({ appId }: { appId: string }) {
  const t = useT();
  const { data, setData, error, setError } = useDependencies();
  const [busy, setBusy] = useState(false);
  const a = data?.apps.find((x) => x.app === appId);
  if (!a) return error ? <Card title={t("Dependencies")}><ErrorText>{error}</ErrorText></Card> : null;

  async function upgrade() {
    setError(""); setBusy(true);
    try {
      setData(await upgradeApp(appId));
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }

  if (a.findings.length) {
    return (
      <div className="space-y-2">
        <AppBlock a={a} busy={busy} onUpgrade={upgrade} link={false} />
        <ErrorText>{error}</ErrorText>
      </div>
    );
  }
  return (
    <Card title={t("Dependencies")} actions={a.at ? <Badge tone="emerald">{t("up to date")}</Badge> : <Badge>{t("not checked yet")}</Badge>}>
      <div className="space-y-2 text-sm">
        <p className="flex items-center gap-2 text-zinc-600 dark:text-zinc-400">
          <ShieldCheck className="size-4 text-emerald-500" />
          {a.at ? t("No known vulnerability in {n} packages, checked {when}.", { n: a.packages, when: timeAgo(a.at) }) : t("Checked after the next deployment.")}
        </p>
        <UpdateLine update={a.update} />
        <Link href="/dependencies/" className="text-xs text-indigo-600 hover:underline dark:text-indigo-400">{t("All dependencies")} →</Link>
      </div>
    </Card>
  );
}
