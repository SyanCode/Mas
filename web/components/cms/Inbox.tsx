"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useDescribe } from "@/components/cms/common";
import { InboxBell, type InboxItem } from "@/components/cms/premium";
import { api, timeAgo, type CmsActivity, type CmsSite, type ComplianceReport, type UptimeReport } from "@/lib/api";
import { useT } from "@/lib/i18n";

const KEY = (site: string) => `mas-cms-inbox:${site}`;

/** What deserves a look on the selected site: recent changes, a failing audit, an outage. Read marks stay in the browser. */
export function SiteInbox({ site }: { site: CmsSite }) {
  const t = useT();
  const describe = useDescribe();
  const [items, setItems] = useState<Omit<InboxItem, "read">[]>([]);
  const [read, setRead] = useState<Set<string>>(new Set());

  useEffect(() => {
    try { setRead(new Set(JSON.parse(localStorage.getItem(KEY(site.id)) ?? "[]"))); } catch { setRead(new Set()); }
  }, [site.id]);

  const load = useCallback(async () => {
    const id = encodeURIComponent(site.id);
    const [activity, compliance, uptime] = await Promise.all([
      api<CmsActivity[]>(`/cms/activity?app_id=${id}&limit=8`).catch(() => [] as CmsActivity[]),
      site.domain ? api<ComplianceReport>(`/cms/sites/${id}/compliance`).catch(() => null) : Promise.resolve(null),
      site.domain ? api<UptimeReport>(`/cms/sites/${id}/uptime?days=1`).catch(() => null) : Promise.resolve(null),
    ]);
    const out: Omit<InboxItem, "read">[] = [];
    if (uptime && uptime.up_now === false) out.push({ id: `down-${uptime.last_check}`, title: t("The site is down"), body: site.domain ?? "", time: uptime.last_check ? timeAgo(uptime.last_check) : "" });
    const audit = compliance?.latest;
    if (audit && audit.verdict !== "ok") {
      out.push({ id: `audit-${audit.id}`, title: audit.verdict === "blocking" ? t("Blocking compliance issues") : t("Compliance points to improve"),
        body: `${audit.score}/100 · ${t("{n} blocking", { n: audit.counts.critical })}`, time: timeAgo(audit.at) });
    }
    for (const a of activity) out.push({ id: `act-${a.id}`, title: describe(a), body: a.actor, time: timeAgo(a.at) });
    setItems(out);
  }, [site.id, site.domain, t, describe]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 120_000);
    return () => clearInterval(timer);
  }, [load]);

  const save = (next: Set<string>) => { setRead(next); try { localStorage.setItem(KEY(site.id), JSON.stringify([...next].slice(-200))); } catch {} };
  const shown = useMemo<InboxItem[]>(() => items.map((i) => ({ ...i, read: read.has(i.id) })), [items, read]);

  return <InboxBell items={shown} onMarkRead={(id) => save(new Set(read).add(id))} onMarkAllRead={() => save(new Set([...read, ...items.map((i) => i.id)]))} />;
}
