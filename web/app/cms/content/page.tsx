"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { flexRender, getCoreRowModel, getFilteredRowModel, getPaginationRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from "@tanstack/react-table";
import { ArrowUpDown, Eye, EyeOff, MoreHorizontal, Pencil, Plus, Search, Trash2 } from "lucide-react";
import { CmsLayout, NoSite, PageTitle, useCms } from "@/components/cms/CmsLayout";
import { StatusBadge, formatDate, parseUtc } from "@/components/cms/common";
import { PublishBanner, usePublisher, type Saved } from "@/components/cms/publish";
import { Button } from "@/components/cms/ui/button";
import { Card } from "@/components/cms/ui/card";
import { ConfirmDialog } from "@/components/cms/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/cms/ui/dropdown-menu";
import { Input } from "@/components/cms/ui/input";
import { Skeleton } from "@/components/cms/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/cms/ui/table";
import { Pager, SegmentTabs } from "@/components/cms/premium";
import { api, type ApiError, type CmsCollection, type CmsEntry, type CmsEntrySummary } from "@/lib/api";
import { useI18n, useT } from "@/lib/i18n";

type Row = CmsEntrySummary & { _title: string; _status: string; _sort: string };

function List() {
  const t = useT();
  const { lang } = useI18n();
  const { site, reloadOverview } = useCms();
  const params = useSearchParams();
  const cid = params.get("c") ?? "";
  const [collection, setCollection] = useState<CmsCollection | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [status, setStatus] = useState("all");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [removing, setRemoving] = useState<Row | null>(null);
  const publisher = usePublisher(site);

  const load = useCallback(() => {
    if (!site) return;
    api<{ collections: CmsCollection[] }>(`/cms/sites/${encodeURIComponent(site.id)}`)
      .then((s) => setCollection(s.collections.find((c) => c.id === cid) ?? null))
      .catch((e: ApiError) => setError(e.detail));
  }, [site, cid]);
  useEffect(() => { setCollection(null); load(); }, [load]);

  const base = site ? `/cms/sites/${encodeURIComponent(site.id)}/collections/${encodeURIComponent(cid)}/entries` : "";
  const q = site ? `app=${encodeURIComponent(site.id)}&c=${encodeURIComponent(cid)}` : "";

  const rows: Row[] = useMemo(() => (collection?.entries ?? []).map((e) => ({
    ...e,
    _title: String(e[collection!.title_field] ?? e.slug),
    _status: String(e.status ?? "published"),
    _sort: collection!.sort_field ? String(e[collection!.sort_field] ?? "") : "",
  })), [collection]);
  const counts = useMemo(() => ({
    all: rows.length, published: rows.filter((r) => r._status === "published").length,
    draft: rows.filter((r) => r._status === "draft").length, scheduled: rows.filter((r) => r._status === "scheduled").length,
  }), [rows]);
  const shown = useMemo(() => (status === "all" ? rows : rows.filter((r) => r._status === status)), [rows, status]);

  async function setState(row: Row, next: "published" | "draft") {
    const ok = await publisher.run(() => api<Saved>(`${base}/${encodeURIComponent(row.slug)}/status`, { method: "POST", body: { status: next } }),
      next === "published" ? t("Published.") : t("Moved to drafts."));
    if (ok) { load(); reloadOverview(); }
  }

  async function remove(row: Row) {
    setRemoving(null);
    const ok = await publisher.run(async () => {
      const entry = await api<CmsEntry>(`${base}/${encodeURIComponent(row.slug)}`);
      return api<Saved>(`${base}/${encodeURIComponent(row.slug)}?sha=${encodeURIComponent(entry.sha ?? "")}`, { method: "DELETE" });
    }, t("Deleted."));
    if (ok) { load(); reloadOverview(); }
  }

  const columns = useMemo<ColumnDef<Row>[]>(() => [
    {
      accessorKey: "_title", header: ({ column }) => <SortHead label={t("Title")} onClick={() => column.toggleSorting(column.getIsSorted() === "asc")} />,
      cell: ({ row }) => (
        <Link href={`/cms/entry/?${q}&slug=${encodeURIComponent(row.original.slug)}`} className="block min-w-0">
          <span className="block truncate font-medium hover:underline">{row.original._title}</span>
          <span className="block truncate text-xs text-muted-foreground">/{row.original.slug}</span>
        </Link>
      ),
    },
    ...(collection?.drafts ? [{
      accessorKey: "_status", header: t("Status"),
      cell: ({ row }: { row: { original: Row } }) => (
        <div className="space-y-0.5">
          <StatusBadge status={row.original._status} />
          {row.original._status === "scheduled" && typeof row.original.publish_at === "string" &&
            <div className="text-xs text-muted-foreground">{formatDate(parseUtc(row.original.publish_at).toISOString(), lang)}</div>}
        </div>
      ),
    } as ColumnDef<Row>] : []),
    ...(collection?.sort_field ? [{
      accessorKey: "_sort", header: ({ column }: { column: { toggleSorting: (desc: boolean) => void; getIsSorted: () => false | "asc" | "desc" } }) =>
        <SortHead label={collection.fields.find((f) => f.key === collection.sort_field)?.label ?? t("Date")} onClick={() => column.toggleSorting(column.getIsSorted() === "asc")} />,
      cell: ({ row }: { row: { original: Row } }) => <span className="text-muted-foreground">{row.original._sort || "—"}</span>,
    } as ColumnDef<Row>] : []),
    {
      id: "actions", header: "", enableSorting: false,
      cell: ({ row }) => (
        <div className="flex justify-end">
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="ghost" size="icon" className="size-8" aria-label={t("Actions")}><MoreHorizontal /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem asChild><Link href={`/cms/entry/?${q}&slug=${encodeURIComponent(row.original.slug)}`}><Pencil />{t("Edit")}</Link></DropdownMenuItem>
              {collection?.drafts && (row.original._status === "published"
                ? <DropdownMenuItem onSelect={() => setState(row.original, "draft")}><EyeOff />{t("Move to drafts")}</DropdownMenuItem>
                : <DropdownMenuItem onSelect={() => setState(row.original, "published")}><Eye />{t("Publish now")}</DropdownMenuItem>)}
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => setRemoving(row.original)}><Trash2 />{t("Delete")}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      ),
    },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [collection, q, lang, t]);

  const table = useReactTable({
    data: shown, columns, state: { sorting, globalFilter: filter }, onSortingChange: setSorting, onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(), initialState: { pagination: { pageSize: 15 } },
    globalFilterFn: (row, _id, value: string) => `${row.original._title} ${row.original.slug}`.toLowerCase().includes(value.toLowerCase()),
  });

  if (!site) return <NoSite />;
  return (
    <>
      <PageTitle title={collection?.label ?? "…"} description={collection?.description}
        actions={collection && <Button asChild disabled={!!collection.error}><Link href={`/cms/entry/?${q}`}><Plus />{t("Add")} {collection.item_label.toLowerCase()}</Link></Button>} />
      <PublishBanner state={publisher.state} site={site} />
      {(error || collection?.error) && <p className="mb-4 text-sm text-destructive">{error || collection?.error}</p>}

      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        {collection?.drafts ? (
          <SegmentTabs key={`${counts.all}-${counts.published}-${counts.draft}-${counts.scheduled}`} value={status} onChange={setStatus} items={[
            { value: "all", label: `${t("All")} (${counts.all})` }, { value: "published", label: `${t("Published")} (${counts.published})` },
            { value: "draft", label: `${t("Drafts")} (${counts.draft})` }, { value: "scheduled", label: `${t("Scheduled")} (${counts.scheduled})` }]} />
        ) : <span />}
        <div className="relative w-full sm:w-64">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
          <Input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t("Search…")} className="pl-8" />
        </div>
      </div>

      <Card>
        {!collection ? <div className="space-y-2 p-4"><Skeleton className="h-8" /><Skeleton className="h-8" /><Skeleton className="h-8" /></div> : (
          <>
            <Table>
              <TableHeader>
                {table.getHeaderGroups().map((hg) => (
                  <TableRow key={hg.id} className="hover:bg-transparent">
                    {hg.headers.map((h) => <TableHead key={h.id}>{h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}</TableHead>)}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {table.getRowModel().rows.length === 0 ? (
                  <TableRow className="hover:bg-transparent"><TableCell colSpan={columns.length} className="py-12 text-center text-muted-foreground">
                    {rows.length === 0 ? t("Nothing here yet. Add the first one.") : t("No result.")}
                  </TableCell></TableRow>
                ) : table.getRowModel().rows.map((r) => (
                  <TableRow key={r.id}>{r.getVisibleCells().map((c) => <TableCell key={c.id}>{flexRender(c.column.columnDef.cell, c.getContext())}</TableCell>)}</TableRow>
                ))}
              </TableBody>
            </Table>
            {table.getPageCount() > 1 && (
              <div className="flex items-center justify-between border-t px-4 py-3 text-xs text-muted-foreground">
                <span>{t("Page {n} of {total}", { n: table.getState().pagination.pageIndex + 1, total: table.getPageCount() })}</span>
                <Pager page={table.getState().pagination.pageIndex + 1} pages={table.getPageCount()} onPage={(p) => table.setPageIndex(p - 1)} />
              </div>
            )}
          </>
        )}
      </Card>

      <ConfirmDialog open={!!removing} destructive title={t("Delete “{name}”?", { name: removing?._title ?? "" })}
        description={t("It is removed from the site at the next publication. Your administrator can bring it back from the history.")}
        confirmLabel={t("Delete")} cancelLabel={t("Cancel")} onConfirm={() => removing && remove(removing)} onCancel={() => setRemoving(null)} />
    </>
  );
}

function SortHead({ label, onClick }: { label: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="-ml-1 inline-flex items-center gap-1 rounded px-1 hover:text-foreground">{label}<ArrowUpDown className="size-3" /></button>;
}

export default function ContentPage() {
  return <CmsLayout><List /></CmsLayout>;
}
