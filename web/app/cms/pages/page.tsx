"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { FieldInput, type Previews, type Values } from "@/components/cms/fields";
import { PublishBanner, usePublisher, type Saved } from "@/components/cms/publish";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent } from "@/components/cms/ui/card";
import { Input } from "@/components/cms/ui/input";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { api, type ApiError, type CmsPage } from "@/lib/api";
import { useT } from "@/lib/i18n";

function PageEditor() {
  const t = useT();
  const pid = useSearchParams().get("p") ?? "";
  const { site } = useCms();
  const publisher = usePublisher(site);
  const [page, setPage] = useState<CmsPage | null>(null);
  const [values, setValues] = useState<Values>({});
  const [previews, setPreviews] = useState<Previews>({});
  const [note, setNote] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    if (!site) return;
    api<{ pages: CmsPage[] }>(`/cms/sites/${encodeURIComponent(site.id)}`).then((s) => {
      const p = s.pages.find((x) => x.id === pid) ?? null;
      setPage(p);
      setValues(p?.values ?? {});
      if (!p) setError(t("Unknown page."));
    }).catch((e: ApiError) => setError(e.detail));
  }, [site, pid, t]);
  useEffect(() => { setPage(null); load(); }, [load]);

  const dirty = !!page && JSON.stringify(values) !== JSON.stringify(page.values);
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function save() {
    if (!page || !site) return;
    const ok = await publisher.run(() => api<Saved>(`/cms/sites/${encodeURIComponent(site.id)}/pages/${encodeURIComponent(page.id)}`,
      { method: "PUT", body: { values, sha: page.sha, note } }), t("Published."));
    if (ok) { setNote(""); load(); }
  }

  if (!site) return <NoSite />;
  return (
    <>
      <PageTitle title={page?.label ?? "…"} description={page?.description} />
      <PublishBanner state={publisher.state} site={site} />
      {(error || publisher.error) && <p className="mb-4 text-sm text-destructive">{error || publisher.error}</p>}
      {!page ? <Skeleton className="h-96" /> : (
        <form onSubmit={(e) => { e.preventDefault(); save(); }}>
          <Card><CardContent className="space-y-6 p-6">
            {page.fields.map((f) => (
              <FieldInput key={f.key} appId={site.id} field={f} value={values[f.key]} previews={previews}
                addPreview={(k, url) => setPreviews((p) => ({ ...p, [k]: url }))}
                onChange={(v) => setValues((cur) => ({ ...cur, [f.key]: v }))} />
            ))}
          </CardContent></Card>
          <div className="sticky bottom-4 z-10 mt-4 flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3 shadow-md">
            <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={100} placeholder={t("What did you change? (optional)")} className="min-w-[12rem] flex-1" />
            {dirty && <Button type="button" variant="ghost" onClick={() => setValues(page.values)}>{t("Discard")}</Button>}
            <Button disabled={!dirty || publisher.busy}>{publisher.busy && <Loader2 className="animate-spin" />}{t("Publish")}</Button>
          </div>
        </form>
      )}
    </>
  );
}

export default function PagesPage() {
  return <CmsLayout><PageEditor /></CmsLayout>;
}
