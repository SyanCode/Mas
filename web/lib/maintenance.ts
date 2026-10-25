import { api } from "@/lib/api";

export interface MaintenancePage { id: string; name: string; kind: "simple" | "imported"; title?: string; message?: string; back?: string; accent?: string; files?: number }
export interface MaintenanceState { page: string; allow: string[]; until: string | null; since: string; by: string }
export interface MaintenanceSite { id: string; name: string; domain: string; mode: string; supported: boolean; maintenance: MaintenanceState | null }
export interface MaintenanceOverview { pages: MaintenancePage[]; apps: MaintenanceSite[]; your_ip: string }

/** The page as visitors get it, for an <iframe srcdoc> preview (sandboxed, no script). */
export async function previewHtml(pageId: string): Promise<string> {
  const res = await fetch(`/api/maintenance/pages/${encodeURIComponent(pageId)}/files/index.html`, { cache: "no-store" });
  return res.ok ? res.text() : "";
}

export function enableMaintenance(appId: string, page: string, allow: string[], until: string | null) {
  return api<MaintenanceState>(`/apps/${encodeURIComponent(appId)}/maintenance`, { method: "PUT", body: { page, allow, until } });
}

export function disableMaintenance(appId: string) {
  return api(`/apps/${encodeURIComponent(appId)}/maintenance`, { method: "DELETE" });
}
