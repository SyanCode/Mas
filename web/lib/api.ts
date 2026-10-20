export type Status = "running" | "stopped" | "errored" | "unknown";
export type ServiceType = "php" | "nextjs" | "node" | "python" | "static" | "unknown";
export const SERVICE_TYPES: ServiceType[] = ["php", "nextjs", "node", "python", "static", "unknown"];

export interface Commit {
  sha: string;
  subject: string | null;
  author: string | null;
  date: string | null;
}

export interface Service {
  id: string;
  name: string;
  display_name: string | null;
  notes: string | null;
  domains: string[];
  type: ServiceType;
  type_source: "detected" | "manual";
  manager: string;
  path: string | null;
  port: number | null;
  pid: number | null;
  user: string | null;
  status: Status;
  ssl_expires: string | null;
  git_remote: string | null;
  git_branch: string | null;
  last_commit: Commit | null;
  unit: string | null;
  upstream: string | null;
  routes: string[];
  nginx_files: string[];
  https_redirect: boolean;
  cmdline: string | null;
  actions: string[];
  confirm_actions: string[];
  history?: HistoryEntry[];
}

export interface HistoryEntry {
  id: number;
  at: string;
  actor: string;
  service_id: string | null;
  action: string;
  command: string | null;
  ok: boolean;
  detail: string | null;
}

export interface Session {
  username: string;
  csrf: string;
  totp_enabled: boolean;
  version: string;
}

export interface LogSource {
  name: string;
  lines: string[];
  error: string | null;
}

export interface ActionResult {
  ok: boolean;
  action: string;
  command: string[];
  output: string;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

let csrf: string | null = null;

export function rememberSession(s: Session) {
  csrf = s.csrf;
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const method = init.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;

  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
    credentials: "same-origin",
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`;
    if (res.status === 401 && path !== "/login" && typeof window !== "undefined") {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login/?next=${next}`;
    }
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export function githubUrl(remote: string | null): string | null {
  if (!remote) return null;
  const m = remote.match(/github\.com[:/]([^/]+\/[^/]+?)(?:\.git)?$/);
  return m ? `https://github.com/${m[1]}` : null;
}

export function daysUntil(iso: string): number {
  return Math.floor((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

export function timeAgo(iso: string): string {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.max(s, 0)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}
