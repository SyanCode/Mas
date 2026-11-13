"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import { ArrowDown, ArrowUp, KeyRound, Plus, Trash2 } from "lucide-react";
import { Badge, Button, ConfirmDialog, ErrorText, Input, Label, Select, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type AiAccount, type AiAccounts } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Claude, Codex and other accounts in order of priority: the next one takes over when one is at its limit. */
export function AiAccountsList({ onChange }: { onChange?: (accounts: AiAccount[]) => void }) {
  const t = useT();
  const [data, setData] = useState<AiAccounts | null>(null);
  const [adding, setAdding] = useState(false);
  const [provider, setProvider] = useState("anthropic");
  const [name, setName] = useState("");
  const [secret, setSecret] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [password, setPassword] = useState("");
  const [removing, setRemoving] = useState<AiAccount | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const apply = useCallback((d: AiAccounts) => { setData(d); onChange?.(d.accounts); }, [onChange]);
  useEffect(() => { api<AiAccounts>("/ai/accounts").then(apply).catch((e: ApiError) => setError(e.detail)); }, [apply]);
  useEffect(() => {  // limits reset by themselves: keep the badges honest
    const id = setInterval(() => api<AiAccounts>("/ai/accounts").then(apply).catch(() => {}), 30000);
    return () => clearInterval(id);
  }, [apply]);

  async function run(label: string, fn: () => Promise<AiAccounts>) {
    setBusy(label); setError("");
    try { apply(await fn()); return true; } catch (e) { setError((e as ApiError).detail); return false; } finally { setBusy(""); }
  }

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const ok = await run("add", () => api<AiAccounts>("/ai/accounts", { method: "POST", body: { name, provider, secret, model, base_url: baseUrl, password } }));
    if (ok) { setAdding(false); setName(""); setSecret(""); setModel(""); setBaseUrl(""); setPassword(""); }
  };
  const move = (a: AiAccount, direction: number) => run(`move-${a.id}`, () => api<AiAccounts>(`/ai/accounts/${a.id}/move`, { method: "POST", body: { direction } }));
  const toggle = (a: AiAccount) => run(`toggle-${a.id}`, () => api<AiAccounts>(`/ai/accounts/${a.id}`, { method: "PUT", body: { enabled: !a.enabled } }));

  if (!data) return error ? <ErrorText>{error}</ErrorText> : <Spinner />;
  const providers = data.providers;
  const label = (a: AiAccount) => a.provider === "anthropic" ? (a.kind === "claude-code" ? t("Claude Code token") : t("Claude API key")) : a.provider === "openai" ? t("OpenAI key (Codex)") : t("Compatible API");

  return (
    <div className="space-y-4 text-sm">
      <ErrorText>{error}</ErrorText>
      {data.accounts.length === 0 ? (
        <p className="text-zinc-500">{t("No account yet. Add a Claude Code token, an Anthropic or OpenAI key, or any compatible provider.")}</p>
      ) : (
        <>
          <p className="text-xs text-zinc-500">{t("Mas uses the first account of the list. When it reaches its limit, the next one takes over, and the first one is used again once it resets.")}</p>
          <ol className="stagger space-y-2">
            {data.accounts.map((a, i) => {
              const limited = a.limited_until && a.limited_until * 1000 > Date.now();
              return (
                <li key={a.id} className={`glass lift flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl p-3 ${a.enabled ? "" : "opacity-60"}`}>
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-zinc-900/10 text-xs font-semibold tabular-nums dark:bg-white/10">{i + 1}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="truncate font-medium">{a.name}</span>
                      <Badge tone="violet">{label(a)} {a.hint}</Badge>
                      {!a.enabled ? <Badge tone="red">{t("off")}</Badge> : limited ? <Badge tone="amber">{t("limit reached, back {when}", { when: new Date(a.limited_until! * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) })}</Badge> : i === 0 ? <Badge tone="emerald">{t("in use")}</Badge> : <Badge>{t("standby")}</Badge>}
                    </div>
                    <div className="mono mt-0.5 truncate text-[11px] text-zinc-500">{a.model}{a.last_used ? ` · ${t("used {when}", { when: timeAgo(new Date(a.last_used * 1000).toISOString()) })}` : ""}</div>
                    {a.error && !limited && <div className="mt-0.5 text-xs text-rose-500">{a.error}</div>}
                  </div>
                  <div className="flex items-center gap-1">
                    <Button variant="ghost" className="px-2" disabled={!!busy || i === 0} aria-label={t("Move up")} onClick={() => move(a, -1)}><ArrowUp className="size-3.5" /></Button>
                    <Button variant="ghost" className="px-2" disabled={!!busy || i === data.accounts.length - 1} aria-label={t("Move down")} onClick={() => move(a, 1)}><ArrowDown className="size-3.5" /></Button>
                    <Button variant="ghost" disabled={!!busy} onClick={() => toggle(a)}>{a.enabled ? t("Turn off") : t("Turn on")}</Button>
                    <Button variant="ghost" className="px-2 text-rose-500" aria-label={t("Delete")} onClick={() => setRemoving(a)}><Trash2 className="size-3.5" /></Button>
                  </div>
                </li>
              );
            })}
          </ol>
        </>
      )}
      {!adding ? (
        <Button variant="primary" onClick={() => setAdding(true)}><Plus className="size-3.5" />{t("Add an account")}</Button>
      ) : (
        <form onSubmit={add} className="animate-pop max-w-lg space-y-3 rounded-xl border border-zinc-200/80 p-4 dark:border-white/[0.06]">
          <Label title={t("Provider")}>
            <Select value={provider} onChange={(e) => { setProvider(e.target.value); setModel(""); }}>
              <option value="anthropic">{providers.anthropic?.label ?? "Claude"}</option>
              <option value="openai">{providers.openai?.label ?? "OpenAI"}</option>
              <option value="openai_compat">{providers.openai_compat?.label ?? t("OpenAI-compatible")} (Gemini, Mistral, OpenRouter…)</option>
            </Select>
          </Label>
          <Label title={t("Name")} hint={t("To tell your accounts apart, e.g. Claude Pro (work).")}>
            <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder={t("Claude Pro 1")} />
          </Label>
          <Label title={provider === "anthropic" ? t("Claude token or API key") : t("API key")}
            hint={provider === "anthropic" ? t("Run `claude setup-token` on your computer and paste the token here (it uses your Claude subscription), or paste an Anthropic API key.") : provider === "openai" ? t("An OpenAI API key (platform.openai.com). The ChatGPT subscription cannot be used through a key.") : t("The key of the provider.")}>
            <Input type="password" placeholder={provider === "anthropic" ? "sk-ant-…" : "sk-…"} value={secret} onChange={(e) => setSecret(e.target.value)} autoComplete="off" spellCheck={false} required />
          </Label>
          {provider === "openai_compat" && (
            <Label title={t("Base URL")} hint={t("Ends with /v1, e.g. https://openrouter.ai/api/v1")}>
              <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://…/v1" required />
            </Label>
          )}
          <Label title={t("Model")} hint={providers[provider]?.model ? t("Empty: {model}", { model: providers[provider].model }) : t("Required")}>
            <Input value={model} onChange={(e) => setModel(e.target.value)} maxLength={100} required={provider === "openai_compat"} />
          </Label>
          <Input type="password" placeholder={t("Your password")} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          <div className="flex gap-2">
            <Button variant="glow" type="submit" disabled={!!busy}>{busy === "add" ? <Spinner /> : <KeyRound className="size-3.5" />}{t("Connect")}</Button>
            <Button type="button" variant="ghost" onClick={() => setAdding(false)}>{t("Cancel")}</Button>
          </div>
        </form>
      )}
      <p className="text-xs text-zinc-500">{t("Keys are kept encrypted and never shown again. The AI only receives summaries and the files it asks for in the repositories of your sites: no environment variables, no credentials.")}</p>
      <ConfirmDialog open={!!removing} title={t("Remove this account?")} message={removing ? t("{name} is removed from the list. Its key is deleted from this server.", { name: removing.name }) : ""}
        confirmLabel={t("Remove")} danger onConfirm={() => { const a = removing!; setRemoving(null); run(`del-${a.id}`, () => api<AiAccounts>(`/ai/accounts/${a.id}`, { method: "DELETE" })); }} onCancel={() => setRemoving(null)} />
    </div>
  );
}
