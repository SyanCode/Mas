"use client";

import { useEffect, useState } from "react";
import { Check, X } from "lucide-react";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { formatDate, useDescribe } from "@/components/cms/common";
import { Avatar } from "@/components/cms/ui/avatar";
import { Card } from "@/components/cms/ui/card";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { api, type ApiError, type CmsActivity } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";

function History() {
  const t = useT();
  const { lang } = useI18n();
  const { site, session } = useCms();
  const describe = useDescribe();
  const [rows, setRows] = useState<CmsActivity[] | null>(null);
  const [error, setError] = useState("");
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
