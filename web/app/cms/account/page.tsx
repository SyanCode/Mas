"use client";

import { PasswordForm, ProfileCard, TwoFactor } from "@/components/AccountCards";
import { CmsLayout, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { SessionProvider } from "@/components/Shell";
import { useT } from "@/lib/i18n";

/** The account of a CMS user, inside the CMS: they never land on the server dashboard. */
function Account() {
  const t = useT();
  const { session } = useCms();
  return (
    <SessionProvider value={session}>
      <PageTitle title={t("Your account")} description={t("Password and two-factor authentication.")} />
      <div className="max-w-3xl space-y-4">
        <ProfileCard />
        <TwoFactor enabled={!!session.totp_enabled} />
        <PasswordForm />
        <p className="text-xs text-muted-foreground">{t("Signed in as {name}", { name: session.username })}</p>
      </div>
    </SessionProvider>
  );
}

export default function AccountPage() {
  return <CmsLayout><Account /></CmsLayout>;
}
