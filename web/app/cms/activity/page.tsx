"use client";

import { useEffect, useState } from "react";
import { Check, Loader2, Undo2, X } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { formatDate, useDescribe } from "@/components/cms/common";
import { Avatar } from "@/components/cms/ui/avatar";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card, CardDescription, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { ConfirmDialog } from "@/components/cms/ui/dialog";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { api, type ApiError, type CmsActivity, type CmsChange } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";

function History() {
  const t = useT();
  const { lang } = useI18n();
  const { site, session } = useCms();
  const describe = useDescribe();
  const [rows, setRows] = useState<CmsActivity[] | null>(null);
  const [error, setError] = useState("");
  const [changes, setChanges] = useState<CmsChange[] | null>(null);
  const [undo, setUndo] = useState<CmsChange | null>(null);
  const [busy, setBusy] = useState(0);
  const loadChanges = () => { if (site) api<CmsChange[]>(`/cms/sites/${encodeURIComponent(site.id)}/changes`).then(setChanges).catch((e: ApiError) => setError(e.detail)); };
  useEffect(loadChanges, [site]); // eslint-disable-line react-hooks/exhaustive-deps

  async function revert(c: CmsChange) {
    setUndo(null); setBusy(c.id); setError("");
    try {
      await api(`/cms/sites/${encodeURIComponent(site!.id)}/changes/${c.id}/revert`, { method: "POST" });
      toast.success(t("Change undone. The site is being rebuilt."));
      loadChanges();
      api<CmsActivity[]>(`/cms/activity?app_id=${encodeURIComponent(site!.id)}&limit=200`).then(setRows);
    } catch (e) { toast.error((e as ApiError).detail); } finally { setBusy(0); }
  }

  useEffect(() => {
    if (!site) return;
    setRows(null);
    api<CmsActivity[]>(`/cms/activity?app_id=${encodeURIComponent(site.id)}&limit=200`).then(setRows).catch((e: ApiError) => setError(e.detail));
  }, [site]);

  if (!site) return <NoSite />;
  return (
    <>
      <PageTitle title={t("History")} description={t("Who changed what on {site}.", { site: site.name })} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      <Card className="mb-6">
        <CardHeader><CardTitle>{t("Changes you can undo")}</CardTitle><CardDescription>{t("Each change is a commit. Undoing adds a new commit that puts the previous content back; nothing is lost.")}</CardDescription></CardHeader>
        <Table>
          <TableHeader><TableRow><TableHead>{t("Who")}</TableHead><TableHead>{t("What")}</TableHead><TableHead>{t("When")}</TableHead><TableHead className="w-28" /></TableRow></TableHeader>
          <TableBody>
            {!changes && <TableRow><TableCell colSpan={4}><Skeleton className="h-8" /></TableCell></TableRow>}
            {changes?.length === 0 && <TableRow><TableCell colSpan={4} className="py-8 text-center text-muted-foreground">{t("Nothing yet.")}</TableCell></TableRow>}
            {changes?.slice(0, 15).map((c) => (
              <TableRow key={c.id}>
                <TableCell><span className="flex items-center gap-2"><Avatar name={c.actor} className="size-6 text-[10px]" />{c.actor}</span></TableCell>
                <TableCell className="max-w-md break-words"><span className="font-medium">{c.summary}</span> <span className="font-mono text-xs text-muted-foreground">{c.sha}</span></TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(c.at, lang)}</TableCell>
                <TableCell className="text-right">
                  {c.reverted ? <Badge variant="secondary">{t("Undone")}</Badge> : c.scope === "cms(revert)" ? <span className="text-xs text-muted-foreground">{t("An undo")}</span> : (
                    <Button variant="outline" size="sm" disabled={busy === c.id} onClick={() => setUndo(c)}>{busy === c.id ? <Loader2 className="animate-spin" /> : <Undo2 />}{t("Undo")}</Button>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      <ConfirmDialog open={!!undo} title={t("Undo “{name}”?", { name: undo?.summary ?? "" })} description={t("The site goes back to what it was before this change. It is refused if the same content was changed since.")}
        confirmLabel={t("Undo")} cancelLabel={t("Cancel")} onConfirm={() => undo && revert(undo)} onCancel={() => setUndo(null)} />
      <Card>
        <Table>
          <TableHeader><TableRow><TableHead>{t("Who")}</TableHead><TableHead>{t("What")}</TableHead><TableHead>{t("When")}</TableHead><TableHead className="w-10" /></TableRow></TableHeader>
          <TableBody>
            {!rows && <TableRow><TableCell colSpan={4}><Skeleton className="h-8" /></TableCell></TableRow>}
            {rows?.length === 0 && <TableRow><TableCell colSpan={4} className="py-12 text-center text-muted-foreground">{t("Nothing yet.")}</TableCell></TableRow>}
            {rows?.map((a) => (
              <TableRow key={a.id}>
                <TableCell><span className="flex items-center gap-2"><Avatar name={a.actor} className="size-6 text-[10px]" />{a.actor}</span></TableCell>
                <TableCell className="max-w-md break-words">{describe(a)}{!a.ok && a.detail && session.role === "admin" && <span className="block text-xs text-destructive">{a.detail}</span>}</TableCell>
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(a.at, lang)}</TableCell>
                <TableCell>{a.ok ? <Check className="size-4 text-emerald-500" /> : <X className="size-4 text-destructive" />}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </>
  );
}

export default function ActivityPage() {
  return <CmsLayout><History /></CmsLayout>;
}
