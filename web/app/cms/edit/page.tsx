"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";

/** Old address of the editor: now the dashboard of the site. */
function Redirect() {
  const app = useSearchParams().get("app");
  useEffect(() => { window.location.replace(app ? `/cms/?app=${encodeURIComponent(app)}` : "/cms/"); }, [app]);
  return null;
}

export default function CmsEditRedirect() {
  return <Suspense fallback={null}><Redirect /></Suspense>;
}
