"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import { ArrowDown, ArrowUp, ImagePlus, Library, Plus, Trash2, Upload } from "lucide-react";
import { Button } from "@/components/cms/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/cms/ui/dialog";
import { Input, Textarea } from "@/components/cms/ui/input";
import { Label } from "@/components/cms/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/cms/ui/select";
import { Switch } from "@/components/cms/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/cms/ui/tabs";
import { api, ApiError, uploadFile, type CmsField, type CmsMedia } from "@/lib/api";
import { tr, useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

export type Values = Record<string, unknown>;
export type Previews = Record<string, string>; // "mas-upload:…" → object URL

const UPLOAD_LIMIT_KB = 1800; // the dashboard accepts 2 MB per request: bigger photos are resized first

export async function prepareImage(file: File, maxKb: number): Promise<Blob> {
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

export const imageUrl = (appId: string, value: string) => `/api/cms/sites/${encodeURIComponent(appId)}/image?src=${encodeURIComponent(value)}`;

function Counter({ value, max }: { value: string; max?: number }) {
  if (!max) return null;
  return <span className={cn("text-xs tabular-nums", value.length > max ? "text-destructive" : "text-muted-foreground")}>{value.length}/{max}</span>;
}

function Field({ title, hint, extra, children }: { title: string; hint?: ReactNode; extra?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2"><Label>{title}</Label>{extra}</div>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export interface InputProps {
  appId: string;
  field: CmsField;
  value: unknown;
  onChange: (v: unknown) => void;
  previews: Previews;
  addPreview: (key: string, url: string) => void;
}

/** Pick an image the site already has (media library of the repository). */
export function MediaPicker({ appId, folder, open, onClose, onPick }: { appId: string; folder?: string; open: boolean; onClose: () => void; onPick: (value: string) => void }) {
  const t = useT();
  const [media, setMedia] = useState<CmsMedia | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    api<CmsMedia>(`/cms/sites/${encodeURIComponent(appId)}/media`).then(setMedia).catch((e: ApiError) => setError(e.detail));
  }, [open, appId]);
  const items = (media?.items ?? []).filter((i) => !folder || i.dir === folder);
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("Media library")}</DialogTitle>
          <DialogDescription>{t("Choose an image already on the site.")}</DialogDescription>
        </DialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {media && items.length === 0 && <p className="py-8 text-center text-sm text-muted-foreground">{t("No image yet.")}</p>}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {items.map((i) => (
            <button key={i.value} type="button" onClick={() => { onPick(i.value); onClose(); }}
              className="group overflow-hidden rounded-md border text-left transition hover:ring-2 hover:ring-ring">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={imageUrl(appId, i.value)} alt="" loading="lazy" className="aspect-square w-full bg-muted object-cover" />
              <span className="block truncate px-2 py-1 text-xs text-muted-foreground">{i.name}</span>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ImageInput({ appId, field, value, onChange, previews, addPreview }: InputProps & { value: string }) {
  const t = useT();
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [library, setLibrary] = useState(false);
  const src = value ? previews[value] ?? imageUrl(appId, value) : null;

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
    <Field title={`${field.label}${field.required ? " *" : ""}`} hint={field.help}>
      <div className="flex items-center gap-4">
        <div className="flex h-24 w-36 shrink-0 items-center justify-center overflow-hidden rounded-md border bg-muted">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : <ImagePlus className="size-6 text-muted-foreground" />}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" size="sm" onClick={() => input.current?.click()} disabled={busy}><Upload />{value ? t("Replace") : t("Upload")}</Button>
          <Button type="button" variant="outline" size="sm" onClick={() => setLibrary(true)}><Library />{t("Library")}</Button>
          {value && !field.required && <Button type="button" variant="ghost" size="sm" onClick={() => onChange("")}>{t("Remove")}</Button>}
        </div>
        <input ref={input} type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/avif" className="hidden" onChange={(e) => pick(e.target.files?.[0])} />
      </div>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <MediaPicker appId={appId} folder={field.dir} open={library} onClose={() => setLibrary(false)} onPick={onChange} />
    </Field>
  );
}

function MarkdownInput({ field, text, onChange }: { field: CmsField; text: string; onChange: (v: string) => void }) {
  const t = useT();
  return (
    <Field title={`${field.label}${field.required ? " *" : ""}`} extra={<Counter value={text} max={field.max} />} hint={field.help || t("Markdown: **bold**, *italic*, [link](https://…), blank line = new paragraph")}>
      <Tabs defaultValue="write">
        <TabsList className="h-8"><TabsTrigger value="write" className="py-0.5">{t("Write")}</TabsTrigger><TabsTrigger value="preview" className="py-0.5">{t("Preview")}</TabsTrigger></TabsList>
        <TabsContent value="write"><Textarea value={text} maxLength={field.max} placeholder={field.placeholder} rows={14} className="font-mono text-[13px]" onChange={(e) => onChange(e.target.value)} /></TabsContent>
        <TabsContent value="preview">
          <div className="prose-cms min-h-40 rounded-md border bg-background p-4 text-sm leading-relaxed [&_a]:text-primary [&_a]:underline [&_h1]:mb-2 [&_h1]:text-xl [&_h1]:font-semibold [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold [&_h3]:font-semibold [&_li]:ml-5 [&_li]:list-disc [&_p]:mb-3 [&_pre]:overflow-auto [&_pre]:rounded [&_pre]:bg-muted [&_pre]:p-3">
            {text.trim() ? <Markdown>{text}</Markdown> : <p className="text-muted-foreground">{t("Nothing to preview.")}</p>}
          </div>
        </TabsContent>
      </Tabs>
    </Field>
  );
}

export function emptyItem(fields: CmsField[]): Values {
  return Object.fromEntries(fields.map((f) => [f.key, f.type === "boolean" ? false : f.type === "number" ? null : f.type === "list" ? [] : ""]));
}

export function FieldInput(props: InputProps) {
  const t = useT();
  const { field, value, onChange } = props;
  const text = typeof value === "string" ? value : "";
  const title = `${field.label}${field.required ? " *" : ""}`;
  const hint = field.help || undefined;

  switch (field.type) {
    case "text":
      return <Field title={title} hint={hint} extra={<Counter value={text} max={field.max} />}><Input value={text} maxLength={field.max} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} /></Field>;
    case "textarea":
      return <Field title={title} hint={hint} extra={<Counter value={text} max={field.max} />}><Textarea value={text} maxLength={field.max} placeholder={field.placeholder} rows={4} onChange={(e) => onChange(e.target.value)} /></Field>;
    case "markdown":
      return <MarkdownInput field={field} text={text} onChange={onChange} />;
    case "number":
      return (
        <Field title={title} hint={hint}>
          <Input type="number" value={typeof value === "number" ? value : ""} min={field.min} max={field.max} step={field.integer ? 1 : "any"}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
        </Field>
      );
    case "boolean":
      return (
        <div className="flex items-center gap-3">
          <Switch checked={value === true} onCheckedChange={onChange} id={`f-${field.key}`} />
          <Label htmlFor={`f-${field.key}`}>{field.label}</Label>
          {hint && <span className="text-xs text-muted-foreground">{hint}</span>}
        </div>
      );
    case "select":
      return (
        <Field title={title} hint={hint}>
          <Select value={text || "__none"} onValueChange={(v) => onChange(v === "__none" ? "" : v)}>
            <SelectTrigger><SelectValue /></SelectTrigger>
            <SelectContent>
              {!field.required && <SelectItem value="__none">—</SelectItem>}
              {field.options?.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
        </Field>
      );
    case "url":
    case "email":
    case "date":
      return (
        <Field title={title} hint={hint ?? (field.type === "url" ? t("https://…, /page, mailto: or tel:") : undefined)}>
          <Input type={field.type === "url" ? "text" : field.type} value={text} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value)} />
        </Field>
      );
    case "color":
      return (
        <Field title={title} hint={hint}>
          <div className="flex items-center gap-2">
            <input type="color" value={text || "#000000"} onChange={(e) => onChange(e.target.value)} className="h-9 w-12 cursor-pointer rounded-md border border-input bg-transparent p-1" />
            <Input value={text} onChange={(e) => onChange(e.target.value)} placeholder="#1a2b3c" className="max-w-[8rem]" />
          </div>
        </Field>
      );
    case "image":
      return <ImageInput {...props} value={text} />;
    case "list":
      return <ListInput {...props} />;
  }
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
    <Field title={field.label} hint={field.help} extra={<span className="text-xs tabular-nums text-muted-foreground">{items.length}/{max}</span>}>
      <div className="space-y-3">
        {items.map((item, i) => (
          <div key={i} className="space-y-4 rounded-md border bg-muted/30 p-4">
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span className="font-medium">{field.item_label ?? t("Item")} {i + 1}</span>
              <div className="flex gap-1">
                <Button type="button" variant="ghost" size="icon" className="size-7" disabled={i === 0} onClick={() => move(i, -1)} title={t("Move up")}><ArrowUp /></Button>
                <Button type="button" variant="ghost" size="icon" className="size-7" disabled={i === items.length - 1} onClick={() => move(i, 1)} title={t("Move down")}><ArrowDown /></Button>
                <Button type="button" variant="ghost" size="icon" className="size-7" disabled={items.length <= (field.min_items ?? 0)} onClick={() => onChange(items.filter((_, j) => j !== i))} title={t("Remove")}><Trash2 /></Button>
              </div>
            </div>
            {subs.map((sub) => (
              <FieldInput key={sub.key} {...props} field={sub} value={item[sub.key]}
                onChange={(v) => onChange(items.map((it, j) => (j === i ? { ...it, [sub.key]: v } : it)))} />
            ))}
          </div>
        ))}
        <Button type="button" variant="outline" size="sm" disabled={items.length >= max} onClick={() => onChange([...items, emptyItem(subs)])}>
          <Plus />{t("Add")} {(field.item_label ?? t("item")).toLowerCase()}
        </Button>
      </div>
    </Field>
  );
}
