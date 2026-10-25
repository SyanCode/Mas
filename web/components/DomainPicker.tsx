"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { isRootDomain } from "@/components/DomainStatus";
import { Input, Spinner } from "@/components/ui";
import { api, type DnsCheck, type DnsStatus, type DnsZone } from "@/lib/api";
import { useT } from "@/lib/i18n";

export interface DomainChoice {
  zone: string | null; // a connected zone, or null for a domain typed by hand (DNS managed elsewhere)
  subdomain: string;
  domain: string;
  overwrite: boolean;
}

const LABELS = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$/;
const DOMAIN = /^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/;

/** Pick `sub.zone` among the zones of the connected registrars (live DNS check), or type a domain. */
export function DomainPicker({ suggestion, onChange }: { suggestion: string; onChange: (c: DomainChoice | null) => void }) {
  const t = useT();
  const [status, setStatus] = useState<DnsStatus | null>(null);
  const [zones, setZones] = useState<DnsZone[]>([]);
  const [zone, setZone] = useState("");
  const [sub, setSub] = useState(suggestion);
  const [touched, setTouched] = useState(false);
  const [manual, setManual] = useState(false);
  const [manualDomain, setManualDomain] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [check, setCheck] = useState<DnsCheck | null>(null);
  const [checking, setChecking] = useState(false);

  useEffect(() => {
    if (!touched) setSub(suggestion);
  }, [suggestion, touched]);

  useEffect(() => {
    api<DnsStatus>("/dns/status").then((s) => {
      setStatus(s);
      if (!s.providers.length) return setManual(true);
      api<DnsZone[]>("/dns/zones").then((z) => {
        setZones(z);
        setZone((current) => current || z[0]?.zone || "");
        if (!z.length) setManual(true);
      }).catch(() => setManual(true));
    }).catch(() => setManual(true));
  }, []);

  useEffect(() => {
    setCheck(null);
    setOverwrite(false);
    if (manual || !zone || (sub && !LABELS.test(sub))) return;
    setChecking(true);
    const timer = setTimeout(() => {
      api<DnsCheck>(`/dns/check?zone=${encodeURIComponent(zone)}&subdomain=${encodeURIComponent(sub)}`)
        .then(setCheck).catch(() => setCheck(null)).finally(() => setChecking(false));
    }, 400);
    return () => clearTimeout(timer);
  }, [zone, sub, manual]);

  useEffect(() => {
    if (manual) {
      onChange(DOMAIN.test(manualDomain) ? { zone: null, subdomain: "", domain: manualDomain, overwrite: false } : null);
      return;
    }
    const blocked = !check || check.cname || !!check.used_by || (check.points_elsewhere && !overwrite);
    onChange(blocked || !check ? null : { zone, subdomain: sub, domain: check.domain, overwrite });
  }, [manual, manualDomain, zone, sub, check, overwrite, onChange]);

  if (!status) return <p className="text-sm text-zinc-500"><Spinner /> {t("Loading domains…")}</p>;

  const line = "flex items-start gap-2 rounded-lg border px-3 py-2 text-[13px]";

  return (
    <div className="space-y-3 text-sm">
      {!manual ? (
        <>
          <div className="flex items-stretch overflow-hidden rounded-lg border border-zinc-300 focus-within:border-indigo-400 focus-within:ring-4 focus-within:ring-indigo-500/15 dark:border-white/10">
            <input
              value={sub}
              onChange={(e) => { setTouched(true); setSub(e.target.value.trim().toLowerCase()); }}
              placeholder={t("(root)")}
              className="mono min-w-0 flex-1 bg-white/80 px-3 py-2 text-sm outline-none dark:bg-black/40"
            />
            <span className="flex items-center bg-zinc-100 px-1 text-zinc-500 dark:bg-white/[0.04]">.</span>
            <select value={zone} onChange={(e) => setZone(e.target.value)} className="mono max-w-[55%] bg-zinc-100 px-2 text-sm outline-none dark:bg-white/[0.04]">
              {zones.map((z) => <option key={z.zone} value={z.zone}>{z.zone} · {z.label}</option>)}
            </select>
          </div>
          {!status.ipv4 && (
            <p className={`${line} border-amber-400/30 bg-amber-400/10 text-amber-700 dark:text-amber-300`}>
              {t("Set this server's public IP first in")}&nbsp;<Link href="/settings/" className="underline">{t("Settings → Domains")}</Link>.
            </p>
          )}
          {sub && !LABELS.test(sub) && <p className="text-rose-500">{t("Letters, digits and dashes only.")}</p>}
          {checking && <p className="text-xs text-zinc-500"><Spinner /> {t("Checking DNS…")}</p>}
          {check && !checking && (
            check.cname ? (
              <p className={`${line} border-rose-400/30 bg-rose-500/10 text-rose-600 dark:text-rose-300`}>{t("{domain} is a CNAME ({target}); change it at {provider}.", { domain: check.domain, target: check.records[0]?.target ?? "", provider: check.provider })}</p>
            ) : check.used_by ? (
              <p className={`${line} border-rose-400/30 bg-rose-500/10 text-rose-600 dark:text-rose-300`}>{t("{domain} is already used by {app}.", { domain: check.domain, app: check.used_by })}</p>
            ) : check.points_elsewhere ? (
              <label className={`${line} cursor-pointer border-amber-400/30 bg-amber-400/10 text-amber-700 dark:text-amber-300`}>
                <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} className="mt-0.5" />
                <span>{t("{domain} points to {records}. Replace it with this server?", { domain: check.domain, records: check.records.map((r) => `${r.type} ${r.target}`).join(", ") })}</span>
              </label>
            ) : check.points_here ? (
              <p className={`${line} border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300`}>✓ {t("{domain} already points to this server.", { domain: check.domain })}</p>
            ) : (
              <p className={`${line} border-emerald-400/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300`}>
                ✓ {t("{domain} is free. Mas will create it at {provider} → {ips}.", { domain: check.domain, provider: check.provider, ips: `${status.ipv4}${status.ipv6 ? `, ${status.ipv6}` : ""}` })}
              </p>
            )
          )}
          {check && !checking && !check.cname && !check.used_by && isRootDomain(check.domain) && (
            <p className="text-xs text-zinc-500">{t("www.{domain} is added too: Mas creates its record and serves it like {domain}.", { domain: check.domain })}</p>
          )}
          <button type="button" onClick={() => setManual(true)} className="text-xs text-zinc-500 hover:underline">{t("DNS managed elsewhere? Type the domain")}</button>
        </>
      ) : (
        <>
          <Input value={manualDomain} onChange={(e) => setManualDomain(e.target.value.trim().toLowerCase())} placeholder="app.example.com" className="mono" />
          <p className="text-xs text-zinc-500">
            {t("Create the record yourself: A → {ip}.", { ip: status.ipv4 ?? t("this server's IP") })}{" "}
            {DOMAIN.test(manualDomain) && isRootDomain(manualDomain) && <>{t("And for www.{domain}: CNAME → {domain}.", { domain: manualDomain })}{" "}</>}
            {status.providers.length && zones.length ? (
              <button type="button" onClick={() => setManual(false)} className="underline">{t("Pick a connected domain instead")}</button>
            ) : (
              <>{t("Or")} <Link href="/settings/" className="underline">{t("connect your registrar")}</Link> {t("(OVH, Cloudflare, Gandi…) to have Mas create it.")}</>
            )}
          </p>
        </>
      )}
    </div>
  );
}

