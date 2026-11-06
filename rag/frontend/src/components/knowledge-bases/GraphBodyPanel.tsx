import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { Button, DataTable, ListToolbar, OffsetPagination, Skeleton, TimedLoadingState } from "@production-ready/ui";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { CitationSourceSheet } from "@/components/search/CitationSourceSheet";
import { api } from "@/lib/api";
import { t } from "@/lib/i18n";
import { listCountProps } from "@/lib/list-paging";
import { listToolbarLabels, paginationLabels } from "@/lib/pagination-labels";

/** 本文節点が指す同じ保存版の実Chunkから、既存の原文リーダーを開く。 */
export function GraphBodyPanel({ kbId, documentId, chunkSetId }: {kbId: string; documentId: string; chunkSetId: string}) {
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<{chunk_id: string; chunk_index: number} | null>(null);
  const query = useQuery({ queryKey: ["graph-body", kbId, documentId, chunkSetId, offset], queryFn: () => api.getStructureGraphBodyChunks(kbId, documentId, chunkSetId, offset), retry: false });
  const source = useQuery({ queryKey: ["graph-body-source", documentId, chunkSetId, selected?.chunk_id], queryFn: () => api.getDocumentCitationChunk(documentId, selected!.chunk_id), enabled: Boolean(selected), retry: false });
  return <div className="grid min-w-0 gap-3" data-testid="graph-body">
    {query.isPending ? <TimedLoadingState label={t("knowledgeBases.graph.bodyLoading")} operationKey={`${chunkSetId}:${offset}`}><Skeleton className="h-24 w-full" /></TimedLoadingState> : query.isError ? <ApiErrorState error={query.error} fallback={t("knowledgeBases.graph.bodyError")} onRetry={() => void query.refetch()} /> : query.data.total > 0 ? <>
      <ListToolbar count={listCountProps({matchedTotal: query.data.total, filtered: false})} labels={listToolbarLabels()} />
      <DataTable columns={[
        { key: "position", header: t("knowledgeBases.graph.body"), render: (item) => t("knowledgeBases.graph.bodyPart", {index: item.chunk_index + 1}) },
        { key: "open", header: t("knowledgeBases.graph.bodyOpen"), render: (item) => <Button icon={BookOpen} size="sm" variant="ghost" onClick={() => setSelected(item)}>{t("knowledgeBases.graph.bodyOpen")}</Button> },
      ]} rows={query.data.items} getRowKey={(item) => item.chunk_id} />
      <OffsetPagination total={query.data.total} offset={offset} limit={25} count={query.data.items.length} onPageChange={(_page, next) => setOffset(next)} labels={paginationLabels()} ariaLabel={t("knowledgeBases.graph.body")} />
    </> : <EmptyState title={t("knowledgeBases.graph.bodyEmpty")} />}
    <CitationSourceSheet title={t("knowledgeBases.graph.bodySourceTitle")} open={Boolean(selected)} onClose={() => setSelected(null)} index={selected?.chunk_index ?? 0} chunk={selected ? source.data ?? null : null} kind="search" empty={source.isError ? <ApiErrorState error={source.error} fallback={t("knowledgeBases.graph.bodyError")} onRetry={() => void source.refetch()} /> : <TimedLoadingState label={t("knowledgeBases.graph.bodyLoading")} operationKey={selected?.chunk_id ?? ""}><Skeleton className="h-24 w-full" /></TimedLoadingState>} />
  </div>;
}
