"use client";

/**
 * Folders of apps and services. One tree shared by the Apps page (apps) and the Overview (services):
 * a folder "Klarfi" can hold the apps Mas deploys and the services it detected. Items are dragged onto a
 * folder or moved with their "…" menu; a folder can be sorted into one sub-folder per type.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react";
import { Button, Input } from "@/components/ui";
import { api, ApiError, type Folder, type FolderItem } from "@/lib/api";
import { useT } from "@/lib/i18n";

export type Kind = "app" | "service";
/** "all": everything, flat. "none": what is in no folder. A number: that folder. */
export type FolderView = "all" | "none" | number;

const DRAG_TYPE = "application/x-mas-item";

export const FOLDER_DOT: Record<string, string> = {
  zinc: "bg-zinc-400", red: "bg-red-500", orange: "bg-orange-500", amber: "bg-amber-400", emerald: "bg-emerald-500",
  teal: "bg-teal-500", sky: "bg-sky-500", indigo: "bg-indigo-500", violet: "bg-violet-500", pink: "bg-pink-500",
};
const FOLDER_TINT: Record<string, string> = {
  zinc: "from-zinc-400/25", red: "from-red-500/25", orange: "from-orange-500/25", amber: "from-amber-400/25",
  emerald: "from-emerald-500/25", teal: "from-teal-500/25", sky: "from-sky-500/25", indigo: "from-indigo-500/25",
  violet: "from-violet-500/25", pink: "from-pink-500/25",
};

export function useFolders() {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [items, setItems] = useState<FolderItem[]>([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api<{ folders: Folder[]; items: FolderItem[] }>("/folders")
      .then((d) => { setFolders(d.folders); setItems(d.items); })
      .catch((e: ApiError) => setError(e.detail));
  }, []);
  useEffect(load, [load]);

  const run = useCallback(async (fn: () => Promise<unknown>) => {
    setError("");
    try {
      await fn();
    } catch (e) {
      setError((e as ApiError).detail);
    }
    load();
  }, [load]);

  const folderOf = useCallback((kind: Kind, id: string) => items.find((i) => i.kind === kind && i.item_id === id)?.folder_id ?? null, [items]);

  /** Every folder id under ``id`` (itself included). */
  const subtree = useCallback((id: number): Set<number> => {
    const out = new Set([id]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const f of folders) {
        if (f.parent_id !== null && out.has(f.parent_id) && !out.has(f.id)) {
          out.add(f.id);
          grew = true;
        }
      }
    }
    return out;
  }, [folders]);

  const move = useCallback((kind: Kind, ids: string[], folderId: number | null) => {
    setItems((current) => [  // shown at once, confirmed by the reload
      ...current.filter((i) => !(i.kind === kind && ids.includes(i.item_id))),
      ...(folderId === null ? [] : ids.map((item_id) => ({ kind, item_id, folder_id: folderId }))),
    ]);
    return run(() => api("/folders/items", { method: "PUT", body: { items: ids.map((id) => ({ kind, id })), folder_id: folderId } }));
  }, [run]);

  return {
    folders, items, error, setError, load, folderOf, subtree, move,
    create: (name: string, parent_id: number | null, color: string | null) =>
      run(() => api("/folders", { method: "POST", body: { name, parent_id, color } })),
    update: (id: number, body: Partial<Pick<Folder, "name" | "parent_id" | "color">>) =>
      run(() => api(`/folders/${id}`, { method: "PATCH", body })),
    remove: (id: number) => run(() => api(`/folders/${id}`, { method: "DELETE" })),
    sort: (id: number, labels: Record<string, string>) =>
      run(() => api(`/folders/${id}/sort`, { method: "POST", body: { labels } })),
  };
}

export type FoldersApi = ReturnType<typeof useFolders>;

/** The view picked last on a page, kept in the browser. */
export function useFolderView(page: string): [FolderView, (v: FolderView) => void] {
  const key = `mas-folder-${page}`;
  const [view, setViewState] = useState<FolderView>("all");
  useEffect(() => {
    try {
      const saved = localStorage.getItem(key);
      if (saved === "none" || saved === "all") setViewState(saved);
      else if (saved && /^\d+$/.test(saved)) setViewState(Number(saved));
    } catch {}
  }, [key]);
  const setView = useCallback((v: FolderView) => {
    setViewState(v);
    try {
      localStorage.setItem(key, String(v));
    } catch {}
  }, [key]);
  return [view, setView];
}

/** Ids of the items of ``kind`` shown in ``view`` (directly in the folder; "all": every one). */
export function visibleIds(f: FoldersApi, kind: Kind, ids: string[], view: FolderView): string[] {
  if (view === "all") return ids;
  if (view === "none") return ids.filter((id) => f.folderOf(kind, id) === null);
  return ids.filter((id) => f.folderOf(kind, id) === view);
}

/** Drag an item card onto a folder. */
export function dragProps(kind: Kind, id: string) {
  return {
    draggable: true,
    onDragStart: (e: DragEvent) => {
      e.dataTransfer.setData(DRAG_TYPE, JSON.stringify({ kind, id }));
      e.dataTransfer.effectAllowed = "move";
    },
  };
}

function useDrop(onDrop: (kind: Kind, id: string) => void) {
  const [over, setOver] = useState(false);
  return {
    over,
    props: {
      onDragOver: (e: DragEvent) => {
        if (!e.dataTransfer.types.includes(DRAG_TYPE)) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        setOver(true);
      },
      onDragLeave: () => setOver(false),
      onDrop: (e: DragEvent) => {
        setOver(false);
        const raw = e.dataTransfer.getData(DRAG_TYPE);
        if (!raw) return;
        e.preventDefault();
        const { kind, id } = JSON.parse(raw) as { kind: Kind; id: string };
        onDrop(kind, id);
      },
    },
  };
}

function FolderIcon({ color, open = false }: { color: string | null; open?: boolean }) {
  return (
    <span className="relative inline-flex h-4 w-4 shrink-0 items-center justify-center">
      <svg viewBox="0 0 16 16" className="h-4 w-4 text-zinc-400" fill="currentColor" aria-hidden>
        {open
          ? <path d="M1.5 4A1.5 1.5 0 0 1 3 2.5h3l1.5 1.5H13A1.5 1.5 0 0 1 14.5 5.5v.5H4.2a1.5 1.5 0 0 0-1.42 1.02L1.5 11V4Zm1.94 3.34A.5.5 0 0 1 3.9 7H15a.5.5 0 0 1 .47.66l-1.6 5a.5.5 0 0 1-.47.34H2.2a.5.5 0 0 1-.47-.66l1.7-5Z" />
          : <path d="M1.5 4A1.5 1.5 0 0 1 3 2.5h3l1.5 1.5H13A1.5 1.5 0 0 1 14.5 5.5V12A1.5 1.5 0 0 1 13 13.5H3A1.5 1.5 0 0 1 1.5 12V4Z" />}
      </svg>
      {color && <span className={`absolute -bottom-0.5 -right-0.5 h-2 w-2 rounded-full ring-2 ring-white dark:ring-zinc-950 ${FOLDER_DOT[color]}`} />}
    </span>
  );
}

/** Create or rename a folder: name and color. */
function FolderDialog({ open, title, initial, onSave, onClose }: {
  open: boolean; title: string; initial: { name: string; color: string | null };
  onSave: (name: string, color: string | null) => void; onClose: () => void;
}) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState(initial.name);
  const [color, setColor] = useState<string | null>(initial.color);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open) {
      setName(initial.name);
      setColor(initial.color);
      if (!d.open) d.showModal();
    } else if (d.open) d.close();
  }, [open, initial.name, initial.color]);
  return (
    <dialog ref={ref} onCancel={onClose} className="panel m-auto w-[min(92vw,24rem)] rounded-2xl p-0 text-zinc-900 backdrop:bg-black/60 backdrop:backdrop-blur-sm dark:text-zinc-100">
      <form className="space-y-4 p-6" onSubmit={(e) => { e.preventDefault(); if (name.trim()) onSave(name.trim(), color); }}>
        <h3 className="text-base font-semibold">{title}</h3>
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("Folder name")} maxLength={60} autoFocus />
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => setColor(null)} title={t("No color")}
            className={`h-6 w-6 rounded-full border border-dashed border-zinc-400 ${color === null ? "ring-2 ring-indigo-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-950" : ""}`} />
          {Object.entries(FOLDER_DOT).map(([c, cls]) => (
            <button key={c} type="button" onClick={() => setColor(c)} title={c}
              className={`h-6 w-6 rounded-full ${cls} ${color === c ? "ring-2 ring-indigo-500 ring-offset-2 ring-offset-white dark:ring-offset-zinc-950" : ""}`} />
          ))}
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" onClick={onClose}>{t("Cancel")}</Button>
          <Button type="submit" variant="primary" disabled={!name.trim()}>{t("Save")}</Button>
        </div>
      </form>
    </dialog>
  );
}

export interface FolderAction {
  label: string;
  onClick: (folder: Folder, itemIds: string[]) => void;
}

interface SidebarProps {
  f: FoldersApi;
  kind: Kind;
  /** Every id of ``kind`` on the page: counts and folder actions. */
  ids: string[];
  view: FolderView;
  onView: (v: FolderView) => void;
  /** Labels of the types, for "Sort by type" (sub-folder names). */
  typeLabels: Record<string, string>;
  /** More entries in the menu of a folder (freeze its apps…), given the ids of ``kind`` in it. */
  actions?: FolderAction[];
}

/** The folder tree beside a list. */
export function FolderSidebar({ f, kind, ids, view, onView, typeLabels, actions = [] }: SidebarProps) {
  const t = useT();
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [menu, setMenu] = useState<number | null>(null);
  const [dialog, setDialog] = useState<{ mode: "create" | "rename"; parent: number | null; folder?: Folder } | null>(null);
  const [deleting, setDeleting] = useState<Folder | null>(null);
  const idSet = useMemo(() => new Set(ids), [ids]);

  const count = useCallback((folderId: number) => {
    const tree = f.subtree(folderId);
    return f.items.filter((i) => i.kind === kind && idSet.has(i.item_id) && tree.has(i.folder_id)).length;
  }, [f, kind, idSet]);
  const loose = ids.filter((id) => f.folderOf(kind, id) === null).length;
  const idsIn = (folderId: number) => {
    const tree = f.subtree(folderId);
    return f.items.filter((i) => i.kind === kind && idSet.has(i.item_id) && tree.has(i.folder_id)).map((i) => i.item_id);
  };

  // The folder being viewed is always unfolded down to it.
  useEffect(() => {
    if (typeof view !== "number") return;
    const chain: number[] = [];
    let cur = f.folders.find((x) => x.id === view);
    while (cur?.parent_id != null) {
      chain.push(cur.parent_id);
      cur = f.folders.find((x) => x.id === cur!.parent_id);
    }
    if (chain.some((id) => !expanded.has(id))) setExpanded((e) => new Set([...e, ...chain]));
  }, [view, f.folders]);  // eslint-disable-line react-hooks/exhaustive-deps

  function Row({ folder, depth }: { folder: Folder; depth: number }) {
    const children = f.folders.filter((x) => x.parent_id === folder.id);
    const open = expanded.has(folder.id);
    const active = view === folder.id;
    const drop = useDrop((k, id) => k === kind && f.move(k, [id], folder.id));
    const folderDrop = {
      ...drop.props,
      onDrop: (e: DragEvent) => {
        const raw = e.dataTransfer.getData("application/x-mas-folder");
        if (raw) {
          e.preventDefault();
          const moving = Number(raw);
          if (moving !== folder.id && !f.subtree(moving).has(folder.id)) f.update(moving, { parent_id: folder.id });
          return;
        }
        drop.props.onDrop(e);
      },
      onDragOver: (e: DragEvent) => {
        if (e.dataTransfer.types.includes("application/x-mas-folder")) {
          e.preventDefault();
          return;
        }
        drop.props.onDragOver(e);
      },
    };
    return (
      <li>
        <div {...folderDrop} draggable onDragStart={(e) => { e.stopPropagation(); e.dataTransfer.setData("application/x-mas-folder", String(folder.id)); }}
          className={`group relative flex items-center gap-1 rounded-lg py-1 pr-1 text-[13px] transition ${active ? "bg-zinc-200/80 font-medium dark:bg-white/[0.08]" : "hover:bg-zinc-100 dark:hover:bg-white/[0.04]"} ${drop.over ? "ring-2 ring-indigo-500" : ""}`}
          style={{ paddingLeft: 4 + depth * 14 }}>
          <button type="button" onClick={() => setExpanded((e) => { const n = new Set(e); if (n.has(folder.id)) n.delete(folder.id); else n.add(folder.id); return n; })}
            className={`flex h-4 w-4 shrink-0 items-center justify-center text-zinc-400 transition ${children.length ? "" : "invisible"} ${open ? "rotate-90" : ""}`} aria-label={t("Expand")}>
            <svg viewBox="0 0 16 16" className="h-3 w-3" fill="currentColor"><path d="M6 4l4 4-4 4z" /></svg>
          </button>
          <button type="button" onClick={() => onView(folder.id)} className="flex min-w-0 flex-1 items-center gap-2 text-left">
            <FolderIcon color={folder.color} open={active} />
            <span className="truncate">{folder.name}</span>
          </button>
          <span className="text-xs tabular-nums text-zinc-500 group-hover:hidden">{count(folder.id) || ""}</span>
          <button type="button" onClick={() => setMenu(menu === folder.id ? null : folder.id)}
            className="hidden h-5 w-5 items-center justify-center rounded text-zinc-500 hover:bg-zinc-200 group-hover:flex dark:hover:bg-white/10" aria-label={t("Folder actions")}>⋯</button>
          {menu === folder.id && (
            <div className="panel absolute right-0 top-7 z-30 w-56 rounded-xl p-1 font-normal" onMouseLeave={() => setMenu(null)}>
              {[
                { label: t("New sub-folder"), run: () => setDialog({ mode: "create", parent: folder.id }) },
                { label: t("Rename or recolor"), run: () => setDialog({ mode: "rename", parent: folder.parent_id, folder }) },
                { label: t("Sort into sub-folders by type"), run: () => f.sort(folder.id, typeLabels) },
                ...(folder.parent_id !== null ? [{ label: t("Move to the top level"), run: () => f.update(folder.id, { parent_id: null }) }] : []),
                ...actions.map((a) => ({ label: a.label, run: () => a.onClick(folder, idsIn(folder.id)) })),
              ].map((entry) => (
                <button key={entry.label} type="button" onClick={() => { setMenu(null); entry.run(); }}
                  className="block w-full rounded-md px-3 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06]">{entry.label}</button>
              ))}
              <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
              <button type="button" onClick={() => { setMenu(null); setDeleting(folder); }}
                className="block w-full rounded-md px-3 py-1.5 text-left text-sm text-rose-600 hover:bg-zinc-100 dark:text-rose-400 dark:hover:bg-white/[0.06]">{t("Delete folder")}</button>
            </div>
          )}
        </div>
        {open && children.length > 0 && (
          <ul>{children.map((c) => <Row key={c.id} folder={c} depth={depth + 1} />)}</ul>
        )}
      </li>
    );
  }

  const looseDrop = useDrop((k, id) => k === kind && f.move(k, [id], null));
  const roots = f.folders.filter((x) => x.parent_id === null);

  return (
    <aside className="space-y-1">
      <div className="mb-2 flex items-center justify-between px-1">
        <span className="text-[11px] font-medium uppercase tracking-wider text-zinc-500">{t("Folders")}</span>
        <button type="button" onClick={() => setDialog({ mode: "create", parent: null })} title={t("New folder")}
          className="flex h-6 w-6 items-center justify-center rounded-md text-zinc-500 hover:bg-zinc-200 hover:text-zinc-900 dark:hover:bg-white/10 dark:hover:text-white">+</button>
      </div>
      <button type="button" onClick={() => onView("all")}
        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-[13px] ${view === "all" ? "bg-zinc-200/80 font-medium dark:bg-white/[0.08]" : "hover:bg-zinc-100 dark:hover:bg-white/[0.04]"}`}>
        <span className="flex h-4 w-4 items-center justify-center text-zinc-400">▦</span>{t("Everything")}
        <span className="ml-auto text-xs tabular-nums text-zinc-500">{ids.length}</span>
      </button>
      <button type="button" onClick={() => onView("none")} {...looseDrop.props}
        className={`flex w-full items-center gap-2 rounded-lg px-2 py-1 text-left text-[13px] ${view === "none" ? "bg-zinc-200/80 font-medium dark:bg-white/[0.08]" : "hover:bg-zinc-100 dark:hover:bg-white/[0.04]"} ${looseDrop.over ? "ring-2 ring-indigo-500" : ""}`}>
        <span className="flex h-4 w-4 items-center justify-center text-zinc-400">◌</span>{t("Not in a folder")}
        <span className="ml-auto text-xs tabular-nums text-zinc-500">{loose}</span>
      </button>
      <ul className="pt-1">{roots.map((folder) => <Row key={folder.id} folder={folder} depth={0} />)}</ul>
      {roots.length === 0 && (
        <p className="px-2 pt-2 text-xs text-zinc-500">{t("Create folders to group the apps and services of a client or a product, then drag them in.")}</p>
      )}
      {f.error && <p className="px-2 pt-2 text-xs text-rose-500">{t(f.error)}</p>}
      <FolderDialog
        open={!!dialog}
        title={dialog?.mode === "rename" ? t("Folder") : dialog?.parent ? t("New sub-folder") : t("New folder")}
        initial={{ name: dialog?.folder?.name ?? "", color: dialog?.folder?.color ?? null }}
        onClose={() => setDialog(null)}
        onSave={(name, color) => {
          const d = dialog!;
          setDialog(null);
          if (d.mode === "rename" && d.folder) f.update(d.folder.id, { name, color });
          else {
            f.create(name, d.parent, color);
            if (d.parent !== null) setExpanded((e) => new Set([...e, d.parent!]));
          }
        }}
      />
      <DeleteFolder folder={deleting} onCancel={() => setDeleting(null)} onConfirm={() => {
        const folder = deleting!;
        setDeleting(null);
        if (view === folder.id) onView(folder.parent_id ?? "all");
        f.remove(folder.id);
      }} />
    </aside>
  );
}

function DeleteFolder({ folder, onCancel, onConfirm }: { folder: Folder | null; onCancel: () => void; onConfirm: () => void }) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (folder && !d.open) d.showModal();
    if (!folder && d.open) d.close();
  }, [folder]);
  return (
    <dialog ref={ref} onCancel={onCancel} className="panel m-auto w-[min(92vw,26rem)] rounded-2xl p-0 text-zinc-900 backdrop:bg-black/60 backdrop:backdrop-blur-sm dark:text-zinc-100">
      <div className="space-y-3 p-6">
        <h3 className="text-base font-semibold">{t("Delete the folder {name}?", { name: folder?.name ?? "" })}</h3>
        <p className="text-sm text-zinc-500">{t("Its sub-folders, apps and services move up one level. Nothing else is deleted.")}</p>
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onCancel}>{t("Cancel")}</Button>
          <Button variant="danger" onClick={onConfirm}>{t("Delete folder")}</Button>
        </div>
      </div>
    </dialog>
  );
}

/** Path to the folder viewed, and its sub-folders as tiles to open or drop on. */
export function FolderHeader({ f, kind, ids, view, onView }: { f: FoldersApi; kind: Kind; ids: string[]; view: FolderView; onView: (v: FolderView) => void }) {
  const t = useT();
  if (typeof view !== "number") return null;
  const folder = f.folders.find((x) => x.id === view);
  if (!folder) return null;
  const chain: Folder[] = [folder];
  while (chain[0].parent_id !== null) {
    const parent = f.folders.find((x) => x.id === chain[0].parent_id);
    if (!parent) break;
    chain.unshift(parent);
  }
  const children = f.folders.filter((x) => x.parent_id === folder.id);
  const idSet = new Set(ids);
  return (
    <div className="mb-5 space-y-3">
      <nav className="flex flex-wrap items-center gap-1.5 text-sm text-zinc-500">
        <button type="button" onClick={() => onView("all")} className="hover:text-zinc-900 dark:hover:text-white">{t("Everything")}</button>
        {chain.map((c) => (
          <span key={c.id} className="flex items-center gap-1.5">
            <span className="opacity-50">/</span>
            <button type="button" onClick={() => onView(c.id)} className={c.id === folder.id ? "font-medium text-zinc-900 dark:text-white" : "hover:text-zinc-900 dark:hover:text-white"}>{c.name}</button>
          </span>
        ))}
      </nav>
      {children.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {children.map((c) => {
            const tree = f.subtree(c.id);
            const n = f.items.filter((i) => i.kind === kind && idSet.has(i.item_id) && tree.has(i.folder_id)).length;
            return <FolderTile key={c.id} folder={c} count={n} onOpen={() => onView(c.id)} onDrop={(k, id) => k === kind && f.move(k, [id], c.id)} />;
          })}
        </div>
      )}
    </div>
  );
}

function FolderTile({ folder, count, onOpen, onDrop }: { folder: Folder; count: number; onOpen: () => void; onDrop: (kind: Kind, id: string) => void }) {
  const t = useT();
  const drop = useDrop(onDrop);
  return (
    <button type="button" onClick={onOpen} {...drop.props}
      className={`glass relative flex items-center gap-3 overflow-hidden rounded-xl px-4 py-3 text-left transition hover:-translate-y-0.5 ${drop.over ? "ring-2 ring-indigo-500" : ""}`}>
      <span className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${FOLDER_TINT[folder.color ?? "zinc"]} to-transparent`} />
      <FolderIcon color={folder.color} />
      <span className="relative min-w-0">
        <span className="block truncate text-sm font-medium">{folder.name}</span>
        <span className="block text-xs text-zinc-500">{t("{n} item(s)", { n: count })}</span>
      </span>
    </button>
  );
}

/** "…" on an item card: move it to a folder. */
export function MoveMenu({ f, kind, id, className = "" }: { f: FoldersApi; kind: Kind; id: string; className?: string }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const current = f.folderOf(kind, id);
  const depth = (folder: Folder): number => {
    let d = 0;
    let cur: Folder | undefined = folder;
    while (cur?.parent_id != null) {
      d += 1;
      cur = f.folders.find((x) => x.id === cur!.parent_id);
    }
    return d;
  };
  // Tree order: every folder right after its parent.
  const ordered: Folder[] = [];
  const walk = (parent: number | null) => f.folders.filter((x) => x.parent_id === parent).forEach((x) => { ordered.push(x); walk(x.id); });
  walk(null);
  return (
    <span className={`relative ${className}`} onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
      <button type="button" onClick={() => setOpen((o) => !o)} title={t("Move to a folder")}
        className="flex h-7 w-7 items-center justify-center rounded-md bg-black/30 text-white/90 backdrop-blur transition hover:bg-black/50">
        <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="currentColor" aria-hidden><path d="M1.5 4A1.5 1.5 0 0 1 3 2.5h3l1.5 1.5H13A1.5 1.5 0 0 1 14.5 5.5V12A1.5 1.5 0 0 1 13 13.5H3A1.5 1.5 0 0 1 1.5 12V4Z" /></svg>
      </button>
      {open && (
        <div className="panel absolute right-0 top-8 z-30 max-h-72 w-56 overflow-y-auto rounded-xl p-1 text-zinc-900 dark:text-zinc-100" onMouseLeave={() => setOpen(false)}>
          <div className="px-3 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-zinc-500">{t("Move to")}</div>
          {ordered.map((folder) => (
            <button key={folder.id} type="button" onClick={() => { setOpen(false); f.move(kind, [id], folder.id); }}
              className={`flex w-full items-center gap-2 rounded-md py-1.5 pr-3 text-left text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06] ${current === folder.id ? "font-medium" : ""}`}
              style={{ paddingLeft: 12 + depth(folder) * 12 }}>
              <FolderIcon color={folder.color} />
              <span className="truncate">{folder.name}</span>
              {current === folder.id && <span className="ml-auto text-xs">✓</span>}
            </button>
          ))}
          {ordered.length === 0 && <p className="px-3 py-2 text-xs text-zinc-500">{t("No folder yet: create one in the folder list.")}</p>}
          {current !== null && (
            <>
              <div className="my-1 h-px bg-zinc-200 dark:bg-white/[0.06]" />
              <button type="button" onClick={() => { setOpen(false); f.move(kind, [id], null); }}
                className="block w-full rounded-md px-3 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-white/[0.06]">{t("Take out of the folder")}</button>
            </>
          )}
        </div>
      )}
    </span>
  );
}

/** Sidebar + content, the sidebar collapsing above the list on small screens. */
export function FolderLayout({ sidebar, children }: { sidebar: ReactNode; children: ReactNode }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <div className="lg:grid lg:grid-cols-[15rem_minmax(0,1fr)] lg:gap-8">
      <div className="mb-4 lg:hidden">
        <Button onClick={() => setOpen((o) => !o)}>{open ? t("Hide folders") : t("Folders")}</Button>
        {open && <div className="glass mt-2 rounded-xl p-2">{sidebar}</div>}
      </div>
      <div className="hidden lg:block"><div className="sticky top-20">{sidebar}</div></div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
