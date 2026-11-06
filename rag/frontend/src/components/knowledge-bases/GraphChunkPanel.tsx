import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen } from "lucide-react";
import { Button, DataTable, DEFAULT_PAGE_SIZE, INFORMATION_TABLE_VISIBLE_ROWS, ListToolbar, OffsetPagination, Skeleton, TimedLoadingState, type DataTableColumn } from "@production-ready/ui";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { CitationSourceSheet } from "@/components/search/CitationSourceSheet";
import { api, type StructureGraphChunk } from "@/lib/api";
import { t } from "@/lib/i18n";
import { listCountProps } from "@/lib/list-paging";
import { listToolbarLabels, paginationLabels } from "@/lib/pagination-labels";

/** 章節・本文の節点に結んだ同じ保存版の実チャンクを保存順に並べ、既存の原文リーダーを開く（#4544）。 */
export function GraphChunkPanel({ kbId, documentId, chunkSetId, nodeId, nodeLabel }: {
  kbId: string; documentId: string; chunkSetId: string; nodeId: string; nodeLabel: string;
}) {
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(DEFAULT_PAGE_SIZE);
  const [selected, setSelected] = useState<StructureGraphChunk | null>(null);
  const query = useQuery({ queryKey: ["graph-chunks", kbId, documentId, chunkSetId, nodeId, offset, limit], queryFn: () => api.getStructureGraphEntityChunks(kbId, documentId, chunkSetId, nodeId, offset, limit), retry: false });
  const source = useQuery({ queryKey: ["graph-chunk-source", documentId, chunkSetId, selected?.chunk_id], queryFn: () => api.getDocumentCitationChunk(documentId, selected!.chunk_id), enabled: Boolean(selected), retry: false });
  const columns: DataTableColumn<StructureGraphChunk>[] = [
    { key: "position", header: t("knowledgeBases.graph.chunks"), render: (item) => t("knowledgeBases.graph.chunkPart", { index: item.chunk_index + 1 }) },
    { key: "page", header: t("knowledgeBases.graph.chunkPageHeader"), render: (item) => item.page_number == null ? "—" : t("knowledgeBases.graph.chunkPage", { page: item.page_number }) },
    { key: "open", header: t("knowledgeBases.graph.chunkOpen"), render: (item) => <Button icon={BookOpen} size="sm" variant="ghost" onClick={() => setSelected(item)}>{t("knowledgeBases.graph.chunkOpen")}</Button> },
  ];
  return <div className="grid min-w-0 gap-3" data-testid="graph-chunks">
    <p className="text-sm text-fg-muted">{t("knowledgeBases.graph.chunkScope")}</p>
    {query.isPending ? <TimedLoadingState label={t("knowledgeBases.graph.chunkLoading")} operationKey={`${nodeId}:${offset}:${limit}`}><Skeleton className="h-24 w-full" /></TimedLoadingState> : query.isError ? <ApiErrorState error={query.error} fallback={t("knowledgeBases.graph.chunkError")} onRetry={() => void query.refetch()} /> : query.data.total > 0 ? <>
      <ListToolbar count={listCountProps({ matchedTotal: query.data.total, filtered: false })} labels={listToolbarLabels()} />
      <DataTable ariaLabel={t("knowledgeBases.graph.chunks")} columns={columns} rows={query.data.items} getRowKey={(item) => item.chunk_id} stickyHeader visibleRows={INFORMATION_TABLE_VISIBLE_ROWS} />
      <OffsetPagination total={query.data.total} offset={offset} limit={limit} count={query.data.items.length} onPageChange={(_page, next) => setOffset(next)} onPageSizeChange={(next) => { setLimit(next); setOffset(0); }} labels={paginationLabels()} ariaLabel={t("knowledgeBases.graph.chunks")} />
    </> : <EmptyState title={t("knowledgeBases.graph.chunkEmpty")} />}
    <CitationSourceSheet showScores={false} title={t("knowledgeBases.graph.chunkSourceTitle", { name: nodeLabel })} open={Boolean(selected)} onClose={() => setSelected(null)} index={selected?.chunk_index ?? 0} chunk={selected ? source.data ?? null : null} kind="search" empty={source.isError ? <ApiErrorState error={source.error} fallback={t("knowledgeBases.graph.chunkError")} onRetry={() => void source.refetch()} /> : <TimedLoadingState label={t("knowledgeBases.graph.chunkLoading")} operationKey={selected?.chunk_id ?? ""}><Skeleton className="h-24 w-full" /></TimedLoadingState>} />
  </div>;
}
