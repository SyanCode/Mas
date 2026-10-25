"use client";

/**
 * Themes. A theme is a style (colors, fonts, corners, background: globals.css, `data-style` on <html>)
 * and a layout for the lists of apps and services (cards, bento grid or dense list). The mode (dark,
 * light, system) is picked separately; a few styles only exist in one mode. Both are kept in the browser.
 */

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { msg } from "@/lib/i18n";

export type Mode = "dark" | "light" | "system";
export type Layout = "cards" | "bento" | "list";
export type StyleId = "aurora" | "geist" | "bento" | "terminal" | "nord" | "paper";

export interface ThemeStyle {
  id: StyleId;
  label: string;
  description: string;
  layout: Layout;
  /** Gradient banner on the cards of the apps. */
  banner: boolean;
  /** The only mode the style exists in. */
  mode?: "dark" | "light";
  /** Preview: background, surface, text, accents. */
  swatch: { bg: string; surface: string; text: string; accents: [string, string, string] };
}

export const STYLES: ThemeStyle[] = [
  { id: "aurora", label: msg("Aurora"), description: msg("Glass and soft glowing gradients. The original Mas."), layout: "cards", banner: true,
    swatch: { bg: "#050608", surface: "#11131a", text: "#f4f4f5", accents: ["#818cf8", "#38bdf8", "#e879f9"] } },
  { id: "geist", label: msg("Geist"), description: msg("Black and white, sharp and quiet, like Vercel."), layout: "cards", banner: false,
    swatch: { bg: "#000000", surface: "#0a0a0a", text: "#ededed", accents: ["#ededed", "#a1a1a1", "#525252"] } },
  { id: "bento", label: msg("Bento Grid"), description: msg("Big rounded tiles of different sizes, the important ones first."), layout: "bento", banner: true,
    swatch: { bg: "#0c0c0e", surface: "#18181b", text: "#fafafa", accents: ["#8b5cf6", "#22d3ee", "#fb7185"] } },
  { id: "terminal", label: msg("Terminal"), description: msg("Green phosphor on black, monospace, dense lists."), layout: "list", banner: false, mode: "dark",
    swatch: { bg: "#050a06", surface: "#0b140d", text: "#7dff9a", accents: ["#39ff6a", "#2dd4bf", "#facc15"] } },
  { id: "nord", label: msg("Nord"), description: msg("Cold arctic blues, calm and easy on the eyes."), layout: "cards", banner: true,
    swatch: { bg: "#2e3440", surface: "#3b4252", text: "#eceff4", accents: ["#88c0d0", "#81a1c1", "#b48ead"] } },
  { id: "paper", label: msg("Paper"), description: msg("Warm paper, serif titles and ink, like a magazine."), layout: "list", banner: false,
    swatch: { bg: "#f6f1e7", surface: "#fffdf8", text: "#1c1917", accents: ["#c2410c", "#1e3a8a", "#15803d"] } },
];

const STYLE_KEY = "mas-style";
const MODE_KEY = "mas-theme";  // the dark / light / system choice, kept under its historical name

const isStyle = (v: string | null): v is StyleId => STYLES.some((s) => s.id === v);

/** Runs before the first paint (layout.tsx) so the page never flashes another theme. */
export const themeScript = `try{var s=localStorage.getItem("${STYLE_KEY}")||"aurora";var f={terminal:"dark"}[s];var t=f||localStorage.getItem("${MODE_KEY}")||"dark";var d=t==="dark"||(t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches);var e=document.documentElement;e.classList.toggle("dark",d);e.dataset.style=s}catch(e){}`;

function apply(style: ThemeStyle, mode: Mode) {
  const forced = style.mode;
  const dark = forced ? forced === "dark" : mode === "dark" || (mode === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const root = document.documentElement;
  root.classList.toggle("dark", dark);
  root.dataset.style = style.id;
}

interface ThemeContextValue {
  style: ThemeStyle;
  mode: Mode;
  setStyle: (id: StyleId) => void;
  setMode: (mode: Mode) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  style: STYLES[0], mode: "dark", setStyle: () => {}, setMode: () => {},
});

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [styleId, setStyleId] = useState<StyleId | null>(null);
  const [mode, setModeState] = useState<Mode>("dark");
  const style = STYLES.find((s) => s.id === styleId) ?? STYLES[0];

  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(STYLE_KEY);
      setModeState((localStorage.getItem(MODE_KEY) as Mode) || "dark");
    } catch {}
    setStyleId(isStyle(saved) ? saved : "aurora");
  }, []);

  useEffect(() => {
    if (styleId === null) return;  // not read yet: the script of layout.tsx already applied it
    apply(style, mode);
    if (mode !== "system" || style.mode) return;
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const follow = () => apply(style, mode);
    query.addEventListener("change", follow);
    return () => query.removeEventListener("change", follow);
  }, [style, styleId, mode]);

  const setStyle = useCallback((id: StyleId) => {
    setStyleId(id);
    try {
      localStorage.setItem(STYLE_KEY, id);
    } catch {}
  }, []);
  const setMode = useCallback((next: Mode) => {
    setModeState(next);
    try {
      localStorage.setItem(MODE_KEY, next);
    } catch {}
  }, []);

  return <ThemeContext.Provider value={{ style, mode, setStyle, setMode }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}

/** Bento grid: a repeating pattern of 7 tiles, one 2×2, one wide and five small. */
export function bentoSize(index: number, total: number): "lg" | "wide" | "sm" {
  if (total < 3) return "wide";
  const slot = index % 7;
  return slot === 0 ? "lg" : slot === 4 ? "wide" : "sm";
}

export const BENTO_SPAN = { lg: "sm:col-span-2 sm:row-span-2", wide: "sm:col-span-2", sm: "" } as const;
