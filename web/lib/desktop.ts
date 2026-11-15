import { Activity, Database, Gauge, HeartPulse, LayoutDashboard, Newspaper, Rocket, Server, Settings, Sparkles, type LucideIcon } from "lucide-react";
import { msg } from "@/lib/i18n";

/** The apps of the desktop: each one is a page of the dashboard shown in a window. */
export interface DesktopApp {
  id: string;
  label: string;
  href: string;
  icon: LucideIcon;
  /** Tile gradient (Tailwind classes). */
  tint: string;
  size: [number, number];
  /** Also open to CMS-only accounts. */
  editor?: boolean;
  match: (path: string) => boolean;
}

export const DESKTOP_APPS: DesktopApp[] = [
  { id: "overview", label: msg("Overview"), href: "/", icon: LayoutDashboard, tint: "from-indigo-500 to-violet-500", size: [980, 640], match: (p) => p === "/" || p.startsWith("/service") },
  { id: "apps", label: msg("Apps"), href: "/apps/", icon: Rocket, tint: "from-sky-500 to-cyan-400", size: [960, 640], match: (p) => p.startsWith("/apps") || p.startsWith("/projects") },
  { id: "databases", label: msg("Databases"), href: "/databases/", icon: Database, tint: "from-emerald-500 to-teal-400", size: [940, 620], match: (p) => p.startsWith("/databases") },
  { id: "resources", label: msg("Resources"), href: "/resources/", icon: Gauge, tint: "from-amber-500 to-orange-400", size: [920, 620], match: (p) => p.startsWith("/resources") },
  { id: "cluster", label: msg("Servers"), href: "/cluster/", icon: Server, tint: "from-slate-500 to-zinc-400", size: [960, 620], match: (p) => p.startsWith("/cluster") },
  { id: "advisor", label: msg("Advisor"), href: "/advisor/", icon: Sparkles, tint: "from-fuchsia-500 to-pink-400", size: [900, 620], match: (p) => p.startsWith("/advisor") },
  { id: "health", label: msg("Health"), href: "/health/", icon: HeartPulse, tint: "from-rose-500 to-red-400", size: [880, 600], match: (p) => p.startsWith("/health") },
  { id: "history", label: msg("Activity"), href: "/history/", icon: Activity, tint: "from-blue-500 to-indigo-400", size: [860, 580], match: (p) => p.startsWith("/history") },
  { id: "cms", label: msg("CMS"), href: "/cms/", icon: Newspaper, tint: "from-lime-500 to-green-400", size: [1000, 660], editor: true, match: (p) => p.startsWith("/cms") },
  { id: "settings", label: msg("Settings"), href: "/settings/", icon: Settings, tint: "from-zinc-600 to-zinc-400", size: [920, 640], editor: true, match: (p) => p.startsWith("/settings") },
];

export function appOf(href: string): DesktopApp {
  const path = href.split("?")[0];
  return DESKTOP_APPS.find((a) => a.match(path)) ?? DESKTOP_APPS[0];
}

// -- the choice between the classic dashboard and the desktop, kept in the browser ---------

export type UiMode = "classic" | "desktop";
const UI_KEY = "mas-ui";

export function getUiMode(): UiMode {
  try { return localStorage.getItem(UI_KEY) === "desktop" ? "desktop" : "classic"; } catch { return "classic"; }
}

export function setUiMode(mode: UiMode) {
  try { localStorage.setItem(UI_KEY, mode); } catch {}
}

/** The page runs inside a desktop window: no sidebar, no header, the window is the frame. */
export function isEmbedded(): boolean {
  try { return window.self !== window.top; } catch { return true; }
}
