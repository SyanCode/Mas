"use client";

import { useEffect, useState } from "react";
import { previewHtml } from "@/lib/maintenance";

/** What a visitor sees, scaled down. Sandboxed: the page runs no script and gets no cookie. */
export function MaintenancePreview({ pageId, version = 0, className = "h-48" }: { pageId: string; version?: number; className?: string }) {
  const [html, setHtml] = useState("");
  useEffect(() => {
    previewHtml(pageId).then(setHtml).catch(() => setHtml(""));
  }, [pageId, version]);
  return (
    <div className={`relative overflow-hidden rounded-lg border border-zinc-200 bg-white dark:border-white/[0.08] ${className}`}>
      <iframe title={pageId} srcDoc={html} sandbox="" className="pointer-events-none absolute left-0 top-0 h-[250%] w-[250%] origin-top-left scale-[0.4] border-0" />
    </div>
  );
}
