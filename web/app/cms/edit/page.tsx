"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Shell } from "@/components/Shell";
import { Button, Card, ConfirmDialog, ErrorText, Input, Label, Notice, PageHeader, ReleaseBadge, Select, Spinner } from "@/components/ui";
import { api, ApiError, uploadFile, type CmsCollection, type CmsEntry, type CmsField, type CmsPage, type CmsSite, type ReleaseStatus } from "@/lib/api";
import { msg, tr, useT } from "@/lib/i18n";

type Values = Record<string, unknown>;
type Previews = Record<string, string>; // "mas-upload:…" → object URL

const FIELD_CLASS =
  "w-full rounded-lg border border-zinc-300 bg-white/80 px-3 py-2 text-sm text-zinc-900 outline-none transition " +
  "focus:border-indigo-400 focus:ring-4 focus:ring-indigo-500/15 dark:border-white/10 dark:bg-black/40 dark:text-zinc-100";
const UPLOAD_LIMIT_KB = 1800; // the dashboard accepts 2 MB per request: bigger photos are resized first

async function prepareImage(file: File, maxKb: number): Promise<Blob> {
  const limit = Math.min(maxKb, UPLOAD_LIMIT_KB) * 1024;
  if (file.size <= limit) return file;
  const bitmap = await createImageBitmap(file);
  let scale = Math.min(1, 2400 / Math.max(bitmap.width, bitmap.height));
  for (;;) {
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/webp", 0.85));
    if (!blob) throw new Error(tr("this image cannot be read"));
    if (blob.size <= limit || scale < 0.15) return blob;
    scale *= 0.75;
  }
}

function Counter({ value, max }: { value: string; max?: number }) {
  if (!max) return null;
  return <span className={`text-xs ${value.length > max ? "text-rose-500" : "text-zinc-500"}`}>{value.length}/{max}</span>;
}

interface InputProps {
  appId: string;
  field: CmsField;
  value: unknown;
  onChange: (v: unknown) => void;
  previews: Previews;
  addPreview: (key: string, url: string) => void;
}

function ImageInput({ appId, field, value, onChange, previews, addPreview }: InputProps & { value: string }) {
  const t = useT();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const src = value ? previews[value] ?? `/api/cms/sites/${encodeURIComponent(appId)}/image?src=${encodeURIComponent(value)}` : null;

  async function pick(file: File | undefined) {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const blob = await prepareImage(file, field.max_kb ?? 2048);
      const r = await uploadFile<{ value: string }>(`/cms/sites/${encodeURIComponent(appId)}/uploads?name=${encodeURIComponent(file.name)}`, blob);
      addPreview(r.value, URL.createObjectURL(blob));
      onChange(r.value);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : (e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <div className="flex h-24 w-36 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-zinc-300 bg-zinc-100 dark:border-white/10 dark:bg-black/40">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : <span className="text-xs text-zinc-500">{t("No image")}</span>}
        </div>
        <div className="flex flex-col gap-1.5">
          <Button type="button" onClick={() => input.current?.click()} disabled={busy}>{busy ? <Spinner /> : null}{value ? t("Replace") : t("Upload")}</Button>
          {value && !field.required && <Button type="button" variant="ghost" onClick={() => onChange("")}>{t("Remove")}</Button>}
        </div>
        <input ref={input} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/avif" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
      </div>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

function FieldInput(props: InputProps) {
  const t = useT();
  const { field, value, onChange } = props;
  const text = typeof value === "string" ? value : "";
  const title = `${field.label}${field.required ? " *" : ""}`;
  const hint = field.help || undefined;

  switch (field.type) {
    case "text":
      return (
        <Label title={title} hint={<span className="flex justify-between gap-2"><span>{hint}</span><Counter value={text} max={field.max} /></span>}>
          <Input value={text} maxLength={field.max} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />
        </Label>
      );
    case "textarea":
    case "markdown":
      return (
        <Label title={title} hint={<span className="flex justify-between gap-2"><span>{hint ?? (field.type === "markdown" ? t("Markdown: **bold**, *italic*, [link](https://…), blank line = new paragraph") : "")}</span><Counter value={text} max={field.max} /></span>}>
          <textarea value={text} maxLength={field.max} placeholder={field.placeholder} rows={field.type === "markdown" ? 10 : 4}
            onChange={(e) => onChange(e.target.value)} className={FIELD_CLASS} />
        </Label>
      );
    case "number":
      return (
        <Label title={title} hint={hint}>
          <Input type="number" value={typeof value === "number" ? value : ""} min={field.min} max={field.max} step={field.integer ? 1 : "any"}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
        </Label>
      );
    case "boolean":
      return (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
          <span className="font-medium">{field.label}</span>
          {hint && <span className="text-xs text-zinc-500">{hint}</span>}
        </label>
      );
    case "select":
      return (
        <Label title={title} hint={hint}>
          <Select value={text} onChange={(e) => onChange(e.target.value)}>
            {!field.required && <option value="">—</option>}
            {field.options?.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </Label>
      );
    case "url":
    case "email":
    case "date":
      return (
        <Label title={title} hint={hint ?? (field.type === "url" ? t("https://…, /page, mailto: or tel:") : undefined)}>
          <Input type={field.type === "url" ? "text" : field.type} value={text} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />
        </Label>
      );
    case "color":
      return (
        <Label title={title} hint={hint}>
          <div className="flex items-center gap-2">
            <input type="color" value={text || "#000000"} onChange={(e) => onChange(e.target.value)} className="h-9 w-12 rounded border border-zinc-300 bg-transparent dark:border-white/10" />
            <Input value={text} onChange={(e) => onChange(e.target.value)} placeholder="#1a2b3c" className="max-w-[8rem]" />
          </div>
        </Label>
      );
    case "image":
      return (
        <div className="space-y-1.5 text-sm">
          <span className="text-[13px] font-medium text-zinc-700 dark:text-zinc-300">{title}</span>
          <ImageInput {...props} value={text} />
          {hint && <span className="block text-xs text-zinc-500">{hint}</span>}
        </div>
      );
    case "list":
      return <ListInput {...props} />;
  }
}

function emptyItem(fields: CmsField[]): Values {
  return Object.fromEntries(fields.map((f) => [f.key, f.type === "boolean" ? false : f.type === "number" ? null : f.type === "list" ? [] : ""]));
}

function ListInput(props: InputProps) {
  const t = useT();
  const { field, onChange } = props;
  const items = Array.isArray(props.value) ? (props.value as Values[]) : [];
  const subs = field.fields ?? [];
  const max = field.max_items ?? 50;
  const move = (i: number, d: number) => {
    const next = [...items];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    onChange(next);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-zinc-700 dark:text-zinc-300">{field.label}</span>
        <span className="text-xs text-zinc-500">{items.length}/{max}</span>
      </div>
      {field.help && <p className="text-xs text-zinc-500">{field.help}</p>}
      {items.map((item, i) => (
        <div key={i} className="space-y-3 rounded-xl border border-zinc-200/80 p-4 dark:border-white/[0.06]">
          <div className="flex items-center justify-between text-xs text-zinc-500">
            <span>{field.item_label ?? t("Item")} {i + 1}</span>
            <div className="flex gap-1">
              <Button type="button" variant="ghost" disabled={i === 0} onClick={() => move(i, -1)} title={t("Move up")}>↑</Button>
              <Button type="button" variant="ghost" disabled={i === items.length - 1} onClick={() => move(i, 1)} title={t("Move down")}>↓</Button>
              <Button type="button" variant="ghost" disabled={items.length <= (field.min_items ?? 0)} onClick={() => onChange(items.filter((_, j) => j !== i))}>{t("Remove")}</Button>
            </div>
          </div>
          {subs.map((sub) => (
            <FieldInput key={sub.key} {...props} field={sub} value={item[sub.key]}
              onChange={(v) => onChange(items.map((it, j) => (j === i ? { ...it, [sub.key]: v } : it)))} />
          ))}
        </div>
      ))}
      <Button type="button" disabled={items.length >= max} onClick={() => onChange([...items, emptyItem(subs)])}>+ {t("Add")} {(field.item_label ?? t("item")).toLowerCase()}</Button>
    </div>
  );
}

type Saved = { unchanged: boolean; instant?: boolean }; // instant: stored in Supabase, online without a rebuild
type Run = (action: () => Promise<Saved>) => Promise<boolean>;

interface OpenEntry {
  slug: string | null; // null: not published yet
  original: Values;
  values: Values;
  sha: string | null;
  address: string;
}

function entryTitle(collection: CmsCollection, values: Values): string {
  const title = values[collection.title_field];
  return typeof title === "string" ? title : "";
}

function CollectionPanel({ appId, collection, busy, run, onDirty, previews, addPreview }: {
  appId: string;
  collection: CmsCollection;
  busy: boolean;
  run: Run;
  onDirty: (dirty: boolean) => void;
  previews: Previews;
  addPreview: (key: string, url: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState<OpenEntry | null>(null);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState("");
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const base = `/cms/sites/${encodeURIComponent(appId)}/collections/${encodeURIComponent(collection.id)}/entries`;
  const dirty = !!open && (JSON.stringify(open.values) !== JSON.stringify(open.original) || open.address !== "");
  useEffect(() => onDirty(dirty), [dirty, onDirty]);

  function edit(slug: string) {
    setError("");
    setLoading(slug);
    api<CmsEntry>(`${base}/${encodeURIComponent(slug)}`)
      .then((e) => setOpen({ slug: e.slug, original: e.values, values: e.values, sha: e.sha, address: "" }))
      .catch((e: ApiError) => setError(e.detail))
      .finally(() => setLoading(""));
  }

  function create() {
    const empty = emptyItem(collection.fields);
    setError("");
    setOpen({ slug: null, original: empty, values: empty, sha: null, address: "" });
  }

  function close() {
    if (dirty && !window.confirm(t("Discard the changes on this page?"))) return;
    setOpen(null);
    setNote("");
  }

  async function publish() {
    if (!open) return;
    const ok = await run(() => open.slug === null
      ? api<Saved>(base, { method: "POST", body: { values: open.values, slug: open.address.trim() || null, note } })
      : api<Saved>(`${base}/${encodeURIComponent(open.slug)}`, { method: "PUT", body: { values: open.values, sha: open.sha, note } }));
    if (ok) {
      setOpen(null);
      setNote("");
    }
  }

  async function remove() {
    if (!open?.slug) return;
    setDeleting(false);
    const ok = await run(() => api<Saved>(`${base}/${encodeURIComponent(open.slug!)}?sha=${encodeURIComponent(open.sha ?? "")}`, { method: "DELETE" }));
    if (ok) setOpen(null);
  }

  if (!open) {
    const sortKey = collection.sort_field;
    return (
      <Card title={collection.label} actions={<Button variant="primary" onClick={create} disabled={!!collection.error}>+ {t("Add")} {collection.item_label.toLowerCase()}</Button>}>
        <div className="space-y-3">
          {collection.description && <p className="text-sm text-zinc-500">{collection.description}</p>}
          <ErrorText>{error || collection.error}</ErrorText>
          {collection.entries.length === 0 ? (collection.error ? null :
            <p className="py-6 text-center text-sm text-zinc-500">{t("Nothing here yet. Add the first one.")}</p>
          ) : (
            <ul className="divide-y divide-zinc-200/80 dark:divide-white/[0.06]">
              {collection.entries.map((e) => (
                <li key={e.slug}>
                  <button onClick={() => edit(e.slug)} disabled={!!loading}
                    className="flex w-full items-center justify-between gap-3 rounded-lg px-2 py-2.5 text-left text-sm transition hover:bg-zinc-100 dark:hover:bg-white/[0.05]">
                    <span className="min-w-0">
                      <span className="block truncate font-medium">{entryTitle(collection, e) || e.slug}</span>
                      <span className="block truncate text-xs text-zinc-500">/{e.slug}</span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2 text-xs text-zinc-500">
                      {sortKey && e[sortKey] != null && <span>{String(e[sortKey])}</span>}
                      {loading === e.slug ? <Spinner /> : <span className="text-indigo-500">{t("Edit")} →</span>}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    );
  }

  const title = entryTitle(collection, open.values);
  return (
    <Card title={open.slug === null ? `${collection.item_label} · ${t("new")}` : title || open.slug}
      actions={<Button variant="ghost" onClick={close}>← {t("Back to the list")}</Button>}>
      <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); publish(); }}>
        {collection.fields.map((f) => (
          <FieldInput key={f.key} appId={appId} field={f} value={open.values[f.key]} previews={previews} addPreview={addPreview}
            onChange={(v) => setOpen({ ...open, values: { ...open.values, [f.key]: v } })} />
        ))}
        {open.slug === null ? (
          <Label title={t("Address")} hint={t("Leave empty to build it from the title. It cannot be changed once published.")}>
            <Input value={open.address} maxLength={80} placeholder="mon-article" onChange={(e) => setOpen({ ...open, address: e.target.value.toLowerCase() })} />
          </Label>
        ) : (
          <p className="text-xs text-zinc-500">{t("Address")}: <code>{open.slug}</code></p>
        )}
        <div className="sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200/80 bg-white/90 p-3 backdrop-blur dark:border-white/[0.08] dark:bg-zinc-950/90">
          <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={100} placeholder={t("What did you change? (optional)")} className="min-w-[12rem] flex-1" />
          {open.slug !== null && <Button type="button" variant="ghost" disabled={busy} onClick={() => setDeleting(true)}>{t("Delete")}</Button>}
          {dirty && open.slug !== null && <Button type="button" variant="ghost" onClick={() => setOpen({ ...open, values: open.original })}>{t("Discard")}</Button>}
          <Button variant="glow" disabled={!dirty || busy}>{busy ? <Spinner /> : null}{t("Publish")}</Button>
        </div>
      </form>
      <ConfirmDialog open={deleting} danger title={t("Delete “{name}”?", { name: title || open.slug || "" })}
        message={t("It is removed from the site at the next publication. Your administrator can bring it back from the history.")}
        confirmLabel={t("Delete")} onConfirm={remove} onCancel={() => setDeleting(false)} />
    </Card>
  );
}

type Publish = { phase: "saving" | "waiting" | ReleaseStatus; after: number; startedAt: number } | null;
type SiteContent = CmsSite & { pages: CmsPage[]; collections: CmsCollection[] };
type View = { kind: "page" | "collection" | "stats"; id: string };

interface SiteStats {
  days: { day: string; requests: number; pageviews: number; visitors: number }[];
  totals: { requests: number; pageviews: number; visitors: number } | null;
  top_pages: { path: string; views: number }[];
  since: string | null;
  tracked_since: string | null;
}
type Metric = "requests" | "pageviews" | "visitors";
const METRICS: { id: Metric; label: string }[] = [
  { id: "requests", label: msg("Requests") }, { id: "pageviews", label: msg("Page views") }, { id: "visitors", label: msg("Visitors") },
];
const fmtDay = (day: string, lang: string) => new Date(`${day}T12:00:00`).toLocaleDateString(lang, { day: "numeric", month: "short" });

/** Visits of the site per day, counted from its server logs (bots and files left out of page views). */
function StatsPanel({ appId }: { appId: string }) {
  const t = useT();
  const lang = typeof navigator !== "undefined" ? navigator.language : "fr";
  const [days, setDays] = useState(30);
  const [metric, setMetric] = useState<Metric>("requests");
  const [data, setData] = useState<SiteStats | null>(null);
  const [error, setError] = useState("");
  const [hover, setHover] = useState<number | null>(null);
  useEffect(() => {
    setData(null);
    api<SiteStats>(`/cms/sites/${encodeURIComponent(appId)}/stats?days=${days}`).then(setData).catch((e: ApiError) => setError(e.detail));
  }, [appId, days]);

  const series = data?.days ?? [];
  const max = Math.max(1, ...series.map((d) => d[metric]));
  const shown = hover !== null ? series[hover] : null;
  const label = METRICS.find((m) => m.id === metric)!.label;
  return (
    <Card title={t("Statistics")} actions={
      <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label={t("Period")} className="rounded border border-zinc-300 bg-transparent px-1.5 py-1 text-xs dark:border-zinc-700">
        {[7, 30, 90, 365].map((n) => <option key={n} value={n}>{t("{n} days", { n })}</option>)}
      </select>
    }>
      <ErrorText>{error}</ErrorText>
      {!data && !error && <div className="glass shimmer h-48 rounded-xl" />}
      {data && !data.totals && <p className="text-sm text-zinc-500">{t("No statistics: the site has no domain yet.")}</p>}
      {data?.totals && (
        <div className="space-y-6">
          <div className="grid grid-cols-3 gap-3">
            {METRICS.map((m) => (
              <button key={m.id} type="button" onClick={() => setMetric(m.id)} aria-pressed={metric === m.id}
                className={`rounded-xl border p-3 text-left transition ${metric === m.id ? "border-indigo-400/60 bg-indigo-500/10" : "border-zinc-200 hover:bg-zinc-50 dark:border-white/[0.08] dark:hover:bg-white/[0.04]"}`}>
                <div className="text-xs text-zinc-500">{t(m.label)}</div>
                <div className="mt-1 text-2xl font-semibold tabular-nums">{data.totals![m.id].toLocaleString(lang)}</div>
              </button>
            ))}
          </div>

          <div>
            <div className="mb-2 flex items-baseline justify-between gap-2 text-xs text-zinc-500">
              <span>{t("{metric} per day", { metric: t(label) })}</span>
              <span className="tabular-nums">{shown ? `${fmtDay(shown.day, lang)} · ${shown[metric].toLocaleString(lang)}` : t("max {n}", { n: max.toLocaleString(lang) })}</span>
            </div>
            <div className="relative flex h-40 items-end gap-[2px] border-b border-zinc-200 dark:border-white/[0.08]" onMouseLeave={() => setHover(null)}>
              {series.map((d, i) => (
                <div key={d.day} className="flex h-full flex-1 items-end" onMouseEnter={() => setHover(i)}
                  title={`${fmtDay(d.day, lang)} : ${d.requests} ${t("requests")}, ${d.pageviews} ${t("page views")}, ${d.visitors} ${t("visitors")}`}>
                  <div className={`w-full rounded-t-[4px] transition-colors ${hover === i ? "bg-indigo-400" : "bg-indigo-500"}`}
                    style={{ height: `${d[metric] ? Math.max(2, (d[metric] / max) * 100) : 0}%` }} />
                </div>
              ))}
            </div>
            <div className="mt-1 flex justify-between text-[11px] text-zinc-500">
              <span>{series[0] && fmtDay(series[0].day, lang)}</span>
              <span>{series.length > 0 && fmtDay(series[series.length - 1].day, lang)}</span>
            </div>
          </div>

          {data.top_pages.length > 0 && (
            <div>
              <div className="mb-2 text-xs text-zinc-500">{t("Most viewed pages")}</div>
              <ul className="divide-y divide-zinc-100 text-sm dark:divide-white/[0.06]">
                {data.top_pages.map((p) => (
                  <li key={p.path} className="flex justify-between gap-3 py-1.5"><span className="mono truncate text-xs">{p.path}</span><span className="tabular-nums text-zinc-500">{p.views.toLocaleString(lang)}</span></li>
                ))}
              </ul>
            </div>
          )}

          <details className="text-sm">
            <summary className="cursor-pointer text-xs text-zinc-500">{t("Table")}</summary>
            <table className="mt-2 w-full text-xs tabular-nums">
              <thead><tr className="text-left text-zinc-500"><th className="py-1 font-normal">{t("Day")}</th><th className="font-normal">{t("Requests")}</th><th className="font-normal">{t("Page views")}</th><th className="font-normal">{t("Visitors")}</th></tr></thead>
              <tbody>{[...series].reverse().map((d) => <tr key={d.day}><td className="py-0.5">{fmtDay(d.day, lang)}</td><td>{d.requests}</td><td>{d.pageviews}</td><td>{d.visitors}</td></tr>)}</tbody>
            </table>
          </details>

          <p className="text-xs text-zinc-500">
            {t("Counted from the server logs, without cookies: no address is kept. Page views leave out robots, images and scripts; a visitor is counted once per day.")}
            {data.tracked_since && ` ${t("Data since {date}.", { date: new Date(`${data.tracked_since}T12:00:00`).toLocaleDateString(lang) })}`}
          </p>
        </div>
      )}
    </Card>
  );
}

function Editor() {
  const t = useT();
  const appId = useSearchParams().get("app") ?? "";
  const [site, setSite] = useState<SiteContent | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [values, setValues] = useState<Record<string, Values>>({});
  const [previews, setPreviews] = useState<Previews>({});
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [publish, setPublish] = useState<Publish>(null);
  const [entryDirty, setEntryDirty] = useState(false);

  const load = useCallback(() => {
    if (!appId) return;
    api<SiteContent>(`/cms/sites/${encodeURIComponent(appId)}`).then((s) => {
      setSite(s);
      setValues(Object.fromEntries(s.pages.map((p) => [p.id, p.values])));
      const views: View[] = [...s.pages.map((p) => ({ kind: "page" as const, id: p.id })), ...s.collections.map((c) => ({ kind: "collection" as const, id: c.id }))];
      setView((current) => (current && views.some((v) => v.kind === current.kind && v.id === current.id) ? current : views[0] ?? null));
    }).catch((e: ApiError) => setError(e.detail));
  }, [appId]);
  useEffect(load, [load]);

  // Follow the deployment triggered by the commit.
  useEffect(() => {
    if (!publish || !["waiting", "queued", "building"].includes(publish.phase)) return;
    const timer = setInterval(async () => {
      try {
        const s = await api<CmsSite>(`/cms/sites/${encodeURIComponent(appId)}/status`);
        const last = s.last_release;
        if (last && last.id > publish.after) setPublish({ ...publish, phase: last.status });
        else if (Date.now() - publish.startedAt > 120_000) setPublish({ ...publish, phase: "live" }); // no deployment seen
      } catch {}
    }, 3000);
    return () => clearInterval(timer);
  }, [publish, appId]);

  const page = view?.kind === "page" ? site?.pages.find((p) => p.id === view.id) ?? null : null;
  const collection = view?.kind === "collection" ? site?.collections.find((c) => c.id === view.id) ?? null : null;
  const current = page ? values[page.id] ?? {} : {};
  const pageDirty = !!page && JSON.stringify(current) !== JSON.stringify(page.values);
  const dirty = pageDirty || (!!collection && entryDirty);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  // Commits a change, then follows the deployment it triggers.
  const run: Run = async (action) => {
    if (!site) return false;
    setError("");
    setMsg("");
    const after = site.last_release?.id ?? 0;
    setPublish({ phase: "saving", after, startedAt: Date.now() });
    try {
      const r = await action();
      if (r.unchanged) {
        setPublish(null);
        setMsg(t("Nothing changed."));
      } else {
        setPublish({ phase: r.instant ? "live" : "waiting", after, startedAt: Date.now() });
      }
      load();
      return true;
    } catch (e) {
      setPublish(null);
      setError((e as ApiError).detail);
      return false;
    }
  };

  async function save() {
    if (!page) return;
    const ok = await run(() => api<Saved>(`/cms/sites/${encodeURIComponent(appId)}/pages/${encodeURIComponent(page.id)}`,
      { method: "PUT", body: { values: current, sha: page.sha, note } }));
    if (ok) setNote("");
  }

  function switchView(next: View) {
    if (dirty && !window.confirm(t("Discard the changes on this page?"))) return;
    if (page) setValues({ ...values, [page.id]: page.values });
    setEntryDirty(false);
    setView(next);
  }

  if (!appId) return <ErrorText>{t("No site selected.")}</ErrorText>;
  if (!site) {
    return error
      ? <div className="space-y-4"><ErrorText>{error}</ErrorText><Link href="/cms/" className="text-sm text-indigo-500 hover:underline">← {t("My sites")}</Link></div>
      : <div className="glass shimmer h-64 rounded-2xl" />;
  }

  const busy = publish?.phase === "saving";
  const status = publish && publish.phase !== "saving" ? publish.phase : null;
  const nav: (View & { label: string })[] = [
    ...(site.domain ? [{ kind: "stats" as const, id: "stats", label: t("Statistics") }] : []),
    ...site.pages.map((p) => ({ kind: "page" as const, id: p.id, label: p.label })),
    ...site.collections.map((c) => ({ kind: "collection" as const, id: c.id, label: c.label })),
  ];

  return (
    <>
      <PageHeader eyebrow={<Link href="/cms/" className="hover:underline">← {t("My sites")}</Link>} title={site.name}
        subtitle={site.domain ?? undefined}
        actions={site.url ? <a href={site.url} target="_blank" rel="noreferrer" className="glass inline-flex h-8 items-center rounded-lg px-3 text-[13px] font-medium hover:bg-white/[0.08]">{t("View site")} ↗</a> : null} />
      <div className="mb-4 space-y-3">
        <ErrorText>{error}</ErrorText>
        <Notice>{msg}</Notice>
        {status && (
          <div className="glass flex items-center gap-3 rounded-xl px-4 py-3 text-sm">
            {status === "waiting" ? <><Spinner /> {t("Saved. Publishing your changes…")}</>
              : status === "live" ? <><ReleaseBadge status="live" /> {t("Your changes are online.")}{site.url && <a href={site.url} target="_blank" rel="noreferrer" className="text-indigo-500 hover:underline">{t("View")} ↗</a>}</>
              : status === "failed" ? <><ReleaseBadge status="failed" /> {t("Saved, but the site could not be rebuilt. Your administrator can see why in Mas.")}</>
              : <><ReleaseBadge status={status} /> {t("Publishing your changes…")}</>}
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-4">
        {nav.length > 1 && (
          <nav className="space-y-1 lg:col-span-1">
            {nav.map((v) => {
              const active = view?.kind === v.kind && view.id === v.id;
              const count = v.kind === "collection" ? site.collections.find((c) => c.id === v.id)?.entries.length : null;
              return (
                <button key={`${v.kind}:${v.id}`} onClick={() => !active && switchView(v)}
                  className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm transition ${active ? "bg-zinc-200 font-medium dark:bg-white/10" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/[0.05]"}`}>
                  <span className="truncate">{v.label}</span>
                  {count != null && <span className="text-xs text-zinc-500">{count}</span>}
                </button>
              );
            })}
          </nav>
        )}
        <div className={nav.length > 1 ? "lg:col-span-3" : "lg:col-span-4"}>
          {page && (
            <Card title={page.label}>
              <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); save(); }}>
                {page.description && <p className="text-sm text-zinc-500">{page.description}</p>}
                {page.fields.map((f) => (
                  <FieldInput key={f.key} appId={appId} field={f} value={current[f.key]} previews={previews}
                    addPreview={(k, url) => setPreviews((p) => ({ ...p, [k]: url }))}
                    onChange={(v) => setValues({ ...values, [page.id]: { ...current, [f.key]: v } })} />
                ))}
                <div className="sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200/80 bg-white/90 p-3 backdrop-blur dark:border-white/[0.08] dark:bg-zinc-950/90">
                  <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={100} placeholder={t("What did you change? (optional)")} className="min-w-[12rem] flex-1" />
                  {pageDirty && <Button type="button" variant="ghost" onClick={() => setValues({ ...values, [page.id]: page.values })}>{t("Discard")}</Button>}
                  <Button variant="glow" disabled={!pageDirty || busy}>{busy ? <Spinner /> : null}{t("Publish")}</Button>
                </div>
              </form>
            </Card>
          )}
          {view?.kind === "stats" && <StatsPanel appId={appId} />}
          {collection && (
            <CollectionPanel key={collection.id} appId={appId} collection={collection} busy={busy} run={run} onDirty={setEntryDirty}
              previews={previews} addPreview={(k, url) => setPreviews((p) => ({ ...p, [k]: url }))} />
          )}
        </div>
      </div>
    </>
  );
}

export default function CmsEditPage() {
  return (
    <Shell>
      <Suspense fallback={null}>
        <Editor />
      </Suspense>
    </Shell>
  );
}
