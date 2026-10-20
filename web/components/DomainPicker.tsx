"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Input } from "@/components/ui";
import { api, type DnsCheck, type DnsStatus } from "@/lib/api";

export interface DomainChoice {
  zone: string | null; // OVH zone, or null for a domain typed by hand (DNS managed elsewhere)
  subdomain: string;
  domain: string;
  overwrite: boolean;
}

const LABELS = /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$/;
const DOMAIN = /^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}$/;

/** Pick `sub.zone` among the OVH zones (live DNS check), or type a domain by hand. */
export function DomainPicker({ suggestion, onChange }: { suggestion: string; onChange: (c: DomainChoice | null) => void }) {
  const [status, setStatus] = useState<DnsStatus | null>(null);
  const [zones, setZones] = useState<string[]>([]);
  const [zone, setZone] = useState("");
  const [sub, setSub] = useState(suggestion);
  const [manual, setManual] = useState(false);
  const [manualDomain, setManualDomain] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [check, setCheck] = useState<DnsCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [touched, setTouched] = useState(false);

  // Follow the suggestion (app id) until the user edits the subdomain.
  useEffect(() => {
    if (!touched) setSub(suggestion);
  }, [suggestion, touched]);

  useEffect(() => {
    api<DnsStatus>("/dns/status").then((s) => {
      setStatus(s);
      if (s.provider) {
        api<string[]>("/dns/zones").then((z) => {
          setZones(z);
          setZone((current) => current || z[0] || "");
        }).catch(() => setManual(true));
      } else {
        setManual(true);
      }
    }).catch(() => setManual(true));
  }, []);

  // Live check of the chosen name.
  useEffect(() => {
    setCheck(null);
    setOverwrite(false);
    if (manual || !zone || (sub && !LABELS.test(sub))) return;
    setChecking(true);
    const t = setTimeout(() => {
      api<DnsCheck>(`/dns/check?zone=${encodeURIComponent(zone)}&subdomain=${encodeURIComponent(sub)}`)
        .then(setCheck).catch(() => setCheck(null)).finally(() => setChecking(false));
    }, 400);
    return () => clearTimeout(t);
  }, [zone, sub, manual]);

  useEffect(() => {
    if (manual) {
      onChange(DOMAIN.test(manualDomain) ? { zone: null, subdomain: "", domain: manualDomain, overwrite: false } : null);
      return;
    }
    const blocked = !check || check.cname || !!check.used_by || (check.points_elsewhere && !overwrite);
    onChange(blocked || !check ? null : { zone, subdomain: sub, domain: check.domain, overwrite });
  }, [manual, manualDomain, zone, sub, check, overwrite, onChange]);

  if (!status) return <p className="text-sm text-zinc-500">Loading domains…</p>;

  const noIp = status.provider && !status.ipv4;

  return (
    <div className="space-y-2 text-sm">
      {!manual ? (
        <>
          <div className="flex flex-wrap items-center gap-1">
            <Input
              value={sub}
              onChange={(e) => {
                setTouched(true);
                setSub(e.target.value.trim().toLowerCase());
              }}
              placeholder="(root domain)"
              className="max-w-[12rem]"
            />
            <span className="text-zinc-500">.</span>
            <select value={zone} onChange={(e) => setZone(e.target.value)} className="rounded-md border border-zinc-300 bg-white px-2 py-2 dark:border-zinc-700 dark:bg-zinc-950">
              {zones.map((z) => <option key={z}>{z}</option>)}
            </select>
          </div>
          {noIp && (
            <p className="text-amber-700 dark:text-amber-400">
              Set this server&apos;s public IP first in <Link href="/settings/" className="underline">Settings → Domains</Link>.
            </p>
          )}
          {sub && !LABELS.test(sub) && <p className="text-red-600">Letters, digits and dashes only.</p>}
          {checking && <p className="text-zinc-500">Checking DNS…</p>}
          {check && !checking && (
            check.cname ? (
              <p className="text-red-600 dark:text-red-400">{check.domain} is a CNAME ({check.records[0]?.target}); change it in the OVH manager or pick another name.</p>
            ) : check.used_by ? (
              <p className="text-red-600 dark:text-red-400">{check.domain} is already used by {check.used_by}.</p>
            ) : check.points_elsewhere ? (
              <label className="flex items-start gap-2 text-amber-700 dark:text-amber-400">
                <input type="checkbox" checked={overwrite} onChange={(e) => setOverwrite(e.target.checked)} className="mt-1" />
                <span>{check.domain} currently points to {check.records.map((r) => `${r.type} ${r.target}`).join(", ")}. Replace it with this server?</span>
              </label>
            ) : check.points_here ? (
              <p className="text-emerald-700 dark:text-emerald-400">✓ {check.domain} already points to this server.</p>
            ) : (
              <p className="text-emerald-700 dark:text-emerald-400">✓ {check.domain} is free: Mas will create it ({status.ipv4}{status.ipv6 ? `, ${status.ipv6}` : ""}).</p>
            )
          )}
          <button type="button" onClick={() => setManual(true)} className="text-xs text-zinc-500 hover:underline">
            Domain managed elsewhere? Type it by hand
          </button>
        </>
      ) : (
        <>
          <Input value={manualDomain} onChange={(e) => setManualDomain(e.target.value.trim().toLowerCase())} placeholder="app.example.com" />
          <p className="text-xs text-zinc-500">
            Create the DNS record yourself (A → {status.ipv4 ?? "this server's IP"}).{" "}
            {status.provider ? (
              <button type="button" onClick={() => setManual(false)} className="underline">Pick an OVH domain instead</button>
            ) : (
              <>Or <Link href="/settings/" className="underline">connect OVH</Link> to have Mas create it.</>
            )}
          </p>
        </>
      )}
    </div>
  );
}
