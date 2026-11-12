"use client";

import type { ReactNode } from "react";
import { Badge } from "@/components/cms/ui/badge";
import { Card, CardContent } from "@/components/cms/ui/card";
import type { CmsActivity, CmsStatus, ReleaseStatus } from "@/lib/api";
import { RollingNumber, Trend } from "@/components/cms/premium";
import { msg, useI18n, useT } from "@/lib/i18n";

export function StatCard({ label, value, numeric, format, trend, trendColor, trendFormat, hint, icon }: {
  label: string; value?: ReactNode; numeric?: number; format?: (n: number) => string; trend?: number[]; trendColor?: string;
  trendFormat?: (n: number) => string; hint?: ReactNode; icon: ReactNode;
}) {
  const { lang } = useI18n();
  const fmt = format ?? ((n: number) => new Intl.NumberFormat(lang).format(n));
  return (
    <Card className="group relative overflow-hidden transition-all duration-200 hover:-translate-y-0.5">
      <CardContent className="p-6 pb-0">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[13px] font-medium text-muted-foreground">{label}</p>
            <p className="mt-2 font-display text-[48px] leading-none tracking-[-0.02em] tabular-nums">{numeric !== undefined ? <RollingNumber value={numeric} format={fmt} /> : value}</p>
            {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
          </div>
          <div className="text-muted-foreground/70 transition-colors group-hover:text-brand [&_svg]:size-[18px]">{icon}</div>
        </div>
        {trend && trend.length > 1 ? <Trend data={trend} color={trendColor} format={trendFormat ?? fmt} className="-mx-6 mt-3" /> : <div className="pb-6" />}
      </CardContent>
    </Card>
  );
}

export function StatusBadge({ status }: { status: CmsStatus | string }) {
  const t = useT();
  if (status === "draft") return <Badge variant="warning">{t("Draft")}</Badge>;
  if (status === "scheduled") return <Badge variant="info">{t("Scheduled")}</Badge>;
  return <Badge variant="success">{t("Published")}</Badge>;
}

export function DeployBadge({ status }: { status: ReleaseStatus | string }) {
  const t = useT();
  if (status === "live") return <Badge variant="success">{t("Ready")}</Badge>;
  if (status === "failed") return <Badge variant="destructive">{t("Error")}</Badge>;
  if (status === "building" || status === "queued") return <Badge variant="warning">{status === "building" ? t("Building") : t("Queued")}</Badge>;
  return <Badge variant="secondary">{t("Superseded")}</Badge>;
}

const VERBS: Record<string, string> = {
  "cms-save": msg("saved"),
  "cms-delete": msg("deleted"),
  "cms-media": msg("media"),
  "cms-schema": msg("updated the content settings of"),
  "cms-table": msg("created the table"),
  "editor-create": msg("created the account"),
  "editor-password-reset": msg("reset the password of"),
  "editor-2fa-reset": msg("reset the 2FA of"),
  "editor-delete": msg("deleted the account"),
  "editor-access": msg("changed the access of"),
  "invite-create": msg("created an invitation"),
  "invite-revoke": msg("revoked an invitation"),
};

/** One line for the history: who did what, to what. */
export function useDescribe() {
  const t = useT();
  return (a: CmsActivity): string => {
    const detail = (a.detail ?? "").replace(/ → [0-9a-f]{7,8}$/, "").replace(/ → supabase$/, "");
    if (a.action === "cms-status") {
      const [what, state] = detail.split(": ");
      const word = state?.startsWith("published") ? t("published") : state?.startsWith("draft") ? t("unpublished") : state?.startsWith("scheduled") ? t("scheduled") : state;
      return `${word} ${what}`;
    }
    const verb = VERBS[a.action];
    return `${verb ? t(verb) : a.action}${detail ? ` ${detail}` : ""}`;
  };
}

export function formatDate(iso: string, lang: string, withTime = true): string {
  const d = new Date(iso);
  return d.toLocaleString(lang, withTime ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" } : { day: "numeric", month: "short", year: "numeric" });
}

/** "YYYY-MM-DDTHH:MM" (UTC, as stored) → Date. */
export const parseUtc = (value: string) => new Date(`${value}:00Z`);
