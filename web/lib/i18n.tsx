"use client";

/**
 * Interface languages. The English text is the key: `t("Deploy Supabase")`; each
 * locales/<lang>.json maps it to the translation, and a missing entry shows the English.
 * Variables use braces: `t("{n} editor(s)", { n: 3 })`.
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import de from "@/locales/de.json";
import es from "@/locales/es.json";
import fr from "@/locales/fr.json";

export const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "fr", label: "Français" },
  { code: "es", label: "Español" },
  { code: "de", label: "Deutsch" },
] as const;

export type Lang = (typeof LANGUAGES)[number]["code"];
type Vars = Record<string, string | number>;

const DICTS: Record<Lang, Record<string, string>> = { en: {}, fr, es, de };
const STORAGE_KEY = "mas-lang";
const isLang = (value: string | null | undefined): value is Lang => LANGUAGES.some((l) => l.code === value);

let current: Lang = "en"; // for code outside React (timeAgo…)

function detect(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLang(saved)) return saved;
  } catch {}
  for (const tag of navigator.languages ?? [navigator.language]) {
    const code = tag?.slice(0, 2).toLowerCase();
    if (isLang(code)) return code;
  }
  return "en";
}

export function translate(lang: Lang, text: string, vars?: Vars): string {
  const out = DICTS[lang][text] ?? text;
  return vars ? out.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : out;
}

/** Marks a constant for translation (translated later with t(value)); lets the key checker find it. */
export const msg = (text: string) => text;

/** Translation outside a component (uses the language picked last). */
export function tr(text: string, vars?: Vars): string {
  return translate(current, text, vars);
}

const I18nContext = createContext<{ lang: Lang; setLang: (lang: Lang) => void }>({ lang: "en", setLang: () => {} });

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("en");
  const apply = useCallback((next: Lang) => {
    current = next;
    setLangState(next);
    document.documentElement.lang = next;
  }, []);
  useEffect(() => apply(detect()), [apply]);
  const setLang = useCallback((next: Lang) => {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {}
    apply(next);
  }, [apply]);
  return <I18nContext.Provider value={{ lang, setLang }}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const { lang, setLang } = useContext(I18nContext);
  const t = useCallback((text: string, vars?: Vars) => translate(lang, text, vars), [lang]);
  return { t, lang, setLang };
}

export function useT() {
  return useI18n().t;
}

export function LanguagePicker({ className = "" }: { className?: string }) {
  const { lang, setLang } = useI18n();
  return (
    <select value={lang} onChange={(e) => setLang(e.target.value as Lang)} aria-label="Language"
      className={`rounded-md border border-zinc-300 bg-transparent px-2 py-1 text-xs text-zinc-600 dark:border-white/10 dark:text-zinc-300 ${className}`}>
      {LANGUAGES.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
    </select>
  );
}
