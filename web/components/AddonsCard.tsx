"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge, Button, Card, ErrorText, Input, Select, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Addon { id: string; name: string; description: string; icon: string; source: "modrinth" | "spiget"; url: string; downloads?: number }
interface Selected { server_type: string; version: string; searchable: boolean; selected: Addon[] }

const key = (a: Addon) => `${a.source}:${a.id}`;
const compact = (n = 0) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}k` : String(n));

function Icon({ addon }: { addon: Addon }) {
  return addon.icon
    // eslint-disable-next-line @next/next/no-img-element
    ? <img src={addon.icon} alt="" className="h-9 w-9 shrink-0 rounded-lg bg-zinc-200/60 object-cover dark:bg-white/10" />
    : <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-zinc-200/60 text-xs font-semibold dark:bg-white/10">{addon.name.slice(0, 2).toUpperCase()}</span>;
}

function Row({ addon, children }: { addon: Addon; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-3">
      <Icon addon={addon} />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          {addon.url ? <a href={addon.url} target="_blank" rel="noreferrer" className="truncate text-sm font-medium hover:underline">{addon.name}</a>
            : <span className="truncate text-sm font-medium">{addon.name}</span>}
          {addon.downloads ? <span className="text-[11px] text-zinc-500">↓ {compact(addon.downloads)}</span> : null}
        </span>
        <span className="block truncate text-xs text-zinc-500">{addon.description}</span>
      </span>
      {children}
    </li>
  );
}

/** Mods and plugins of a Minecraft server: pick them by search, then apply once to reload the server. */
export function AddonsCard({ appId, onApply }: { appId: string; onApply: (modrinth: string[], spiget: string[]) => Promise<void> }) {
  const t = useT();
  const base = `/apps/${encodeURIComponent(appId)}/stack/addons`;
  const [data, setData] = useState<Selected | null>(null);
  const [picked, setPicked] = useState<Addon[]>([]);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<"modrinth" | "spiget">("modrinth");
  const [hits, setHits] = useState<Addon[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<Selected>(base).then((r) => { setData(r); setPicked(r.selected); }).catch((e: ApiError) => setError(e.detail));
  }, [base]);
  useEffect(load, [load]);

  async function search(e?: React.FormEvent) {
    e?.preventDefault();
    setSearching(true);
    setError("");
    try {
      setHits((await api<{ hits: Addon[] }>(`${base}/search?q=${encodeURIComponent(query)}&source=${source}`)).hits);
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setSearching(false);
    }
  }
  // The first look shows what is popular for this server.
  useEffect(() => { if (data?.searchable && hits === null) void search(); }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  async function apply() {
    setApplying(true);
    setError("");
    try {
      await onApply(picked.filter((a) => a.source === "modrinth").map((a) => a.id), picked.filter((a) => a.source === "spiget").map((a) => a.id));
    } catch (err) {
      setError((err as ApiError).detail);
    } finally {
      setApplying(false);
    }
  }

  if (!data) return error ? <Card title={t("Mods and plugins")}><ErrorText>{error}</ErrorText></Card> : null;
  const chosen = new Set(picked.map(key));
  const before = new Set(data.selected.map(key));
  const changes = picked.filter((a) => !before.has(key(a))).length + data.selected.filter((a) => !chosen.has(key(a))).length;
  const kind = ["PAPER", "PURPUR", "FOLIA", "SPIGOT"].includes(data.server_type) ? t("plugins") : t("mods");

  return (
    <Card title={t("Mods and plugins")}
      actions={<Button variant="primary" disabled={applying || changes === 0} onClick={apply}>{applying ? <Spinner /> : null}{changes ? t("Apply and reload ({n})", { n: changes }) : t("Apply and reload")}</Button>}>
      <div className="space-y-4 text-sm">
        {!data.searchable ? (
          <p className="text-xs text-zinc-500">{t("This server type has no catalog search. Use the settings below to add links or a modpack.")}</p>
        ) : (
          <>
            <p className="text-xs text-zinc-500">{t("Search, add what you want, then apply: the server restarts once with your choices. Files are downloaded at start. The world is kept.")}</p>
            <div>
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">{t("Selected")} ({picked.length})</h3>
              <ul className="space-y-2">
                {picked.map((a) => (
                  <Row key={key(a)} addon={a}>
                    {!before.has(key(a)) && <Badge tone="emerald">{t("new")}</Badge>}
                    <Button variant="ghost" onClick={() => setPicked(picked.filter((p) => key(p) !== key(a)))}>{t("Remove")}</Button>
                  </Row>
                ))}
                {picked.length === 0 && <li className="text-xs text-zinc-500">{t("Nothing selected yet.")}</li>}
              </ul>
              {data.selected.filter((a) => !chosen.has(key(a))).length > 0 && <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">{t("Removed entries stop being downloaded; their files already in the folder stay until you delete them.")}</p>}
            </div>
            <form onSubmit={search} className="flex flex-wrap gap-2">
              <Select className="max-w-[9rem]" value={source} onChange={(e) => { setSource(e.target.value as "modrinth" | "spiget"); setHits(null); }}>
                <option value="modrinth">Modrinth</option>
                <option value="spiget">SpigotMC</option>
              </Select>
              <Input className="min-w-[10rem] flex-1" value={query} maxLength={80} placeholder={t("Search {kind}…", { kind })} onChange={(e) => setQuery(e.target.value)} />
              <Button type="submit" disabled={searching}>{searching ? <Spinner /> : null}{t("Search")}</Button>
            </form>
            <p className="text-[11px] text-zinc-500">{data.version === "LATEST" ? t("All Minecraft versions.") : t("Compatible with Minecraft {version}.", { version: data.version })}</p>
            <ErrorText>{error}</ErrorText>
            <ul className="space-y-2">
              {hits?.map((a) => (
                <Row key={key(a)} addon={a}>
                  {chosen.has(key(a))
                    ? <Badge tone="emerald">{t("Selected")}</Badge>
                    : <Button onClick={() => setPicked([...picked, a])}>{t("Add")}</Button>}
                </Row>
              ))}
              {hits?.length === 0 && <li className="text-xs text-zinc-500">{t("No result.")}</li>}
            </ul>
          </>
        )}
      </div>
    </Card>
  );
}
