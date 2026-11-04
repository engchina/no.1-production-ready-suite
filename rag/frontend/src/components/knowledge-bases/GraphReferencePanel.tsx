import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LocateFixed } from "lucide-react";
import { Button, CursorPagination, DataTable, Skeleton, TabPanel, Tabs, TimedLoadingState, type DataTableColumn } from "@production-ready/ui";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { api, type GraphReferenceEndpoint, type GraphReferenceItem } from "@/lib/api";
import { t } from "@/lib/i18n";
import { paginationLabels } from "@/lib/pagination-labels";

/** 選択した章節の参照関係だけを取得し、図の未表示部分を必要時に開く。 */
export function GraphReferencePanel({ kbId, nodeId, onLocate }: {
  kbId: string; nodeId: string; onLocate: (target: GraphReferenceEndpoint) => void;
}) {
  const [direction, setDirection] = useState<"outgoing" | "incoming">("outgoing");
  const [offset, setOffset] = useState(0);
  const query = useQuery({ retry: false, queryKey: ["graph-references", kbId, nodeId, direction, offset], queryFn: () => api.getKnowledgeBaseGraphReferences(kbId, nodeId, direction, offset) });
  const columns: DataTableColumn<GraphReferenceItem>[] = [
    { key: "label", header: t("knowledgeBases.graph.references"), render: (item) => <span className="break-words">{item.label}</span> },
    { key: "target", header: t(`knowledgeBases.graph.${direction}`), render: (item) => <div className="grid gap-1 break-words"><span className="text-xs text-fg-muted">{(direction === "incoming" ? item.source : item.target)?.document_name}</span><span>{(direction === "incoming" ? item.source : item.target)?.name ?? "—"}</span></div> },
    { key: "status", header: t("knowledgeBases.structure.build.status"), render: (item) => t(`knowledgeBases.graph.referenceStatus.${item.status}`) },
    { key: "locate", header: t("knowledgeBases.graph.referenceLocate"), render: (item) => {
      const target = direction === "incoming" ? item.source : item.target;
      return target ? <Button icon={LocateFixed} size="sm" variant="ghost" onClick={() => onLocate(target)}>{t("knowledgeBases.graph.referenceLocate")}</Button> : null;
    } },
  ];
  return <div className="grid min-w-0 gap-3" data-testid="graph-references">
    <p className="text-sm text-fg-muted">{t("knowledgeBases.graph.referenceScope")}</p>
    <Tabs idPrefix="graph-references" ariaLabel={t("knowledgeBases.graph.referenceList")} value={direction} onChange={(value) => { setDirection(value as "outgoing" | "incoming"); setOffset(0); }} items={[{ id: "outgoing", label: t("knowledgeBases.graph.outgoing") }, { id: "incoming", label: t("knowledgeBases.graph.incoming") }]} />
    <TabPanel idPrefix="graph-references" id={direction} value={direction}>
      {query.isPending ? <TimedLoadingState label={t("knowledgeBases.graph.referenceLoading")} operationKey={`${nodeId}:${direction}:${offset}`}><Skeleton className="h-24 w-full" /></TimedLoadingState> : query.isError ? <ApiErrorState error={query.error} fallback={t("knowledgeBases.graph.referenceError")} onRetry={() => void query.refetch()} /> : query.data.items.length ? <>
        <DataTable columns={columns} rows={query.data.items} getRowKey={(item) => item.id} />
        <CursorPagination depth={offset / 25} count={query.data.items.length} nextCursor={query.data.has_more ? String(offset + 25) : null} onPrevious={() => setOffset((value) => Math.max(0, value - 25))} onNext={() => setOffset((value) => value + 25)} labels={paginationLabels()} />
      </> : <EmptyState title={t("knowledgeBases.graph.referenceEmpty")} />}
    </TabPanel>
  </div>;
}
