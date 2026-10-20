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
  role: "admin" | "editor";
  version: string;
}

// -- CMS ------------------------------------------------------------------------

export type CmsFieldType =
  | "text" | "textarea" | "markdown" | "number" | "boolean" | "select" | "url" | "email" | "date" | "color" | "image" | "list";

export interface CmsField {
  key: string;
  type: CmsFieldType;
  label: string;
  help: string;
  required: boolean;
  placeholder?: string;
  min?: number;
  max?: number;
  integer?: boolean;
  options?: { value: string; label: string }[];
  max_kb?: number;
  fields?: CmsField[];
  min_items?: number;
  max_items?: number;
  item_label?: string;
}

export interface CmsPage {
  id: string;
  label: string;
  description: string;
  file: string;
  fields: CmsField[];
  values: Record<string, unknown>;
  sha: string | null;
}

export interface CmsSite {
  id: string;
  name: string;
  domain: string | null;
  url: string | null;
  last_release: { id: number; status: ReleaseStatus; trigger: string; message: string | null; created_at: string; finished_at: string | null } | null;
}

export interface CmsUser {
  id: number;
  username: string;
  role: "admin" | "editor";
  totp_enabled: boolean;
  created_at: string;
  apps: string[];
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

export interface Check {
  level: "error" | "warning" | "info";
  category: string;
  title: string;
  detail: string;
  fix: string | null;
}

export type ReleaseStatus = "queued" | "building" | "live" | "failed" | "superseded";

export interface Release {
  id: number;
  app_id: string;
  sha: string | null;
  message: string | null;
  author: string | null;
  trigger: string;
  actor: string | null;
  status: ReleaseStatus;
  path: string | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface App {
  id: string;
  name: string;
  repo: string;
  branch: string;
  installation_id: number | null;
  domain: string | null;
  type: ServiceType;
  port: number | null;
  mode: "managed" | "adopted";
  service_id: string | null;
  path: string | null;
  root_dir: string;
  build_command: string | null;
  start_command: string | null;
  auto_deploy: boolean;
  has_env: boolean;
  instances: number;
  instance_ports: number[];
  backends: string[];
  current_release: number | null;
  current: Release | null;
  last_release: Release | null;
  releases?: Release[];
}

export interface GitHubStatus {
  configured: boolean;
  app_id: number | null;
  slug: string | null;
  install_url?: string;
  webhook_url: string;
  installations: { id: number; account: string }[];
  error: string | null;
}

export interface Repo {
  full_name: string;
  default_branch: string;
  private: boolean;
  installation_id: number;
}

export interface ProviderField {
  name: string;
  label: string;
  secret: boolean;
  options: string[];
}

export interface ProviderInfo {
  type: string;
  label: string;
  help: string;
  token_urls: Record<string, string>;
  fields: ProviderField[];
}

export interface DnsStatus {
  providers: { type: string; label: string; account: string | null }[];
  catalog: ProviderInfo[];
  ipv4: string | null;
  ipv6: string | null;
  suggested_ipv4: string | null;
}

export interface DnsZone {
  zone: string;
  provider: string;
  label: string;
}

export interface DnsCheck {
  domain: string;
  provider: string;
  records: { type: string; target: string }[];
  points_here: boolean;
  points_elsewhere: boolean;
  cname: boolean;
  used_by: string | null;
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

/** POST a file as the raw request body (CMS image uploads). */
export async function uploadFile<T>(path: string, file: Blob): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": file.type || "application/octet-stream" };
  if (csrf) headers["X-CSRF-Token"] = csrf;
  const res = await fetch(`/api${path}`, { method: "POST", headers, body: file, credentials: "same-origin", cache: "no-store" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`);
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
