"use client";

import { useState, type FormEvent } from "react";
import { Bot, KeyRound, Link2Off, Sparkles } from "lucide-react";
import { Badge, Button, ErrorText, Input, Label, Select, Spinner } from "@/components/ui";
import { api, ApiError, timeAgo, type AdvisorState } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Connect a Claude Code token (or an API key) so Claude proposes improvements on its own. */
export function AdvisorConnect({ state, onChange }: { state: AdvisorState; onChange: (s: Partial<AdvisorState>) => void }) {
  const t = useT();
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function run(label: string, fn: () => Promise<Partial<AdvisorState>>) {
    setBusy(label);
    setError("");
    try {
      onChange(await fn());
    } catch (e) {
      setError((e as ApiError).detail);
    } finally {
      setBusy("");
    }
  }

  const connect = (e: FormEvent) => {
    e.preventDefault();
    run("connect", async () => {
      const r = await api<AdvisorState>("/advisor", { method: "PUT", body: { token, password } });
      setToken(""); setPassword("");
      return r;
    });
  };
  const save = (body: Record<string, string>) => run("save", () => api<AdvisorState>("/advisor", { method: "PUT", body }));

  return (
    <div className="space-y-4 text-sm">
      <ErrorText>{error}</ErrorText>
      {state.connected ? (
        <div className="animate-pop space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="emerald"><Sparkles className="size-3" />{t("Connected")}</Badge>
            <Badge tone="violet">{state.kind === "claude-code" ? t("Claude Code token") : t("API key")} {state.hint}</Badge>
            <span className="text-xs text-zinc-500">
              {state.last_run ? t("last analysis {when}", { when: timeAgo(new Date(state.last_run * 1000).toISOString()) }) : t("no analysis yet")}
            </span>
          </div>
          {state.error && <ErrorText>{state.error}</ErrorText>}
          <div className="grid max-w-xl gap-3 sm:grid-cols-2">
            <Label title={t("Analyse")}>
              <Select value={state.interval} disabled={!!busy} onChange={(e) => save({ interval: e.target.value })}>
                <option value="daily">{t("every day")}</option>
                <option value="weekly">{t("every week")}</option>
                <option value="off">{t("only when I ask")}</option>
              </Select>
            </Label>
            <Label title={t("Model")}>
              <Input defaultValue={state.model} maxLength={80} onBlur={(e) => e.target.value !== state.model && save({ model: e.target.value })} />
            </Label>
          </div>
          <Button variant="ghost" disabled={!!busy} onClick={() => run("disconnect", () => api<AdvisorState>("/advisor", { method: "PUT", body: { token: "", password: "" } }))}>
            {busy === "disconnect" ? <Spinner /> : <Link2Off className="size-3.5" />}{t("Disconnect")}
          </Button>
        </div>
      ) : (
        <form onSubmit={connect} className="max-w-md space-y-3">
          <Label title={t("Claude token")} hint={t("Run `claude setup-token` on your computer and paste the token here (it uses your Claude subscription), or paste an Anthropic API key.")}>
            <Input type="password" placeholder="sk-ant-…" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" spellCheck={false} required />
          </Label>
          <Input type="password" placeholder={t("Your password")} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          <Button variant="glow" type="submit" disabled={!!busy || !token}>
            {busy === "connect" ? <Spinner /> : <KeyRound className="size-3.5" />}{t("Connect Claude")}
          </Button>
        </form>
      )}
      <p className="flex gap-2 text-xs text-zinc-500">
        <Bot className="mt-0.5 size-3.5 shrink-0" />
        <span>{t("Claude only receives a summary of statuses (services, health checks, resource advice). No logs, variables, files or credentials. It proposes, you decide: nothing is applied by itself. The token is kept encrypted.")}</span>
      </p>
    </div>
  );
}
