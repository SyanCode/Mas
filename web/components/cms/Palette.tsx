"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { Activity, Archive, BarChart3, Bell, ClipboardList, Cookie, FileSignature, FileText, HeartPulse, Image as ImageIcon, LayoutDashboard, Newspaper, Plus, ScanSearch, Search, ShieldCheck, Users } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/cms/ui/dialog";
import { api, type CmsCollection, type CmsOverview, type CmsSite } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

interface Item { key: string; label: string; hint?: string; extract?: string; icon: typeof Search; href: string }
interface Hit { kind: "page" | "entry"; cid: string; collection: string; slug: string; title: string; status: string | null; extract: string }

/** ⌘K / Ctrl+K: jump to a page, a collection, an entry, or start a new entry. */
export function Palette({ open, onClose, site, sites, overview, admin }: {
  open: boolean; onClose: () => void; site: CmsSite | null; sites: CmsSite[]; overview: CmsOverview | null; admin: boolean;
}) {
  const t = useT();
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [entries, setEntries] = useState<{ collection: CmsCollection; slug: string; title: string }[]>([]);
  const [hits, setHits] = useState<Hit[]>([]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery(""); setActive(0);
    if (site) {
      api<{ collections: CmsCollection[] }>(`/cms/sites/${encodeURIComponent(site.id)}`).then((s) =>
        setEntries(s.collections.flatMap((c) => c.entries.map((e) => ({ collection: c, slug: e.slug, title: String(e[c.title_field] ?? e.slug) })))),
      ).catch(() => setEntries([]));
    }
  }, [open, site]);

  // the words inside the content, not only the titles
  useEffect(() => {
    const q = query.trim();
    if (!open || !site || q.length < 3) { setHits([]); return; }
    let live = true;
    const timer = setTimeout(() => {
      api<Hit[]>(`/cms/sites/${encodeURIComponent(site.id)}/search?q=${encodeURIComponent(q)}`).then((h) => live && setHits(h)).catch(() => live && setHits([]));
    }, 250);
    return () => { live = false; clearTimeout(timer); };
  }, [query, open, site]);

  const items = useMemo<Item[]>(() => {
    if (!site) return [];
    const q = `app=${encodeURIComponent(site.id)}`;
    const list: Item[] = [
      { key: "dash", label: t("Dashboard"), icon: LayoutDashboard, href: `/cms/?${q}` },
      ...(overview?.collections ?? []).flatMap((c) => [
        { key: `c-${c.id}`, label: c.label, hint: t("Content"), icon: Newspaper, href: `/cms/content/?${q}&c=${encodeURIComponent(c.id)}` },
        { key: `n-${c.id}`, label: `${t("Add")} ${c.item_label.toLowerCase()}`, hint: c.label, icon: Plus, href: `/cms/entry/?${q}&c=${encodeURIComponent(c.id)}` },
      ]),
      ...(overview?.pages ?? []).map((p) => ({ key: `p-${p.id}`, label: p.label, hint: t("Pages"), icon: FileText, href: `/cms/pages/?${q}&p=${encodeURIComponent(p.id)}` })),
      ...entries.map((e) => ({ key: `e-${e.collection.id}-${e.slug}`, label: e.title, hint: e.collection.label, icon: ClipboardList, href: `/cms/entry/?${q}&c=${encodeURIComponent(e.collection.id)}&slug=${encodeURIComponent(e.slug)}` })),
      { key: "media", label: t("Media"), icon: ImageIcon, href: `/cms/media/?${q}` },
      { key: "stats", label: t("Statistics"), icon: BarChart3, href: `/cms/stats/?${q}` },
      { key: "up", label: t("Availability"), icon: HeartPulse, href: `/cms/uptime/?${q}` },
      { key: "seo", label: t("Search engines"), icon: ScanSearch, href: `/cms/seo/?${q}` },
      { key: "comp", label: t("Compliance"), icon: ShieldCheck, href: `/cms/compliance/?${q}` },
      { key: "legal", label: t("Legal pages"), icon: FileSignature, href: `/cms/legal/?${q}` },
      { key: "cook", label: t("Cookie banner"), icon: Cookie, href: `/cms/cookies/?${q}` },
      { key: "hist", label: t("History"), icon: Activity, href: `/cms/activity/?${q}` },
      ...(admin ? [{ key: "backups", label: t("Backups"), icon: Archive, href: `/cms/backups/?${q}` }] : []),
      ...(admin ? [{ key: "users", label: t("Users"), icon: Users, href: "/cms/users/" }, { key: "notif", label: t("Notifications"), icon: Bell, href: `/cms/notifications/?${q}` }] : []),
      ...sites.filter((s) => s.id !== site.id).map((s) => ({ key: `s-${s.id}`, label: s.name, hint: t("Switch site"), icon: LayoutDashboard, href: `/cms/?app=${encodeURIComponent(s.id)}` })),
    ];
    return list;
  }, [site, sites, overview, entries, admin, t]);

  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    const byTitle = items.filter((i) => words.every((w) => `${i.label} ${i.hint ?? ""}`.toLowerCase().includes(w))).slice(0, 30);
    const q = site ? `app=${encodeURIComponent(site.id)}` : "";
    const seen = new Set(byTitle.map((i) => i.href));
    const inside: Item[] = hits.map((h) => ({
      key: `h-${h.cid}-${h.slug}`, label: h.title, hint: h.collection || t("Pages"), extract: h.extract, icon: h.kind === "page" ? FileText : ClipboardList,
      href: h.kind === "page" ? `/cms/pages/?${q}&p=${encodeURIComponent(h.cid)}` : `/cms/entry/?${q}&c=${encodeURIComponent(h.cid)}&slug=${encodeURIComponent(h.slug)}`,
    })).filter((i) => !seen.has(i.href));
    return [...byTitle, ...inside];
  }, [items, query, hits, site, t]);
  useEffect(() => setActive(0), [query]);

  const go = (item: Item | undefined) => {
    if (!item) return;
    onClose();
    router.push(item.href);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="top-[20%] max-w-xl translate-y-0 gap-0 p-0" onOpenAutoFocus={(e) => { e.preventDefault(); input.current?.focus(); }}>
        <DialogTitle className="sr-only">{t("Search…")}</DialogTitle>
        <DialogDescription className="sr-only">{t("Jump to a page, an entry or an action.")}</DialogDescription>
        <div className="flex items-center gap-2 border-b px-4">
          <Search className="size-4 text-muted-foreground" />
          <input ref={input} aria-label={t("Search…")} value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("Search pages, entries, actions…")}
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, shown.length - 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
              else if (e.key === "Enter") { e.preventDefault(); go(shown[active]); }
            }} />
        </div>
        <ul className="max-h-80 overflow-y-auto p-1.5" role="listbox">
          {shown.length === 0 && <li className="px-3 py-8 text-center text-sm text-muted-foreground">{t("No result.")}</li>}
          {shown.map((i, idx) => (
            <li key={i.key} role="option" aria-selected={idx === active} onMouseEnter={() => setActive(idx)} onClick={() => go(i)}
              className={cn("flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm", idx === active && "bg-accent")}>
              <i.icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1"><span className="block truncate">{i.label}</span>{i.extract && <span className="block truncate text-xs text-muted-foreground">{i.extract}</span>}</span>
              {i.hint && <span className="truncate text-xs text-muted-foreground">{i.hint}</span>}
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  );
}
