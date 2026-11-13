"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Bot } from "lucide-react";
import { api } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Active { id: number; goal: string; status: "running" | "waiting"; app_id: string; steps: number; last: string; started: number }

/** In the header of every page: the robot is working (or waiting for you), what it does now, one click to follow it. */
export function RobotPill({ admin }: { admin: boolean }) {
  const t = useT();
  const [active, setActive] = useState<Active | null>(null);
  useEffect(() => {
    if (!admin) return;
    let alive = true;
    const look = () => api<Active | null>("/robot/active").then((a) => alive && setActive(a)).catch(() => {});
    look();
    const id = setInterval(look, 4000);
    return () => { alive = false; clearInterval(id); };
  }, [admin]);
  if (!active) return null;
  const waiting = active.status === "waiting";
  return (
    <Link href="/advisor/" title={active.last || active.goal}
      className={`flex max-w-[11rem] animate-pop items-center gap-2 rounded-full border px-2.5 py-1 text-xs sm:max-w-xs ${waiting ? "border-amber-400/40 bg-amber-400/10 text-amber-600 dark:text-amber-300" : "border-indigo-400/30 bg-indigo-400/10 text-indigo-600 dark:text-indigo-300"}`}>
      <Bot className={`size-3.5 shrink-0 ${waiting ? "" : "animate-pulse"}`} />
      <span className="min-w-0 truncate">{waiting ? t("The robot needs you") : t("Robot working…")} · {t("{n} steps", { n: active.steps })}</span>
    </Link>
  );
}
