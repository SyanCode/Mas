"use client";

import { useEffect, useState } from "react";
import { Check, Play, Sparkles, X } from "lucide-react";
import { Button, Spinner, Textarea } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Question { question: string; options: string[] }
interface Refined { ready: boolean; questions: Question[]; goal: string; why: string }
interface Answer { question: string; answer: string }

/**
 * Auto-prompting: before the robot starts, Claude looks at the request. Too vague: it asks one to three questions
 * (suggested answers, or type your own). Then it writes a precise goal (site, skills, checks, what must not change,
 * when it is done) that you can edit before starting it, or you start your request as typed.
 */
export function PromptCoach({ request, appId, onStart, onCancel, compact = false }: {
  request: string; appId: string; onStart: (goal: string) => void; onCancel: () => void; compact?: boolean;
}) {
  const t = useT();
  const [result, setResult] = useState<Refined | null>(null);
  const [goal, setGoal] = useState("");
  const [picked, setPicked] = useState<Record<number, string>>({});
  const [history, setHistory] = useState<Answer[]>([]);
  const [rounds, setRounds] = useState(0);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");

  const ask = async (answers: Answer[], round: number) => {
    setBusy(true); setError("");
    try {
      const r = await api<Refined>("/robot/refine", { method: "POST", body: { goal: request, app_id: appId, answers, rounds: round } });
      setResult(r); setGoal(r.goal); setPicked({});
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    } finally { setBusy(false); }
  };
  useEffect(() => { ask([], 0); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const answer = () => {
    const given = (result?.questions ?? []).map((q, i) => ({ question: q.question, answer: (picked[i] ?? "").trim() })).filter((a) => a.answer);
    const all = [...history, ...given];
    setHistory(all); setRounds(rounds + 1);
    ask(all, rounds + 1);
  };

  const small = compact ? "text-xs" : "text-sm";
  return (
    <div className={`space-y-3 ${small}`} aria-live="polite">
      <div className="flex items-start gap-2">
        <Sparkles className="mt-0.5 size-4 shrink-0 text-indigo-400" />
        <p className="text-zinc-600 dark:text-zinc-300">
          {busy ? t("Claude is reading your request…") : result && !result.ready ? t("A few details make the result much better:") : t("Here is your request, written for the robot. Edit it if you like, then start.")}
        </p>
      </div>
      {busy && <div className="flex items-center gap-2 text-xs text-zinc-500"><Spinner />{t("Preparing the goal…")}</div>}
      {error && (
        <div className="space-y-2">
          <p className="rounded-lg bg-rose-500/10 px-2.5 py-1.5 text-xs text-rose-600 dark:text-rose-300">{error}</p>
          <div className="flex gap-2">
            <Button variant="primary" onClick={() => onStart(request)}><Play className="size-3.5" />{t("Start as typed")}</Button>
            <Button variant="ghost" onClick={onCancel}>{t("Cancel")}</Button>
          </div>
        </div>
      )}

      {!busy && result && !result.ready && (
        <div className="space-y-3">
          {result.questions.map((q, i) => (
            <fieldset key={i} className="space-y-1.5">
              <legend className="font-medium">{q.question}</legend>
              <div className="flex flex-wrap gap-1.5">
                {q.options.map((o) => (
                  <button key={o} type="button" aria-pressed={picked[i] === o} onClick={() => setPicked({ ...picked, [i]: picked[i] === o ? "" : o })}
                    className={`rounded-full border px-2.5 py-1 text-xs transition ${picked[i] === o ? "border-indigo-500 bg-indigo-500 text-white" : "border-zinc-300 hover:border-indigo-400 dark:border-white/15"}`}>
                    {picked[i] === o && <Check className="-ml-0.5 mr-1 inline size-3" />}{o}
                  </button>
                ))}
              </div>
              <input value={q.options.includes(picked[i] ?? "") ? "" : picked[i] ?? ""} onChange={(e) => setPicked({ ...picked, [i]: e.target.value })}
                placeholder={t("Or your own answer…")} aria-label={`${q.question} ${t("Or your own answer…")}`} maxLength={500}
                className="w-full rounded-lg border border-zinc-200 bg-transparent px-2.5 py-1 text-xs outline-none focus-visible:ring-2 focus-visible:ring-indigo-400/60 dark:border-white/10" />
            </fieldset>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={!Object.values(picked).some((v) => v.trim())} onClick={answer}>{t("Continue")}</Button>
            <Button variant="ghost" onClick={() => setResult({ ...result, ready: true })}>{t("Skip the questions")}</Button>
          </div>
        </div>
      )}

      {!busy && result && result.ready && (
        <div className="space-y-2">
          {result.why && <p className="text-xs text-zinc-500">{result.why}</p>}
          <Textarea rows={compact ? 7 : 9} value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={4000} aria-label={t("Goal for the robot")} />
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" disabled={goal.trim().length < 5} onClick={() => onStart(goal.trim())}><Play className="size-3.5" />{t("Start with this goal")}</Button>
            <Button variant="ghost" onClick={() => onStart(request)}>{t("Use my request as typed")}</Button>
            <Button variant="ghost" onClick={onCancel}><X className="size-3.5" />{t("Cancel")}</Button>
          </div>
        </div>
      )}
    </div>
  );
}
