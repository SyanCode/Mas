"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { Copy, KeyRound, MoreHorizontal, ShieldOff, Trash2, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { CmsLayout, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { Avatar } from "@/components/cms/ui/avatar";
import { Badge } from "@/components/cms/ui/badge";
import { Button } from "@/components/cms/ui/button";
import { Card } from "@/components/cms/ui/card";
import { ConfirmDialog, Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/cms/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/cms/ui/dropdown-menu";
import { Input } from "@/components/cms/ui/input";
import { Label } from "@/components/cms/ui/label";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Switch } from "@/components/cms/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { api, type ApiError, type CmsUser } from "@/lib/api";
import { useT } from "@/lib/i18n";

function People() {
  const t = useT();
  const { session, sites } = useCms();
  const [users, setUsers] = useState<CmsUser[] | null>(null);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState<{ username: string; password: string } | null>(null);
  const [access, setAccess] = useState<{ user: CmsUser; apps: string[] } | null>(null);
  const [removing, setRemoving] = useState<CmsUser | null>(null);

  const load = useCallback(() => {
    api<CmsUser[]>("/users").then(setUsers).catch((e: ApiError) => setError(e.detail));
  }, []);
  useEffect(load, [load]);

  async function act(fn: () => Promise<void>) {
    setError("");
    try { await fn(); load(); } catch (e) { toast.error((e as ApiError).detail); }
  }

  if (session.role !== "admin") return <p className="text-sm text-muted-foreground">{t("Only administrators manage users.")}</p>;
  const siteName = (id: string) => sites.find((s) => s.id === id)?.name ?? id;

  return (
    <>
      <PageTitle title={t("Users")} description={t("Client accounts that can edit the content of their sites.")}
        actions={<Button onClick={() => { setUsername(""); setAdding(true); }}><UserPlus />{t("Add editor")}</Button>} />
      {error && <p className="mb-4 text-sm text-destructive">{error}</p>}
      <Card>
        <Table>
          <TableHeader><TableRow><TableHead>{t("User")}</TableHead><TableHead>{t("Role")}</TableHead><TableHead>{t("Sites")}</TableHead><TableHead>2FA</TableHead><TableHead className="w-10" /></TableRow></TableHeader>
          <TableBody>
            {!users && <TableRow><TableCell colSpan={5}><Skeleton className="h-8" /></TableCell></TableRow>}
            {users?.map((u) => (
              <TableRow key={u.id}>
                <TableCell><span className="flex items-center gap-2.5"><Avatar name={u.username} /><span className="font-medium">{u.username}</span></span></TableCell>
                <TableCell><Badge variant={u.role === "admin" ? "default" : "secondary"}>{u.role === "admin" ? t("Administrator") : t("Editor")}</Badge></TableCell>
                <TableCell>{u.role === "admin" ? <span className="text-muted-foreground">{t("All sites")}</span> : u.apps.length === 0 ? <span className="text-muted-foreground">{t("None")}</span> : <span className="flex flex-wrap gap-1">{u.apps.map((a) => <Badge key={a} variant="outline">{siteName(a)}</Badge>)}</span>}</TableCell>
                <TableCell>{u.totp_enabled ? <Badge variant="success">{t("On")}</Badge> : <Badge variant="outline">{t("Off")}</Badge>}</TableCell>
                <TableCell>
                  {u.role === "editor" && (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="size-8" aria-label={t("Actions")}><MoreHorizontal /></Button></DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => setAccess({ user: u, apps: u.apps })}><UserPlus />{t("Manage sites")}</DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => act(async () => { const r = await api<{ password: string }>(`/users/${u.id}/password`, { method: "POST" }); setSecret({ username: u.username, password: r.password }); })}><KeyRound />{t("Reset password")}</DropdownMenuItem>
                        {u.totp_enabled && <DropdownMenuItem onSelect={() => act(async () => { await api(`/users/${u.id}/totp/reset`, { method: "POST" }); toast.success(t("2FA reset.")); })}><ShieldOff />{t("Reset 2FA")}</DropdownMenuItem>}
                        <DropdownMenuSeparator />
                        <DropdownMenuItem destructive onSelect={() => setRemoving(u)}><Trash2 />{t("Delete")}</DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      <p className="mt-4 text-xs text-muted-foreground">{t("Invitation links and open sign-up are in")} <Link href="/settings/" className="text-primary hover:underline">{t("Settings")}</Link>.</p>

      <Dialog open={adding} onOpenChange={setAdding}>
        <DialogContent>
          <form onSubmit={(e) => { e.preventDefault(); act(async () => { const r = await api<CmsUser & { password: string }>("/users", { method: "POST", body: { username } }); setAdding(false); setSecret({ username: r.username, password: r.password }); }); }} className="grid gap-4">
            <DialogHeader><DialogTitle>{t("Add editor")}</DialogTitle><DialogDescription>{t("A password is generated. Give access to a site afterwards.")}</DialogDescription></DialogHeader>
            <div className="space-y-1.5"><Label htmlFor="u">{t("Username")}</Label><Input id="u" value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())} maxLength={32} autoFocus /></div>
            <DialogFooter><Button type="submit" disabled={username.length < 2}>{t("Create")}</Button></DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={!!secret} onOpenChange={(o) => !o && setSecret(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t("Password for {name}", { name: secret?.username ?? "" })}</DialogTitle><DialogDescription>{t("Shown only once. Send it through a safe channel.")}</DialogDescription></DialogHeader>
          <div className="flex items-center gap-2"><code className="flex-1 rounded bg-muted px-3 py-2 text-sm">{secret?.password}</code>
            <Button variant="outline" size="icon" aria-label={t("Copy")} onClick={() => { navigator.clipboard?.writeText(secret?.password ?? ""); toast.success(t("Copied.")); }}><Copy /></Button></div>
        </DialogContent>
      </Dialog>

      <Dialog open={!!access} onOpenChange={(o) => !o && setAccess(null)}>
        <DialogContent>
          {access && (
            <>
              <DialogHeader><DialogTitle>{t("Sites of {name}", { name: access.user.username })}</DialogTitle><DialogDescription>{t("The sites this editor can change.")}</DialogDescription></DialogHeader>
              <div className="space-y-3">
                {sites.map((s) => (
                  <div key={s.id} className="flex items-center justify-between gap-3">
                    <Label htmlFor={`a-${s.id}`}>{s.name}<span className="ml-2 text-xs font-normal text-muted-foreground">{s.domain}</span></Label>
                    <Switch id={`a-${s.id}`} checked={access.apps.includes(s.id)}
                      onCheckedChange={(on) => setAccess({ ...access, apps: on ? [...access.apps, s.id] : access.apps.filter((a) => a !== s.id) })} />
                  </div>
                ))}
              </div>
              <DialogFooter><Button onClick={() => act(async () => { await api(`/users/${access.user.id}/apps`, { method: "PUT", body: { apps: access.apps } }); setAccess(null); toast.success(t("Saved.")); })}>{t("Save")}</Button></DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>

      <ConfirmDialog open={!!removing} destructive title={t("Delete “{name}”?", { name: removing?.username ?? "" })}
        description={t("The account is removed. The content it edited stays.")} confirmLabel={t("Delete")} cancelLabel={t("Cancel")}
        onConfirm={() => { const u = removing!; setRemoving(null); act(async () => { await api(`/users/${u.id}`, { method: "DELETE" }); }); }} onCancel={() => setRemoving(null)} />
    </>
  );
}

export default function UsersPage() {
  return <CmsLayout><People /></CmsLayout>;
}
