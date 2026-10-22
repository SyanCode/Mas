"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { MaintenancePreview } from "@/components/MaintenancePreview";
import { Badge, Button, Card, ErrorText, Select, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { disableMaintenance, enableMaintenance, type MaintenanceOverview } from "@/lib/maintenance";

/** Put this app's site on hold, with one of the maintenance pages (the options live on /maintenance/). */
export function MaintenanceCard({ appId }: { appId: string }) {
  const t = useT();
  const [data, setData] = useState<MaintenanceOverview | null>(null);
  const [page, setPage] = useState("default");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => api<MaintenanceOverview>("/maintenance").then((d) => {
    setData(d);
    const st = d.apps.find((a) => a.id === appId)?.maintenance;
    if (st) setPage(st.page);
  }).catch((e) => setError((e as ApiError).detail)), [appId]);
  useEffect(() => { load(); }, [load]);

  const site = data?.apps.find((a) => a.id === appId);
  if (!site) return error ? <Card title={t("Maintenance")}><ErrorText>{error}</ErrorText></Card> : null;
  const st = site.maintenance;
  async function toggle() {
    setBusy(true);
    setError("");
    try {
      if (st) await disableMaintenance(appId);
      else await enableMaintenance(appId, page, data?.your_ip ? [data.your_ip] : [], null);
      await load();
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card title={t("Maintenance")} actions={st ? <Badge tone="amber">{t("in maintenance")}</Badge> : <Badge tone="emerald">{t("online")}</Badge>}>
      <div className="space-y-3 text-sm">
        {!site.supported ? <p className="text-xs text-zinc-500">{t("Its nginx site is not written by Mas: convert it to a Mas app first.")}</p> : <>
          {st ? (
            <p className="text-xs text-zinc-500">
              {t("Visitors get “{page}” since {when}.", { page: data?.pages.find((p) => p.id === st.page)?.name ?? st.page, when: timeAgo(st.since) })}
              {st.allow.length > 0 && ` ${t("Allowed: {ips}.", { ips: st.allow.join(", ") })}`}
              {st.until && ` ${t("Ends {date}.", { date: new Date(st.until).toLocaleString() })}`}
            </p>
          ) : (
            <Select value={page} onChange={(e) => setPage(e.target.value)} aria-label={t("Page")}>
              {data?.pages.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </Select>
          )}
          <MaintenancePreview pageId={page} className="h-32" />
          {!st && data?.your_ip && <p className="text-xs text-zinc-500">{t("You keep seeing the real site from this address ({ip}).", { ip: data.your_ip })}</p>}
          <ErrorText>{error}</ErrorText>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant={st ? "primary" : "secondary"} disabled={busy} onClick={toggle}>{busy ? <Spinner /> : null}{st ? t("Bring back") : t("Put in maintenance")}</Button>
            <Link href="/maintenance/" className="text-xs text-indigo-600 hover:underline dark:text-indigo-400">{t("Pages, allowed addresses, end time →")}</Link>
          </div>
        </>}
      </div>
    </Card>
  );
}
