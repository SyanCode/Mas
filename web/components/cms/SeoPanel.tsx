"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Circle, Sparkles } from "lucide-react";
import { Button } from "@/components/cms/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/cms/ui/card";
import { Input } from "@/components/cms/ui/input";
import type { CmsCollection, CmsField } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type Values = Record<string, unknown>;
const DESC_KEY = /^(meta_?|seo_?)?(description|excerpt|summary|resume|résumé|intro|chapo)$/i;

const plain = (md: string) => md.replace(/```[\s\S]*?```/g, " ").replace(/!\[[^\]]*\]\([^)]*\)/g, " ").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
  .replace(/^[#>\-*\d.)\s]+/gm, "").replace(/[*_`]/g, "").replace(/\s+/g, " ").trim();

function cut(text: string, limit: number): string {
  if (text.length <= limit) return text;
  const head = text.slice(0, limit);
  const stop = Math.max(head.lastIndexOf(". "), head.lastIndexOf("! "), head.lastIndexOf("? "));
  if (stop >= 50) return head.slice(0, stop + 1);
  return head.slice(0, head.lastIndexOf(" ")).replace(/[ ,;:\-–|]+$/, "");
}

const words = (s: string) => (s.trim() ? s.trim().split(/\s+/).length : 0);
const norm = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();

/** Search preview and a few checks while writing an entry; fills the description from the text on request. */
export function SeoPanel({ collection, values, slug, url, onChange }: {
  collection: CmsCollection; values: Values; slug: string; url: string | null; onChange: (key: string, value: string) => void;
}) {
  const t = useT();
  const [keyword, setKeyword] = useState("");
  const f = useMemo(() => {
    const fields: CmsField[] = collection.fields;
    const title = fields.find((x) => x.key === collection.title_field);
    const desc = fields.find((x) => x.key !== collection.title_field && DESC_KEY.test(x.key) && (x.type === "text" || x.type === "textarea" || x.type === "markdown"))
      ?? fields.find((x) => x.type === "textarea" && x.key !== collection.title_field);
    const body = fields.find((x) => x.type === "markdown" && x.key !== desc?.key);
    return { title, desc, body };
  }, [collection]);

  const title = String(values[collection.title_field] ?? "").trim();
  const desc = f.desc ? String(values[f.desc.key] ?? "").trim() : "";
  const body = f.body ? plain(String(values[f.body.key] ?? "")) : "";
  const firstWords = body.split(" ").slice(0, 100).join(" ");
  const kw = norm(keyword.trim());
  const suggestion = body ? cut(body, 155) : "";
  const path = slug ? `/${slug}` : "";

  type Check = { ok: boolean | null; text: string };
  const checks: Check[] = [
    { ok: title.length === 0 ? null : title.length >= 15 && title.length <= 60, text: t("Title of 15 to 60 characters ({n})", { n: title.length }) },
    ...(f.desc ? [{ ok: desc.length === 0 ? false : desc.length >= 50 && desc.length <= 160, text: t("Description of 50 to 160 characters ({n})", { n: desc.length }) }] : []),
    ...(f.body ? [{ ok: body.length === 0 ? null : words(body) >= 300, text: t("At least 300 words ({n})", { n: words(body) }) }] : []),
    ...(f.body ? [{ ok: body.length === 0 ? null : /^#{2,3}\s/m.test(String(values[f.body.key] ?? "")), text: t("Subheadings to structure the text") }] : []),
    ...(slug ? [{ ok: slug.length <= 60, text: t("Short address ({n} characters)", { n: slug.length }) }] : []),
    ...(kw ? [
      { ok: norm(title).includes(kw), text: t("The keyword is in the title") },
      ...(f.desc ? [{ ok: norm(desc).includes(kw), text: t("The keyword is in the description") }] : []),
      ...(f.body ? [{ ok: norm(firstWords).includes(kw), text: t("The keyword is in the first 100 words") }] : []),
    ] : []),
  ];
  const applicable = checks.filter((c) => c.ok !== null);
  const score = applicable.length ? Math.round((applicable.filter((c) => c.ok).length / applicable.length) * 100) : 0;
  const shownTitle = title || t("Title of the page");
  const shownDesc = desc || cut(body, 155) || t("No description: the engine picks a sentence of the page.");

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 pb-3">
        <CardTitle>{t("Search engines")}</CardTitle>
        <span className={cn("text-sm font-medium tabular-nums", score >= 80 ? "text-emerald-600" : score >= 50 ? "text-amber-600" : "text-red-500")}>{score}/100</span>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="rounded-lg bg-muted/50 p-3">
          <div className="truncate text-[15px] text-[#1a0dab] dark:text-sky-400">{shownTitle.length > 60 ? `${shownTitle.slice(0, 58)}…` : shownTitle}</div>
          <div className="truncate text-xs text-emerald-700 dark:text-emerald-400">{(url ?? "https://your-site").replace(/^https?:\/\//, "")}{path}</div>
          <div className="line-clamp-2 text-[13px] text-muted-foreground">{shownDesc.length > 160 ? `${shownDesc.slice(0, 158)}…` : shownDesc}</div>
        </div>
        <Input value={keyword} onChange={(e) => setKeyword(e.target.value)} placeholder={t("Focus keyword (optional)")} aria-label={t("Focus keyword (optional)")} />
        <ul className="space-y-1.5">
          {checks.map((c) => (
            <li key={c.text} className="flex items-start gap-2 text-[13px]">
              {c.ok === null ? <Circle className="mt-0.5 size-3.5 shrink-0 text-muted-foreground/50" /> : c.ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-500" /> : <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />}
              <span className={cn(c.ok === null && "text-muted-foreground")}>{c.text}</span>
            </li>
          ))}
        </ul>
        {f.desc && suggestion.length >= 50 && suggestion !== desc && (
          <Button type="button" variant="outline" size="sm" onClick={() => onChange(f.desc!.key, suggestion)}><Sparkles />{desc ? t("Rewrite the description from the text") : t("Write the description from the text")}</Button>
        )}
        {!f.desc && <p className="text-xs text-muted-foreground">{t("Add a description field to this collection in mas.cms.json to write the text search engines show.")}</p>}
      </CardContent>
    </Card>
  );
}
