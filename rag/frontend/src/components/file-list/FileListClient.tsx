"use client";

import Link from "next/link";
import { Search as SearchIcon, Sparkles, X } from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { PageHeader } from "@/components/PageHeader";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type DocumentSummary, type FileStatus } from "@/lib/api";
import { useAnalyzeDocument, useDocuments, useRegisterDocument } from "@/lib/queries";
import { useSelection } from "@/lib/useSelection";
import { APP_ROUTES } from "@/lib/routes";
import { t } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { formatBytes, formatDateTime, formatNumber } from "@/lib/format";

const LIMIT = 20;
const FILTERS: (FileStatus | "ALL")[] = ["ALL", "UPLOADED", "ANALYZED", "REGISTERED", "ERROR"];
const ANALYZABLE: ReadonlySet<FileStatus> = new Set(["UPLOADED", "ERROR"]);

/** 本登録用伝票の一覧。絞り込み・検索・ページング・一括選択・行内アクション。 */
export function FileListClient() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<FileStatus | "ALL">("ALL");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [bulk, setBulk] = useState<{ done: number; total: number } | null>(null);

  const selection = useSelection<string>();
  const status = filter === "ALL" ? undefined : filter;
  const query = useDocuments({ status, q: q || undefined, limit: LIMIT, offset });

  const analyze = useAnalyzeDocument();
  const register = useRegisterDocument();

  const page = query.data;
  const items = page?.items ?? [];
  const pageIds = items.map((d) => d.id);
  const allSelected = pageIds.length > 0 && selection.count === pageIds.length;
  const analyzableSelected = items.filter(
    (d) => selection.isSelected(d.id) && ANALYZABLE.has(d.status)
  );

  const resetView = (fn: () => void) => {
    fn();
    setOffset(0);
    selection.clear();
  };

  const runBulkAnalyze = async () => {
    const targets = analyzableSelected.map((d) => d.id);
    if (targets.length === 0) return;
    setBulk({ done: 0, total: targets.length });
    for (const [index, id] of targets.entries()) {
      try {
        await api.analyzeDocument(id);
      } catch {
        // 個別失敗は継続。状態は再取得で反映される。
      }
      setBulk({ done: index + 1, total: targets.length });
    }
    setBulk(null);
    selection.clear();
    qc.invalidateQueries({ queryKey: ["documents"] });
    qc.invalidateQueries({ queryKey: ["dashboard", "summary"] });
  };

  return (
    <div>
      <PageHeader title={t("nav.fileList")} subtitle={t("fileList.subtitle")} />
      <div className="space-y-4 p-8">
        {/* フィルタ + 検索 */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-1">
            {FILTERS.map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => resetView(() => setFilter(f))}
                aria-pressed={filter === f}
                className={cn(
                  "cursor-pointer rounded-full px-3 py-1 text-xs font-medium transition-colors",
                  filter === f
                    ? "bg-primary text-primary-foreground"
                    : "border border-border bg-card text-muted hover:bg-background"
                )}
              >
                {f === "ALL" ? t("fileList.filterAll") : t(`status.${f}`)}
              </button>
            ))}
          </div>
          <div className="relative">
            <SearchIcon
              size={15}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-muted"
              aria-hidden
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") resetView(() => setQ(search.trim()));
              }}
              onBlur={() => resetView(() => setQ(search.trim()))}
              placeholder={t("fileList.searchPlaceholder")}
              aria-label={t("fileList.searchPlaceholder")}
              className="w-56 rounded-md border border-border bg-card py-2 pl-9 pr-3 text-sm outline-none focus-visible:border-primary"
            />
          </div>
        </div>

        {/* 一括操作バー */}
        {selection.count > 0 ? (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-primary/30 bg-info-bg/40 px-4 py-2.5">
            <span className="text-sm font-medium text-foreground">
              {t("fileList.selected", { count: selection.count })}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={() => void runBulkAnalyze()}
                loading={bulk !== null}
                disabled={analyzableSelected.length === 0}
              >
                <Sparkles size={14} aria-hidden />
                {bulk
                  ? t("fileList.bulkRunning", { done: bulk.done, total: bulk.total })
                  : `${t("fileList.bulkAnalyze")} (${analyzableSelected.length})`}
              </Button>
              <Button variant="ghost" size="sm" onClick={selection.clear}>
                <X size={14} aria-hidden />
                {t("fileList.clearSelection")}
              </Button>
            </div>
          </div>
        ) : null}

        {query.isError ? (
          <ErrorState
            message={query.error instanceof ApiError ? query.error.message : "一覧の取得に失敗しました。"}
            onRetry={() => void query.refetch()}
          />
        ) : query.isPending ? (
          <Skeleton className="h-64 w-full rounded-lg" />
        ) : items.length > 0 ? (
          <>
            <Card className="overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-background text-left text-muted">
                  <tr>
                    <th className="w-10 px-4 py-3">
                      <input
                        type="checkbox"
                        checked={allSelected}
                        onChange={() => selection.toggleAll(pageIds)}
                        aria-label={t("fileList.selectAllAria")}
                        className="cursor-pointer accent-[var(--primary)]"
                      />
                    </th>
                    <th className="px-4 py-3 font-medium">{t("fileList.col.fileName")}</th>
                    <th className="px-4 py-3 font-medium">{t("fileList.col.category")}</th>
                    <th className="px-4 py-3 font-medium">{t("fileList.col.status")}</th>
                    <th className="px-4 py-3 text-right font-medium">{t("fileList.col.size")}</th>
                    <th className="px-4 py-3 font-medium">{t("fileList.col.uploadedAt")}</th>
                    <th className="px-4 py-3 text-right font-medium">{t("fileList.col.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((doc) => (
                    <Row
                      key={doc.id}
                      doc={doc}
                      selected={selection.isSelected(doc.id)}
                      onToggle={() => selection.toggle(doc.id)}
                      onAnalyze={(force) => analyze.mutate({ id: doc.id, force })}
                      onRegister={() => register.mutate(doc.id)}
                      analyzing={analyze.isPending && analyze.variables?.id === doc.id}
                      registering={register.isPending && register.variables === doc.id}
                    />
                  ))}
                </tbody>
              </table>
            </Card>

            {/* ページネーション */}
            <div className="flex items-center justify-between">
              <span className="tnum text-xs text-muted">
                {t("pager.range", {
                  start: page && page.total === 0 ? 0 : offset + 1,
                  end: offset + items.length,
                  total: formatNumber(page?.total ?? 0),
                })}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={offset === 0}
                  onClick={() => {
                    setOffset(Math.max(0, offset - LIMIT));
                    selection.clear();
                  }}
                >
                  {t("pager.prev")}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!page?.has_next}
                  onClick={() => {
                    setOffset(offset + LIMIT);
                    selection.clear();
                  }}
                >
                  {t("pager.next")}
                </Button>
              </div>
            </div>
          </>
        ) : (
          <Card>
            <div className="p-5">
              <EmptyState title={t("fileList.empty")} />
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

function Row({
  doc,
  selected,
  onToggle,
  onAnalyze,
  onRegister,
  analyzing,
  registering,
}: {
  doc: DocumentSummary;
  selected: boolean;
  onToggle: () => void;
  onAnalyze: (force: boolean) => void;
  onRegister: () => void;
  analyzing: boolean;
  registering: boolean;
}) {
  return (
    <tr className={cn("border-t border-border", selected && "bg-info-bg/30")}>
      <td className="px-4 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          aria-label={t("fileList.selectRowAria")}
          className="cursor-pointer accent-[var(--primary)]"
        />
      </td>
      <td className="max-w-[260px] px-4 py-3">
        <Link
          href={`${APP_ROUTES.documents}/${doc.id}`}
          className="block truncate font-medium text-primary hover:underline"
          title={doc.file_name}
        >
          {doc.file_name}
        </Link>
      </td>
      <td className="px-4 py-3 text-muted">{doc.category_name ?? "—"}</td>
      <td className="px-4 py-3">
        <StatusBadge status={doc.status} />
      </td>
      <td className="tnum px-4 py-3 text-right text-muted">{formatBytes(doc.file_size_bytes)}</td>
      <td className="tnum px-4 py-3 text-muted">{formatDateTime(doc.uploaded_at)}</td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-2">
          {(doc.status === "UPLOADED" || doc.status === "ERROR") && (
            <Button size="sm" loading={analyzing} onClick={() => onAnalyze(false)}>
              {t("action.analyze")}
            </Button>
          )}
          {doc.status === "ANALYZED" && (
            <Button size="sm" loading={registering} onClick={onRegister}>
              {t("action.register")}
            </Button>
          )}
        </div>
      </td>
    </tr>
  );
}
