"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { MaintenancePreview } from "@/components/MaintenancePreview";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, ErrorText, Input, Label, PageHeader, Select, Spinner, Textarea } from "@/components/ui";
import { api, ApiError, timeAgo, uploadFile } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  disableMaintenance, enableMaintenance, type MaintenanceOverview, type MaintenancePage,
} from "@/lib/maintenance";

const parseAllow = (text: string) => text.split(/[\s,;]+/).filter(Boolean);

function PageEditor({ page, onSaved, onCancel }: { page: MaintenancePage | null; onSaved: () => void; onCancel: () => void }) {
  const t = useT();
  const [name, setName] = useState(page?.name ?? "");
  const [title, setTitle] = useState(page?.title ?? t("Site under maintenance"));
  const [message, setMessage] = useState(page?.message ?? t("We are making a few improvements. Come back in a moment."));
  const [back, setBack] = useState(page?.back ?? "");
  const [accent, setAccent] = useState(page?.accent ?? "#6366f1");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save() {
    setBusy(true);
    setError("");
    try {
      await api("/maintenance/pages", { method: "POST", body: { id: page?.id ?? null, name: name || title, title, message, back, accent } });
      onSaved();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3 text-sm">
      {page?.id !== "default" && <Label title={t("Name (for you)")}><Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Update")} /></Label>}
      <Label title={t("Title")}><Input value={title} onChange={(e) => setTitle(e.target.value)} /></Label>
      <Label title={t("Message")}><Textarea rows={3} value={message} onChange={(e) => setMessage(e.target.value)} /></Label>
      <Label title={t("Back at (optional)")}><Input value={back} onChange={(e) => setBack(e.target.value)} placeholder={t("Back around 2 p.m.")} /></Label>
      <Label title={t("Color")}><input type="color" value={accent} onChange={(e) => setAccent(e.target.value)} className="h-9 w-16 rounded border border-zinc-300 bg-transparent dark:border-white/10" /></Label>
      <ErrorText>{error}</ErrorText>
      <div className="flex gap-2">
        <Button variant="primary" disabled={busy || !title.trim()} onClick={save}>{busy ? <Spinner /> : null}{t("Save")}</Button>
        <Button onClick={onCancel}>{t("Cancel")}</Button>
      </div>
    </div>
  );
}

function Maintenance() {
  const t = useT();
  const [data, setData] = useState<MaintenanceOverview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [page, setPage] = useState("default");
  const [allow, setAllow] = useState("");
  const [until, setUntil] = useState("");
  const [editing, setEditing] = useState<MaintenancePage | "new" | null>(null);
  const [version, setVersion] = useState(0);
  const [deleting, setDeleting] = useState<MaintenancePage | null>(null);
  const [bulk, setBulk] = useState<"on" | "off" | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const load = useCallback(() => api<MaintenanceOverview>("/maintenance").then(setData).catch((e) => setError((e as ApiError).detail)), []);
  useEffect(() => { load(); }, [load]);

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setError("");
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }
  const on = (id: string) => enableMaintenance(id, page, parseAllow(allow), until ? new Date(until).toISOString() : null);
  async function importFile(f: File) {
    const name = f.name.replace(/\.(zip|html?)$/i, "");
    await run("import", async () => {
      const r = await uploadFile<{ id: string }>(`/maintenance/pages/import?name=${encodeURIComponent(name)}`, f);
      setPage(r.id);
      setVersion((v) => v + 1);
    });
  }

  const sites = data?.apps ?? [];
  const usable = sites.filter((s) => s.supported);
  const active = sites.filter((s) => s.maintenance);
  const pageName = (id: string) => data?.pages.find((p) => p.id === id)?.name ?? id;
  return (
    <>
      <PageHeader eyebrow={t("Sites")} title={t("Maintenance")}
        subtitle={t("Put a site on hold: visitors get a page of your choice (503, search engines come back later), the addresses you allow see the real site. The app keeps running.")}
        actions={<>
          {active.length > 0 && <Button disabled={busy !== ""} onClick={() => setBulk("off")}>{t("Bring every site back")}</Button>}
          {usable.length > active.length && <Button variant="primary" disabled={busy !== ""} onClick={() => setBulk("on")}>{t("Every site in maintenance")}</Button>}
        </>} />
      <ErrorText>{error}</ErrorText>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Card title={t("When a site goes into maintenance")}>
            <div className="grid gap-4 text-sm sm:grid-cols-2">
              <Label title={t("Page")}>
                <Select value={page} onChange={(e) => setPage(e.target.value)}>
                  {data?.pages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </Select>
              </Label>
              <Label title={t("Ends by itself (optional)")} hint={t("Empty: until you bring it back.")}>
                <Input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
              </Label>
              <div className="sm:col-span-2">
                <Label title={t("Addresses that see the real site")} hint={t("IP addresses or networks (10.0.0.0/8), one per line. Empty: nobody.")}>
                  <Textarea rows={2} value={allow} onChange={(e) => setAllow(e.target.value)} placeholder="203.0.113.7" />
                </Label>
                {data?.your_ip && !parseAllow(allow).includes(data.your_ip) && (
                  <button type="button" className="mt-1 text-xs text-indigo-600 hover:underline dark:text-indigo-400"
                    onClick={() => setAllow((a) => [...parseAllow(a), data.your_ip].join("\n"))}>{t("Add my address ({ip})", { ip: data.your_ip })}</button>
                )}
              </div>
            </div>
          </Card>

          <Card title={t("Sites")} padded={false}>
            <ul className="divide-y divide-zinc-100 dark:divide-white/[0.05]">
              {!data && <li className="p-5"><div className="glass shimmer h-16 rounded-xl" /></li>}
              {data && sites.length === 0 && <li className="p-5 text-sm text-zinc-500">{t("No app with a domain yet.")}</li>}
              {sites.map((s) => (
                <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <Link href={`/apps/view/?id=${encodeURIComponent(s.id)}`} className="font-medium hover:underline">{s.name}</Link>
                    <div className="mono truncate text-xs text-zinc-500">{s.domain}</div>
                    {s.maintenance && (
                      <div className="mt-1 text-xs text-zinc-500">
                        {t("“{page}” since {when} by {who}", { page: pageName(s.maintenance.page), when: timeAgo(s.maintenance.since), who: s.maintenance.by })}
                        {s.maintenance.until && ` · ${t("ends {date}", { date: new Date(s.maintenance.until).toLocaleString() })}`}
                        {s.maintenance.allow.length > 0 && ` · ${t("{n} address(es) allowed", { n: s.maintenance.allow.length })}`}
                      </div>
                    )}
                    {!s.supported && <div className="mt-1 text-xs text-zinc-500">{t("Its nginx site is not written by Mas: convert it to a Mas app first.")}</div>}
                  </div>
                  <div className="flex items-center gap-2">
                    {s.maintenance ? <Badge tone="amber">{t("in maintenance")}</Badge> : <Badge tone="emerald">{t("online")}</Badge>}
                    {s.supported && (s.maintenance
                      ? <>
                          <Button disabled={busy !== ""} onClick={() => run(s.id, () => on(s.id))} title={t("Apply the options above")}>{t("Update")}</Button>
                          <Button variant="primary" disabled={busy !== ""} onClick={() => run(s.id, () => disableMaintenance(s.id))}>{busy === s.id ? <Spinner /> : null}{t("Bring back")}</Button>
                        </>
                      : <Button disabled={busy !== ""} onClick={() => run(s.id, () => on(s.id))}>{busy === s.id ? <Spinner /> : null}{t("Put in maintenance")}</Button>)}
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        </div>

        <div className="space-y-6">
          <Card title={t("Pages")} actions={<div className="flex gap-2">
            <Button onClick={() => setEditing("new")}>{t("New")}</Button>
            <Button disabled={busy === "import"} onClick={() => file.current?.click()}>{busy === "import" ? <Spinner /> : null}{t("Import")}</Button>
          </div>}>
            <input ref={file} type="file" accept=".html,.htm,.zip,text/html,application/zip" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) importFile(f); }} />
            <p className="mb-4 text-xs text-zinc-500">{t("Import one HTML file, or a ZIP of a folder with an index.html and its images, styles and fonts. Scripts are allowed on the site, not in this preview.")}</p>
            {editing && (
              <div className="mb-5 rounded-xl border border-zinc-200 p-4 dark:border-white/[0.08]">
                <PageEditor page={editing === "new" ? null : editing} onCancel={() => setEditing(null)}
                  onSaved={() => { setEditing(null); setVersion((v) => v + 1); load(); }} />
              </div>
            )}
            <div className="space-y-4">
              {data?.pages.map((p) => (
                <div key={p.id} className={`rounded-xl border p-3 ${page === p.id ? "border-indigo-400/60" : "border-zinc-200 dark:border-white/[0.08]"}`}>
                  <button type="button" className="block w-full text-left" onClick={() => setPage(p.id)}>
                    <MaintenancePreview pageId={p.id} version={version} />
                  </button>
                  <div className="mt-2 flex items-center justify-between gap-2 text-sm">
                    <span className="truncate font-medium">{p.name}</span>
                    <span className="flex shrink-0 gap-1">
                      <Badge>{p.kind === "simple" ? t("written by Mas") : t("imported")}</Badge>
                      {p.kind === "simple" && <Button onClick={() => setEditing(p)}>{t("Edit")}</Button>}
                      {p.id !== "default" && <Button onClick={() => setDeleting(p)}>{t("Delete")}</Button>}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <ConfirmDialog open={bulk !== null} danger={bulk === "on"}
        title={bulk === "on" ? t("Every site in maintenance?") : t("Bring every site back?")}
        message={bulk === "on" ? t("{n} site(s) get the page “{page}” with the options above.", { n: usable.length - active.length, page: pageName(page) }) : t("{n} site(s) are served normally again.", { n: active.length })}
        confirmLabel={bulk === "on" ? t("Put in maintenance") : t("Bring back")}
        onConfirm={() => {
          const which = bulk;
          setBulk(null);
          run("bulk", async () => {
            for (const s of which === "on" ? usable.filter((x) => !x.maintenance) : active) {
              await (which === "on" ? on(s.id) : disableMaintenance(s.id));
            }
          });
        }}
        onCancel={() => setBulk(null)} />
      <ConfirmDialog open={deleting !== null} danger title={t("Delete “{name}”?", { name: deleting?.name ?? "" })}
        message={t("The page and its files are removed.")} confirmLabel={t("Delete")}
        onConfirm={() => { const p = deleting!; setDeleting(null); run("delete", () => api(`/maintenance/pages/${encodeURIComponent(p.id)}`, { method: "DELETE" })); }}
        onCancel={() => setDeleting(null)} />
    </>
  );
}

export default function MaintenancePageRoute() {
  return (
    <Shell>
      <Maintenance />
    </Shell>
  );
}
