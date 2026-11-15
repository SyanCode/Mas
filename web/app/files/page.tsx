"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Shell } from "@/components/Shell";
import { Badge, Button, Card, ConfirmDialog, ErrorText, Input, Label, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, putFile, timeAgo } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Share {
  id: string; name: string; size: number; has_password: boolean; expires_at: number | null;
  max_downloads: number | null; downloads: number; ready: boolean; created_at: number; url_path: string;
}
interface Shares { shares: Share[]; free: number }

function size(n: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

const fullUrl = (s: Share) => `${window.location.origin}${s.url_path}`;

function Row({ s, onChange, onDelete }: { s: Share; onChange: () => void; onDelete: () => void }) {
  const t = useT();
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const expired = s.expires_at !== null && s.expires_at * 1000 <= Date.now();
  async function patch(body: object) {
    setError("");
    try {
      await api(`/shares/${s.id}`, { method: "PATCH", body });
      setEditing(false);
      setPassword("");
      onChange();
    } catch (e) {
      setError((e as ApiError).detail);
    }
  }
  return (
    <tr className="align-top">
      <td className="px-4 py-2">
        <div className="font-medium break-all">{s.name}</div>
        <div className="mono text-xs text-zinc-500">{size(s.size)}</div>
      </td>
      <td className="px-4 py-2">
        <div className="flex flex-wrap gap-1">
          {s.has_password && <Badge tone="amber">{t("Password protected")}</Badge>}
          {expired && <Badge tone="red">{t("Expired")}</Badge>}
          {s.expires_at !== null && !expired && <Badge>{t("Expires")} {new Date(s.expires_at * 1000).toLocaleDateString()}</Badge>}
        </div>
        <div className="mt-1 text-xs text-zinc-500">
          {s.downloads}{s.max_downloads ? ` / ${s.max_downloads}` : ""} {t("downloads")}
        </div>
      </td>
      <td className="px-4 py-2 text-zinc-500">{timeAgo(new Date(s.created_at * 1000).toISOString())}</td>
      <td className="px-4 py-2">
        <div className="flex flex-wrap justify-end gap-1.5">
          <Button onClick={() => { navigator.clipboard?.writeText(fullUrl(s)); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
            {copied ? t("Copied") : t("Copy link")}
          </Button>
          <Button onClick={() => setEditing(!editing)}>{t("Password")}</Button>
          <Button variant="danger" onClick={onDelete}>{t("Delete")}</Button>
        </div>
        {editing && (
          <div className="mt-2 space-y-2 text-left">
            <Input type="text" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t("New password (at least 6 characters)")} />
            <div className="flex gap-2">
              <Button variant="primary" disabled={password.length < 6} onClick={() => patch({ password })}>{t("Save")}</Button>
              {s.has_password && <Button onClick={() => patch({ remove_password: true })}>{t("Remove password")}</Button>}
            </div>
          </div>
        )}
        <ErrorText>{error}</ErrorText>
      </td>
    </tr>
  );
}

function Files() {
  const t = useT();
  const [data, setData] = useState<Shares | null>(null);
  const [error, setError] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [days, setDays] = useState("");
  const [limit, setLimit] = useState("");
  const [sent, setSent] = useState<number | null>(null);
  const [removing, setRemoving] = useState<Share | null>(null);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(() => api<Shares>("/shares").then(setData).catch((e: ApiError) => setError(e.detail)), []);
  useEffect(() => { load(); }, [load]);

  async function send() {
    if (!file) return;
    setError("");
    setSent(0);
    let created: Share | null = null;
    try {
      created = await api<Share>("/shares", { method: "POST", body: {
        name: file.name, size: file.size, password: password || null,
        expires_hours: days ? Math.round(Number(days) * 24) : null, max_downloads: limit ? Number(limit) : null } });
      await putFile(`/shares/${created.id}/content`, file, setSent);
      setFile(null); setPassword(""); setDays(""); setLimit("");
      if (input.current) input.current.value = "";
    } catch (e) {
      setError((e as ApiError).detail);
      if (created) await api(`/shares/${created.id}`, { method: "DELETE" }).catch(() => undefined);  // no half-sent entry
    } finally {
      setSent(null);
      load();
    }
  }

  async function remove(s: Share) {
    setRemoving(null);
    try {
      await api(`/shares/${s.id}`, { method: "DELETE" });
    } catch (e) {
      setError((e as ApiError).detail);
    }
    load();
  }

  const percent = file && sent !== null ? Math.min(100, Math.round((sent / file.size) * 100)) : 0;
  return (
    <div className="space-y-4">
      <PageHeader eyebrow={t("Hosting")} title={t("Files")}
        subtitle={t("Host a big file on this server and give anyone a download link, with an optional password.")} />
      <ErrorText>{error}</ErrorText>
      <Card title={t("Share a file")}>
        <div className="space-y-3">
          <Label title={t("File")} hint={data ? t("Free space for uploads: {size}", { size: size(data.free) }) : undefined}>
            <input ref={input} type="file" disabled={sent !== null} onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="block w-full text-sm" />
          </Label>
          <div className="grid gap-3 sm:grid-cols-3">
            <Label title={t("Password (optional)")}><Input type="text" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={t("No password")} /></Label>
            <Label title={t("Expires after (days)")}><Input type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} placeholder={t("Never")} /></Label>
            <Label title={t("Download limit")}><Input type="number" min={1} value={limit} onChange={(e) => setLimit(e.target.value)} placeholder={t("Unlimited")} /></Label>
          </div>
          {sent !== null && file && (
            <div className="space-y-1">
              <div className="h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800"><div className="h-full bg-indigo-500 transition-all" style={{ width: `${percent}%` }} /></div>
              <div className="text-xs text-zinc-500">{t("Uploading… {percent}% ({sent} of {total})", { percent: String(percent), sent: size(sent), total: size(file.size) })}</div>
            </div>
          )}
          <Button variant="primary" disabled={!file || sent !== null || (password.length > 0 && password.length < 6)} onClick={send}>
            {sent !== null ? <Spinner /> : null}{t("Upload and create the link")}
          </Button>
          {password.length > 0 && password.length < 6 && <p className="text-xs text-zinc-500">{t("The password needs at least 6 characters.")}</p>}
        </div>
      </Card>
      <div className="overflow-x-auto glass rounded-2xl">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-zinc-200 text-xs uppercase text-zinc-500 dark:border-zinc-800">
            <tr>
              <th className="px-4 py-2 font-medium">{t("File")}</th>
              <th className="px-4 py-2 font-medium">{t("Link")}</th>
              <th className="px-4 py-2 font-medium">{t("Created")}</th>
              <th className="px-4 py-2 text-right font-medium">{t("Actions")}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
            {(data?.shares ?? []).filter((s) => s.ready).map((s) => <Row key={s.id} s={s} onChange={load} onDelete={() => setRemoving(s)} />)}
            {data && !data.shares.some((s) => s.ready) && (
              <tr><td colSpan={4} className="px-4 py-8 text-center text-zinc-500">{t("No shared file yet.")}</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <ConfirmDialog open={!!removing} danger title={t("Delete this file?")}
        message={t("The link stops working and the file is deleted from the server.")} confirmLabel={t("Delete")}
        onConfirm={() => removing && remove(removing)} onCancel={() => setRemoving(null)} />
    </div>
  );
}

export default function FilesPage() {
  return (
    <Shell>
      <Files />
    </Shell>
  );
}
