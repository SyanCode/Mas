"use client";

import { useEffect, useRef, useState } from "react";
import { Button, ErrorText, Spinner } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Check { possible: boolean; reason?: string; files?: string[]; certificates?: string[] }

/** The nginx site of a service that is gone: remove it (nginx -t first, everything put back if refused). */
export function RemoveSite({ serviceId }: { serviceId: string }) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [open, setOpen] = useState(false);
  const [certs, setCerts] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Check>(`/services/${encodeURIComponent(serviceId)}/remove-site`).then(setCheck).catch(() => setCheck(null));
  }, [serviceId]);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  if (!check?.possible) return null;

  async function remove() {
    setBusy(true);
    setError("");
    try {
      await api(`/services/${encodeURIComponent(serviceId)}/remove-site`, { method: "POST", body: { delete_certificate: certs } });
      window.location.href = "/";
    } catch (e) {
      setError((e as ApiError).detail);
      setBusy(false);
    }
  }
  return (
    <>
      <Button variant="danger" onClick={() => setOpen(true)}>{t("Remove the site")}</Button>
      <dialog ref={ref} onCancel={() => setOpen(false)}
        className="glass m-auto w-[min(92vw,30rem)] rounded-2xl p-0 text-zinc-900 shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-sm dark:bg-zinc-950/90 dark:text-zinc-100">
        <div className="space-y-3 p-6 text-sm">
          <h3 className="text-base font-semibold">{t("Remove the site?")}</h3>
          <p className="text-zinc-600 dark:text-zinc-400">{t("Nothing runs behind it any more. nginx stops serving it: a link in sites-enabled is removed (its file stays in sites-available), another file is moved to /etc/nginx/mas-removed. If nginx -t refuses, everything is put back.")}</p>
          <ul className="mono space-y-0.5 text-xs text-zinc-500">{check.files?.map((f) => <li key={f}>{f}</li>)}</ul>
          {(check.certificates?.length ?? 0) > 0 && (
            <label className="flex items-start gap-2">
              <input type="checkbox" className="mt-1" checked={certs} onChange={(e) => setCerts(e.target.checked)} />
              <span>{t("Also delete its certificate ({names}), no other site uses it", { names: check.certificates!.join(", ") })}</span>
            </label>
          )}
          <ErrorText>{error}</ErrorText>
          <div className="flex justify-end gap-2 pt-2">
            <Button onClick={() => setOpen(false)}>{t("Cancel")}</Button>
            <Button variant="danger" disabled={busy} onClick={remove}>{busy ? <Spinner /> : null}{t("Remove")}</Button>
          </div>
        </div>
      </dialog>
    </>
  );
}
