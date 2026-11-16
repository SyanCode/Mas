"use client";

import { SITE_PERMS, type SitePerm } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type SiteAccess = Record<string, SitePerm[]>;

const PRESETS: { id: string; perms: SitePerm[] }[] = [
  { id: "stats", perms: ["stats"] },
  { id: "author", perms: ["content", "stats", "seo", "privacy"] },
  { id: "editor", perms: [...SITE_PERMS] },
];

/** Keeps the fixed order and the one rule: publishing needs the content. */
export function normalizePerms(perms: SitePerm[]): SitePerm[] {
  const set = new Set(perms);
  if (set.has("publish")) set.add("content");
  return SITE_PERMS.filter((p) => set.has(p));
}

export function usePermLabels() {
  const t = useT();
  const labels: Record<SitePerm, { label: string; hint: string }> = {
    stats: { label: t("Statistics"), hint: t("Visits, top pages, countries and availability") },
    content: { label: t("Content"), hint: t("Read pages and entries, write drafts, add media") },
    publish: { label: t("Publish"), hint: t("Put changes online, delete, undo") },
    seo: { label: t("Search engines"), hint: t("The SEO look at the site") },
    privacy: { label: t("Privacy"), hint: t("Compliance, legal pages and cookie banner") },
  };
  return labels;
}

/** Short words for a list: "Statistics only", "Editor", or the permissions themselves. */
export function usePermSummary() {
  const t = useT();
  const labels = usePermLabels();
  return (perms: SitePerm[]) => {
    const key = normalizePerms(perms).join(",");
    if (key === "stats") return t("Statistics only");
    if (key === SITE_PERMS.join(",")) return t("Full access");
    if (key === PRESETS[1].perms.join(",")) return t("Drafts only");
    return normalizePerms(perms).map((p) => labels[p].label).join(", ");
  };
}

/** One row per site: off, a preset, or permissions one by one. */
export function SitePermsPicker({ sites, value, onChange }: {
  sites: { id: string; name: string; domain?: string | null }[];
  value: SiteAccess;
  onChange: (next: SiteAccess) => void;
}) {
  const t = useT();
  const labels = usePermLabels();
  const set = (id: string, perms: SitePerm[]) => {
    const next = { ...value };
    const clean = normalizePerms(perms);
    if (clean.length) next[id] = clean;
    else delete next[id];
    onChange(next);
  };
  const presetOf = (perms: SitePerm[] | undefined) => {
    if (!perms?.length) return "none";
    return PRESETS.find((p) => p.perms.join(",") === normalizePerms(perms).join(","))?.id ?? "custom";
  };

  return (
    <div className="space-y-2">
      {sites.map((s) => {
        const perms = value[s.id];
        const preset = presetOf(perms);
        return (
          <div key={s.id} className="rounded-lg border border-zinc-200/80 p-3 dark:border-white/[0.08]">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-sm font-medium">
                {s.name}{s.domain && <span className="ml-2 text-xs font-normal text-zinc-500">{s.domain}</span>}
              </span>
              <select value={preset} aria-label={t("Access to {name}", { name: s.name })}
                className="h-8 rounded-md border border-zinc-300 bg-transparent px-2 text-xs dark:border-white/15"
                onChange={(e) => {
                  const p = PRESETS.find((x) => x.id === e.target.value);
                  if (e.target.value === "none") set(s.id, []);
                  else if (p) set(s.id, p.perms);
                }}>
                <option value="none">{t("No access")}</option>
                <option value="stats">{t("Statistics only")}</option>
                <option value="author">{t("Author: writes drafts, an editor publishes")}</option>
                <option value="editor">{t("Editor: writes and publishes")}</option>
                {preset === "custom" && <option value="custom">{t("Custom")}</option>}
              </select>
            </div>
            {perms && perms.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5">
                {SITE_PERMS.map((p) => (
                  <label key={p} title={labels[p].hint} className="flex items-center gap-1.5 text-xs">
                    <input type="checkbox" checked={perms.includes(p)}
                      disabled={p === "content" && perms.includes("publish")}
                      onChange={(e) => set(s.id, e.target.checked ? [...perms, p] : perms.filter((x) => x !== p))} />
                    {labels[p].label}
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}
      {sites.length === 0 && <p className="text-sm text-zinc-500">{t("No app linked to GitHub yet.")}</p>}
    </div>
  );
}
