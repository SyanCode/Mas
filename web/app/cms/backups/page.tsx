"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Archive, Download, RotateCcw, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { formatDate } from "@/components/cms/common";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { ConfirmDialog, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/cms/ui/dialog";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Switch } from "@/components/cms/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { api, uploadFile, type ApiError } from "@/lib/api";
import { useI18n } from "@/lib/i18n";

type Backup = { name: string; size: number; auto: boolean; created_at?: string; files?: number; rows?: number; content_error?: string | null; broken?: boolean };
type Listing = { items: Backup[]; auto: boolean };

const size = (n: number) => (n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

function Backups() {
  const { t, lang } = useI18n();
  const { site, session } = useCms();
  const [list, setList] = useState<Listing | null>(null);
  const [busy, setBusy] = useState("");
  const [restore, setRestore] = useState<Backup | null>(null);
  const [what, setWhat] = useState({ data: true, content: true });
  const [remove, setRemove] = useState<Backup | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/backups` : "";

  const load = useCallback(() => {
    if (base) api<Listing>(base).then(setList).catch((e: ApiError) => toast.error(e.detail));
  }, [base]);
  useEffect(() => { setList(null); load(); }, [load]);

  if (!site) return <NoSite />;
  if (session.role !== "admin") return <Card className="p-8 text-center text-sm text-muted-foreground">{t("Only administrators can make and restore backups.")}</Card>;

  const run = async (key: string, fn: () => Promise<unknown>, done: string) => {
    setBusy(key);
    try { await fn(); toast.success(done); load(); } catch (e) { toast.error((e as ApiError).detail); } finally { setBusy(""); }
  };

  return (
    <>
      <PageTitle title={t("Backups")} description={t("The content, the media and what Mas keeps about this site, in one file you can download.")}
        actions={<>
          <input ref={file} type="file" accept=".zip,application/zip" className="hidden" onChange={(e) => {
            const f = e.target.files?.[0]; e.target.value = "";
            if (f) run("upload", () => uploadFile(`${base}/upload`, f), t("Backup added to the list."));
          }} />
          <Button variant="outline" onClick={() => file.current?.click()} disabled={!!busy}><Upload />{t("Upload a backup")}</Button>
          <Button onClick={() => run("create", () => api(base, { method: "POST" }), t("Backup created."))} disabled={!!busy}><Archive />{busy === "create" ? t("Backing up…") : t("Back up now")}</Button>
        </>} />

      <Card className="mb-6">
        <CardContent className="flex items-center justify-between gap-4 p-5">
          <div>
            <p className="text-sm font-medium">{t("Back up every day")}</p>
            <p className="text-sm text-muted-foreground">{t("Mas keeps the 14 latest automatic backups.")}</p>
          </div>
          <Switch checked={!!list?.auto} disabled={!list} aria-label={t("Back up every day")}
            onCheckedChange={(on) => run("auto", () => api(`${base}/auto`, { method: "PUT", body: { on } }), on ? t("Daily backups on.") : t("Daily backups off."))} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>{t("Saved backups")}</CardTitle><CardDescription>{t("Before each restore, Mas saves the site as it is, so a restore can be undone.")}</CardDescription></CardHeader>
        <CardContent className="px-2 pb-2">
          {!list ? <Skeleton className="m-4 h-24" /> : (
            <Table>
              <TableHeader><TableRow><TableHead>{t("Date")}</TableHead><TableHead>{t("Content")}</TableHead><TableHead>{t("Size")}</TableHead><TableHead className="w-48" /></TableRow></TableHeader>
              <TableBody>
                {list.items.length === 0 && <TableRow><TableCell colSpan={4} className="py-10 text-center text-muted-foreground">{t("No backup yet.")}</TableCell></TableRow>}
                {list.items.map((b) => (
                  <TableRow key={b.name}>
                    <TableCell className="whitespace-nowrap">
                      {b.created_at ? formatDate(b.created_at, lang) : b.name} {b.auto && <Badge variant="secondary" className="ml-1">{t("auto")}</Badge>}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {b.broken ? <Badge variant="destructive">{t("Damaged")}</Badge>
                        : b.content_error ? <span title={b.content_error}>{t("Data only")} · {b.rows} {t("rows")}</span>
                        : <>{b.files} {t("files")} · {b.rows} {t("rows")}</>}
                    </TableCell>
                    <TableCell className="tabular-nums text-muted-foreground">{size(b.size)}</TableCell>
                    <TableCell className="text-right">
                      <a href={`/api${base}/${b.name}`} download aria-label={t("Download")} className="inline-grid size-9 place-items-center rounded-lg hover:bg-muted"><Download className="size-4" /></a>
                      <Button variant="ghost" size="icon" aria-label={t("Restore")} disabled={!!busy || b.broken} onClick={() => { setWhat({ data: true, content: !b.content_error }); setRestore(b); }}><RotateCcw /></Button>
                      <Button variant="ghost" size="icon" aria-label={t("Delete")} disabled={!!busy} onClick={() => setRemove(b)}><Trash2 /></Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!restore} onOpenChange={(o) => !o && setRestore(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("Restore this backup?")}</DialogTitle>
            <DialogDescription>{t("The site as it is now is saved first. Files are put back in one commit and the site is deployed again.")}</DialogDescription>
          </DialogHeader>
          <div className="space-y-3 text-sm">
            <label className="flex items-start gap-3"><input type="checkbox" className="mt-0.5" checked={what.content} disabled={!!restore?.content_error} onChange={(e) => setWhat({ ...what, content: e.target.checked })} />
              <span><span className="font-medium">{t("Content and media")}</span><br /><span className="text-muted-foreground">{t("Pages, entries and images. Files created since are kept.")}</span></span></label>
            <label className="flex items-start gap-3"><input type="checkbox" className="mt-0.5" checked={what.data} onChange={(e) => setWhat({ ...what, data: e.target.checked })} />
              <span><span className="font-medium">{t("Statistics and settings")}</span><br /><span className="text-muted-foreground">{t("Visits, goals, cookie proofs and settings of this site.")}</span></span></label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRestore(null)}>{t("Cancel")}</Button>
            <Button disabled={!what.data && !what.content || !!busy} onClick={() => {
              const b = restore!; setRestore(null);
              run("restore", () => api(`${base}/${b.name}/restore`, { method: "POST", body: what }), t("Backup restored."));
            }}>{t("Restore")}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog open={!!remove} title={t("Delete this backup?")} confirmLabel={t("Delete")} cancelLabel={t("Cancel")} destructive
        onCancel={() => setRemove(null)} onConfirm={() => { const b = remove!; setRemove(null); run("delete", () => api(`${base}/${b.name}`, { method: "DELETE" }), t("Backup deleted.")); }} />
    </>
  );
}

export default function BackupsPage() {
  return <CmsLayout><Backups /></CmsLayout>;
}
