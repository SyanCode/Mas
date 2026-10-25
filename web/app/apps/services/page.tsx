"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { DomainPicker, type DomainChoice } from "@/components/DomainPicker";
import { Shell } from "@/components/Shell";
import { StackFields, defaults, type StackValues } from "@/components/StackFields";
import { Badge, Button, Card, ErrorText, Input, Label, PageHeader, Spinner } from "@/components/ui";
import { api, ApiError, type StackTemplate } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

const CATEGORIES: { id: StackTemplate["category"]; label: string }[] = [
  { id: "games", label: msg("Game servers") },
  { id: "mail", label: msg("Email") },
  { id: "web", label: msg("Tools with a web page") },
  { id: "other", label: msg("Other") },
];
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);

function Catalog({ templates, onPick }: { templates: StackTemplate[]; onPick: (t: StackTemplate) => void }) {
  const t = useT();
  return (
    <div className="space-y-8">
      {CATEGORIES.map((c) => {
        const list = templates.filter((x) => x.category === c.id);
        if (!list.length) return null;
        return (
          <section key={c.id} className="space-y-3">
            <h2 className="text-xs font-medium uppercase tracking-[0.18em] text-zinc-500">{t(c.label)}</h2>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {list.map((x) => (
                <button key={x.id} onClick={() => onPick(x)}
                  className="glass group flex flex-col gap-2 rounded-2xl p-5 text-left transition hover:-translate-y-0.5 hover:border-indigo-400/40">
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-semibold">{t(x.name)}</span>
                    <span className="mono text-[11px] text-zinc-500">≈ {x.ram_mb >= 1024 ? `${(x.ram_mb / 1024).toFixed(1)} GB` : `${x.ram_mb} MB`}</span>
                  </span>
                  <span className="text-sm text-zinc-500">{t(x.description)}</span>
                  <span className="mt-auto flex flex-wrap items-center gap-1.5 pt-2">
                    {x.web && <Badge tone="sky">HTTPS</Badge>}
                    {x.ports.map((p) => <Badge key={p.name}>{p.container}/{p.protocol}</Badge>)}
                    {x.image && <span className="mono text-[11px] text-zinc-500">{x.image}</span>}
                  </span>
                </button>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function Setup({ template, onBack }: { template: StackTemplate; onBack: () => void }) {
  const t = useT();
  const [id, setId] = useState(slug(template.id));
  const [values, setValues] = useState<StackValues>(() => defaults(template.fields));
  const [ports, setPorts] = useState<Record<string, number>>(() => Object.fromEntries(template.ports.map((p) => [p.name, p.container])));
  const wantsDomain = template.needs_domain || template.web || (template.id === "custom" && Number(values.web_port) > 0);
  const [withDomain, setWithDomain] = useState(true);
  const [choice, setChoice] = useState<DomainChoice | null>(null);
  const onDomain = useCallback((c: DomainChoice | null) => setChoice(c), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const domainOn = wantsDomain && (template.needs_domain || withDomain);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (domainOn && !choice) {
      setError(t("Pick a valid domain, or untick “give it an address”."));
      return;
    }
    setBusy(true);
    setError("");
    try {
      if (domainOn && choice?.zone) {
        await api("/dns/records", { method: "POST", body: { zone: choice.zone, subdomain: choice.subdomain, overwrite: choice.overwrite } });
      }
      const r = await api<{ id: string; release_id: number }>("/stacks", {
        method: "POST", body: { template: template.id, id, domain: domainOn ? choice?.domain : null, options: values, ports },
      });
      window.location.href = `/apps/view/?id=${encodeURIComponent(r.id)}&release=${r.release_id}`;
    } catch (err) {
      setError((err as ApiError).detail.replace(/^points-elsewhere: |^cname: /, ""));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <button type="button" onClick={onBack} className="text-sm text-zinc-500 hover:underline">← {t("Services")}</button>
      <PageHeader eyebrow={t("Deploy a service")} title={t(template.name)} subtitle={t(template.description)} />
      <Card title={t("Basics")}>
        <div className="grid gap-3 sm:grid-cols-2">
          <Label title={t("Name")} hint={t("Folder /srv/mas/stacks/{id}", { id: id || "…" })}>
            <Input value={id} onChange={(e) => setId(slug(e.target.value))} required />
          </Label>
        </div>
      </Card>
      {template.fields.length > 0 && (
        <Card title={t("Settings")}>
          <StackFields fields={template.fields} values={values} onChange={setValues} />
        </Card>
      )}
      {template.ports.length > 0 && (
        <Card title={t("Ports open to the internet")}>
          <div className="space-y-3 text-sm">
            {template.ports.map((p) => (
              <div key={p.name} className="flex flex-wrap items-center gap-3">
                <span className="min-w-[12rem] text-zinc-500">{t(p.label || p.name)}</span>
                {p.fixed
                  ? <code className="text-xs">{p.container}/{p.protocol}</code>
                  : <Input type="number" className="max-w-[8rem]" min={1} max={65535} value={ports[p.name]}
                      onChange={(e) => setPorts({ ...ports, [p.name]: Number(e.target.value) })} />}
                {!p.fixed && <span className="text-xs text-zinc-500">/{p.protocol}</span>}
              </div>
            ))}
            <p className="text-xs text-zinc-500">{t("Docker opens these ports to everyone, firewall included.")}</p>
          </div>
        </Card>
      )}
      {wantsDomain && (
        <Card title={t(template.domain_label)} actions={!template.needs_domain && (
          <label className="flex items-center gap-1.5 text-xs font-normal">
            <input type="checkbox" checked={withDomain} onChange={(e) => setWithDomain(e.target.checked)} /> {t("give it an address")}
          </label>
        )}>
          {domainOn ? <DomainPicker suggestion={template.id === "mail" ? "mail" : template.id} onChange={onDomain} />
            : <p className="text-sm text-zinc-500">{t("No address: the web page stays private (127.0.0.1).")}</p>}
          {template.id === "mail" && <p className="mt-2 text-xs text-zinc-500">{t("The address of the server itself, such as mail.example.com: it gets the HTTPS certificate used by IMAP and SMTP.")}</p>}
        </Card>
      )}
      <ErrorText>{error}</ErrorText>
      <div className="flex items-center gap-3">
        <Button variant="glow" className="!h-10 px-6" disabled={busy || !id}>{busy ? <Spinner /> : null}{t("Deploy")}</Button>
        <span className="text-xs text-zinc-500">≈ {template.ram_mb} MB RAM</span>
      </div>
    </form>
  );
}

function Services() {
  const t = useT();
  const [templates, setTemplates] = useState<StackTemplate[] | null>(null);
  const [picked, setPicked] = useState<StackTemplate | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<StackTemplate[]>("/stacks/templates").then(setTemplates).catch((e: ApiError) => setError(e.detail));
  }, []);

  if (picked) return <Setup template={picked} onBack={() => setPicked(null)} />;
  return (
    <div className="space-y-6">
      <Link href="/apps/" className="text-sm text-zinc-500 hover:underline">← {t("Apps")}</Link>
      <PageHeader eyebrow={t("Apps")} title={t("Deploy a service")}
        subtitle={t("Ready-made servers in a few clicks, run with Docker: Mas writes the configuration, opens the ports and gets the certificates.")} />
      <ErrorText>{error}</ErrorText>
      {!templates && !error && <div className="glass shimmer h-48 rounded-2xl" />}
      {templates && <Catalog templates={templates} onPick={setPicked} />}
    </div>
  );
}

export default function ServicesPage() {
  return (
    <Shell>
      <Services />
    </Shell>
  );
}
