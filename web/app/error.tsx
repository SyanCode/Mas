"use client";

import { useEffect } from "react";
import { useT } from "@/lib/i18n";

const RELOADED = "mas-reloaded-after-update";

/** A page that crashed: say what happened instead of a blank "Application error", and offer a reload. */
export default function PageError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const t = useT();
  // After an update the open tab may ask for files of the previous build: one fresh load fixes it.
  const stale = /ChunkLoadError|Loading chunk|Failed to fetch dynamically imported module|Loading CSS chunk/i.test(`${error.name} ${error.message}`);

  useEffect(() => {
    console.error(error);
    if (!stale) return;
    try {
      if (sessionStorage.getItem(RELOADED)) return;
      sessionStorage.setItem(RELOADED, "1");
    } catch { return; }
    window.location.reload();
  }, [error, stale]);

  return (
    <div className="flex min-h-screen items-center justify-center p-6">
      <div className="w-full max-w-lg space-y-4 rounded-xl border border-zinc-200/80 p-6 text-sm dark:border-white/[0.08]">
        <h1 className="text-lg font-semibold">{t("This page could not be shown")}</h1>
        <p className="text-zinc-500">{stale ? t("Mas was updated while this tab was open. Reload the page.") : t("An error occurred in the page. Reload it; if it comes back, send this message to your administrator:")}</p>
        {!stale && <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md bg-zinc-100 p-3 text-xs dark:bg-white/[0.06]">{`${error.name}: ${error.message}`}{error.digest ? `\n(${error.digest})` : ""}</pre>}
        <div className="flex gap-2">
          <button type="button" className="rounded-md bg-zinc-900 px-3 py-1.5 text-white dark:bg-white dark:text-zinc-900"
            onClick={() => { try { sessionStorage.removeItem(RELOADED); } catch {} window.location.reload(); }}>{t("Reload")}</button>
          <button type="button" className="rounded-md px-3 py-1.5 text-zinc-500 hover:text-zinc-900 dark:hover:text-white" onClick={reset}>{t("Try again")}</button>
        </div>
      </div>
    </div>
  );
}
