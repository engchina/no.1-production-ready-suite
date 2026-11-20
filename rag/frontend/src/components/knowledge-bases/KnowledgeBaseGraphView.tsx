"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Network } from "lucide-react";

import { DataLoadFailure } from "@/components/DataLoadStates";
import { EmptyState } from "@/components/StateViews";
import { Button, Disclosure, Skeleton, TimedLoadingState } from "@production-ready/ui";
import { KnowledgeBaseGraphCanvas } from "./KnowledgeBaseGraphCanvas";
import { queryKeys, useWikiGraphOverview } from "@/lib/queries";
import { t } from "@/lib/i18n";

/**
 * Wiki の図（#5502）: 文書 → 章節 → 原文単位と、同じ文書の依存・文書をまたぐ関連を見る読み取り専用の図。
 * Wiki ナビゲーションのカードの最下部に常に置き、開いたときだけ読む。Wiki を無効にしても保存済みの案内を出す。
 */
export function KnowledgeBaseGraphView({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const [open, setOpen] = useState(false);
  const [offset, setOffset] = useState(0);
  const query = useWikiGraphOverview(open ? knowledgeBaseId : null, offset);
  const isEmpty = query.data && (query.data.status === "empty" || query.data.documents.length === 0);

  const count = useQuery({
    queryKey: [...queryKeys.knowledgeBaseWikiGraph(knowledgeBaseId), "document-count"],
    queryFn: () => api.getWikiGraphDocumentCount(knowledgeBaseId),
    enabled: open && Boolean(query.data?.has_more),
    retry: false,
  });
  const end = offset + (query.data?.documents.length ?? 0);
  const total = query.data?.has_more ? count.data?.total : end;
  const pagination = (
    <div className="ms-auto flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto" data-testid="wiki-graph-pagination">
      {query.data?.documents.length ? <span className="whitespace-nowrap text-sm text-fg-muted" aria-live="polite">
        {total === undefined || total < end ? t("wiki.graph.rangeWithoutTotal", { start: offset + 1, end })
          : t("pager.range", { start: offset + 1, end, total })}
      </span> : null}
      <Button variant="secondary" aria-label={t("wiki.graph.previousDocuments")} disabled={offset === 0} onClick={() => setOffset(offset - 25)}>
        <span className="sm:hidden">{t("pager.prev")}</span>
        <span className="hidden sm:inline">{t("wiki.graph.previousDocuments")}</span>
      </Button>
      <Button variant="secondary" aria-label={t("wiki.graph.nextDocuments")} disabled={!query.data?.has_more} onClick={() => setOffset(offset + 25)}>
        <span className="sm:hidden">{t("pager.next")}</span>
        <span className="hidden sm:inline">{t("wiki.graph.nextDocuments")}</span>
      </Button>
    </div>
  );

  return (
    // カードの中の開閉の区画なので sunken の card にし、見出しは開閉で変えない固定の名詞、補足は description（#397 / #5480）。
    <Disclosure
      surface="sunken"
      summary={t("wiki.graph.title")}
      description={t("wiki.graph.hint")}
      icon={Network}
      open={open}
      onOpenChange={setOpen}
      data-testid="wiki-graph-disclosure"
    >
      {open ? (
        <div id="wiki-graph" className="space-y-2">
          {query.isPending ? (
            <TimedLoadingState label={t("wiki.graph.loading")} operationKey="wiki-graph-load" testId="wiki-graph-loading">
              <Skeleton className="h-[25.71rem] w-full rounded-md" />
            </TimedLoadingState>
          ) : query.isError ? (
            // 読み取れなかったことを「まだありません」と区別する（#4217）。
            <DataLoadFailure error={query.error} fallback={t("wiki.graph.error")} onRetry={() => void query.refetch()} />
          ) : isEmpty ? (
            <>{offset > 0 ? pagination : null}<EmptyState title={t("wiki.graph.empty")} /></>
          ) : query.data ? (
            <KnowledgeBaseGraphCanvas key={`${knowledgeBaseId}:${offset}`} pagination={pagination} overview={query.data} knowledgeBaseId={knowledgeBaseId} />
          ) : null}
        </div>
      ) : null}
    </Disclosure>
  );
}
