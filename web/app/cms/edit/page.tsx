"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Shell } from "@/components/Shell";
import { Button, Card, ErrorText, Input, Label, Notice, PageHeader, ReleaseBadge, Select, Spinner } from "@/components/ui";
import { api, ApiError, uploadFile, type CmsField, type CmsPage, type CmsSite, type ReleaseStatus } from "@/lib/api";

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
    if (!blob) throw new Error("this image cannot be read");
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
          {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : <span className="text-xs text-zinc-500">No image</span>}
        </div>
        <div className="flex flex-col gap-1.5">
          <Button type="button" onClick={() => input.current?.click()} disabled={busy}>{busy ? <Spinner /> : null}{value ? "Replace" : "Upload"}</Button>
          {value && !field.required && <Button type="button" variant="ghost" onClick={() => onChange("")}>Remove</Button>}
        </div>
        <input ref={input} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/avif" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
      </div>
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

function FieldInput(props: InputProps) {
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
        <Label title={title} hint={<span className="flex justify-between gap-2"><span>{hint ?? (field.type === "markdown" ? "Markdown: **bold**, *italic*, [link](https://…), blank line = new paragraph" : "")}</span><Counter value={text} max={field.max} /></span>}>
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
        <Label title={title} hint={hint ?? (field.type === "url" ? "https://…, /page, mailto: or tel:" : undefined)}>
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
  return Object.fromEntries(fields.map((f) => [f.key, f.type === "boolean" ? false : f.type === "number" ? null : ""]));
}

function ListInput(props: InputProps) {
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
            <span>{field.item_label ?? "Item"} {i + 1}</span>
            <div className="flex gap-1">
              <Button type="button" variant="ghost" disabled={i === 0} onClick={() => move(i, -1)} title="Move up">↑</Button>
              <Button type="button" variant="ghost" disabled={i === items.length - 1} onClick={() => move(i, 1)} title="Move down">↓</Button>
              <Button type="button" variant="ghost" disabled={items.length <= (field.min_items ?? 0)} onClick={() => onChange(items.filter((_, j) => j !== i))}>Remove</Button>
            </div>
          </div>
          {subs.map((sub) => (
            <FieldInput key={sub.key} {...props} field={sub} value={item[sub.key]}
              onChange={(v) => onChange(items.map((it, j) => (j === i ? { ...it, [sub.key]: v } : it)))} />
          ))}
        </div>
      ))}
      <Button type="button" disabled={items.length >= max} onClick={() => onChange([...items, emptyItem(subs)])}>+ Add {(field.item_label ?? "item").toLowerCase()}</Button>
    </div>
  );
}

type Publish = { phase: "saving" | "waiting" | ReleaseStatus; after: number; startedAt: number } | null;

function Editor() {
  const appId = useSearchParams().get("app") ?? "";
  const [site, setSite] = useState<(CmsSite & { pages: CmsPage[] }) | null>(null);
  const [pageId, setPageId] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, Values>>({});
  const [previews, setPreviews] = useState<Previews>({});
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState("");
  const [publish, setPublish] = useState<Publish>(null);

  const load = useCallback(() => {
    if (!appId) return;
    api<CmsSite & { pages: CmsPage[] }>(`/cms/sites/${encodeURIComponent(appId)}`).then((s) => {
      setSite(s);
      setValues(Object.fromEntries(s.pages.map((p) => [p.id, p.values])));
      setPageId((current) => (current && s.pages.some((p) => p.id === current) ? current : s.pages[0]?.id ?? null));
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

  const page = site?.pages.find((p) => p.id === pageId) ?? null;
  const current = page ? values[page.id] ?? {} : {};
  const dirty = !!page && JSON.stringify(current) !== JSON.stringify(page.values);

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save() {
    if (!page || !site) return;
    setError("");
    setMsg("");
    const after = site.last_release?.id ?? 0;
    setPublish({ phase: "saving", after, startedAt: Date.now() });
    try {
      const r = await api<{ unchanged: boolean }>(`/cms/sites/${encodeURIComponent(appId)}/pages/${encodeURIComponent(page.id)}`,
        { method: "PUT", body: { values: current, sha: page.sha, note } });
      setNote("");
      if (r.unchanged) {
        setPublish(null);
        setMsg("Nothing changed.");
      } else {
        setPublish({ phase: "waiting", after, startedAt: Date.now() });
      }
      load();
    } catch (e) {
      setPublish(null);
      setError((e as ApiError).detail);
    }
  }

  function switchPage(id: string) {
    if (dirty && !window.confirm("Discard the changes on this page?")) return;
    if (page) setValues({ ...values, [page.id]: page.values });
    setPageId(id);
  }

  if (!appId) return <ErrorText>No site selected.</ErrorText>;
  if (!site) {
    return error
      ? <div className="space-y-4"><ErrorText>{error}</ErrorText><Link href="/cms/" className="text-sm text-indigo-500 hover:underline">← My sites</Link></div>
      : <div className="glass shimmer h-64 rounded-2xl" />;
  }

  const busy = publish?.phase === "saving";
  const status = publish && publish.phase !== "saving" ? publish.phase : null;

  return (
    <>
      <PageHeader eyebrow={<Link href="/cms/" className="hover:underline">← My sites</Link>} title={site.name}
        subtitle={site.domain ?? undefined}
        actions={site.url ? <a href={site.url} target="_blank" rel="noreferrer" className="glass inline-flex h-8 items-center rounded-lg px-3 text-[13px] font-medium hover:bg-white/[0.08]">View site ↗</a> : null} />
      <div className="mb-4 space-y-3">
        <ErrorText>{error}</ErrorText>
        <Notice>{msg}</Notice>
        {status && (
          <div className="glass flex items-center gap-3 rounded-xl px-4 py-3 text-sm">
            {status === "waiting" ? <><Spinner /> Saved. Publishing your changes…</>
              : status === "live" ? <><ReleaseBadge status="live" /> Your changes are online.{site.url && <a href={site.url} target="_blank" rel="noreferrer" className="text-indigo-500 hover:underline">View ↗</a>}</>
              : status === "failed" ? <><ReleaseBadge status="failed" /> Saved, but the site could not be rebuilt. Your administrator can see why in Mas.</>
              : <><ReleaseBadge status={status} /> Publishing your changes…</>}
          </div>
        )}
      </div>

      <div className="grid gap-6 lg:grid-cols-4">
        {site.pages.length > 1 && (
          <nav className="space-y-1 lg:col-span-1">
            {site.pages.map((p) => (
              <button key={p.id} onClick={() => switchPage(p.id)}
                className={`block w-full rounded-lg px-3 py-2 text-left text-sm transition ${p.id === pageId ? "bg-zinc-200 font-medium dark:bg-white/10" : "text-zinc-500 hover:bg-zinc-100 dark:hover:bg-white/[0.05]"}`}>
                {p.label}
              </button>
            ))}
          </nav>
        )}
        {page && (
          <div className={site.pages.length > 1 ? "lg:col-span-3" : "lg:col-span-4"}>
            <Card title={page.label}>
              <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); save(); }}>
                {page.description && <p className="text-sm text-zinc-500">{page.description}</p>}
                {page.fields.map((f) => (
                  <FieldInput key={f.key} appId={appId} field={f} value={current[f.key]} previews={previews}
                    addPreview={(k, url) => setPreviews((p) => ({ ...p, [k]: url }))}
                    onChange={(v) => setValues({ ...values, [page.id]: { ...current, [f.key]: v } })} />
                ))}
                <div className="sticky bottom-4 z-10 flex flex-wrap items-center gap-2 rounded-xl border border-zinc-200/80 bg-white/90 p-3 backdrop-blur dark:border-white/[0.08] dark:bg-zinc-950/90">
                  <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={100} placeholder="What did you change? (optional)" className="min-w-[12rem] flex-1" />
                  {dirty && <Button type="button" variant="ghost" onClick={() => setValues({ ...values, [page.id]: page.values })}>Discard</Button>}
                  <Button variant="glow" disabled={!dirty || busy}>{busy ? <Spinner /> : null}Publish</Button>
                </div>
              </form>
            </Card>
          </div>
        )}
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
