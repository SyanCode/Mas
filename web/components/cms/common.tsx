"use client";

import type { ReactNode } from "react";
import { Badge } from "@/components/cms/ui/badge";
import { Card, CardContent } from "@/components/cms/ui/card";
import type { CmsActivity, CmsStatus, ReleaseStatus } from "@/lib/api";
import { msg, useT } from "@/lib/i18n";

export function StatCard({ label, value, hint, icon }: { label: string; value: ReactNode; hint?: ReactNode; icon: ReactNode }) {
  return (
    <Card>
      <CardContent className="flex items-start justify-between gap-3 p-5">
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">{label}</p>
          <p className="mt-1.5 text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
          {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
        </div>
        <div className="rounded-md bg-muted p-2 text-muted-foreground [&_svg]:size-4">{icon}</div>
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
