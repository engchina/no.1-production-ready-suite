import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookOpen, LocateFixed } from "lucide-react";
import { Banner, Button, DataTable, INFORMATION_TABLE_VISIBLE_ROWS, resolveContentLang, Skeleton, TimedLoadingState, type DataTableColumn } from "@production-ready/ui";
import { GraphSelectionDetails } from "@production-ready/ui/graph";

import { ApiErrorState } from "@/components/StateViews";
import { CitationSourceSheet } from "@/components/search/CitationSourceSheet";
import { api, type WikiGraphRelationItem, type WikiGraphUnitRef } from "@/lib/api";
import { t } from "@/lib/i18n";
import { WikiRelationLabel } from "./wiki-graph-labels";

/** 生成された文の一覧（想定の質問・本文に無いこと）。内容の言語を付ける。 */
function GeneratedList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) return <p className="text-sm text-fg-muted">{empty}</p>;
  return <ul className="grid list-disc gap-1 pl-5 text-sm">
    {items.map((item, index) => <li key={index} lang={resolveContentLang(undefined, item)}>{item}</li>)}
  </ul>;
}

/**
 * 選んだ原文単位の案内（要約・想定の質問・本文に無いこと）と、原文と、出る・入る関係（#5502）。
 * 案内は AI が作った検索用の文で、回答の根拠ではないことを先頭に示す。
 */
export function WikiGraphUnitPanel({ kbId, unitId, onClose, onLocate }: {
  kbId: string; unitId: string; onClose: () => void; onLocate: (unit: WikiGraphUnitRef) => void;
}) {
  const query = useQuery({ queryKey: ["wiki-graph-unit", kbId, unitId], queryFn: () => api.getWikiGraphUnit(kbId, unitId), retry: false });
  const [opened, setOpened] = useState<{ chunkId: string; index: number } | null>(null);
  const source = useQuery({
    queryKey: ["wiki-graph-source", query.data?.document_id, opened?.chunkId],
    queryFn: () => api.getDocumentCitationChunk(query.data!.document_id, opened!.chunkId),
    enabled: Boolean(query.data && opened), retry: false,
  });
  const columns: DataTableColumn<WikiGraphRelationItem>[] = [
    { key: "relation", header: t("wiki.graph.relation"), className: "whitespace-nowrap", render: (item) => <WikiRelationLabel relation={item.relation} /> },
    { key: "direction", header: t("wiki.graph.direction"), className: "min-w-[8rem] text-xs text-fg-muted", render: (item) => t(item.direction === "outgoing" ? "wiki.graph.direction.outgoing" : "wiki.graph.direction.incoming") },
    { key: "unit", header: t("wiki.graph.relatedUnit"), className: "min-w-[12rem]", render: (item) => <div className="grid gap-0.5 break-words">
      <span className="text-xs text-fg-muted">{item.scope === "cross_document" ? t("wiki.graph.otherDocument", { name: item.unit.document_name }) : t("wiki.graph.sameDocument")}</span>
      <span>{item.unit.path.length ? item.unit.path.join(" > ") : item.unit.label}</span>
      {item.reason ? <span className="text-xs text-fg-muted" lang={resolveContentLang(undefined, item.reason)}>{item.reason}</span> : null}
    </div> },
    { key: "locate", header: t("wiki.graph.locate"), render: (item) => <Button icon={LocateFixed} size="sm" variant="ghost" onClick={() => onLocate(item.unit)}>{t("wiki.graph.locate")}</Button> },
  ];
  const detail = query.data;
  return <GraphSelectionDetails
    title={t("wiki.graph.unitTitle")}
    closeLabel={t("wiki.graph.clearSelection")}
    onClose={onClose}
    fields={detail ? [
      { label: t("wiki.graph.document"), value: detail.document_name },
      { label: t("wiki.graph.sectionPath"), value: detail.path.length ? detail.path.join(" > ") : t("wiki.graph.noSection") },
    ] : []}
  >
    <div className="grid min-w-0 gap-3" data-testid="wiki-graph-unit">
      {query.isPending ? <TimedLoadingState label={t("wiki.graph.unitLoading")} operationKey={`wiki-graph-unit-${unitId}`}><Skeleton className="h-40 w-full" /></TimedLoadingState>
        : query.isError ? <ApiErrorState error={query.error} fallback={t("wiki.graph.unitError")} onRetry={() => void query.refetch()} />
        : detail ? <>
          <Banner severity="info" title={t("wiki.graph.generatedNotice")} />
          <section className="grid gap-1" aria-label={t("wiki.graph.context")}>
            <h4 className="text-xs font-semibold text-fg-muted">{t("wiki.graph.context")}</h4>
            <p className="text-sm break-words" lang={resolveContentLang(undefined, detail.context)}>{detail.context || "—"}</p>
          </section>
          <section className="grid gap-1" aria-label={t("wiki.graph.possibleQuestions")}>
            <h4 className="text-xs font-semibold text-fg-muted">{t("wiki.graph.possibleQuestions")}</h4>
            <GeneratedList items={detail.possible_questions} empty={t("wiki.graph.none")} />
          </section>
          <section className="grid gap-1" aria-label={t("wiki.graph.unresolved")}>
            <h4 className="text-xs font-semibold text-fg-muted">{t("wiki.graph.unresolved")}</h4>
            <GeneratedList items={detail.unresolved} empty={t("wiki.graph.none")} />
          </section>
          <section className="grid gap-1" aria-label={t("wiki.graph.source")}>
            <h4 className="text-xs font-semibold text-fg-muted">{t("wiki.graph.source")}</h4>
            <div className="flex flex-wrap gap-2">
              {detail.source_chunks.map((chunk, index) => <Button key={chunk.chunk_id} icon={BookOpen} size="sm" variant="secondary" onClick={() => setOpened({ chunkId: chunk.chunk_id, index: chunk.chunk_index })}>
                {detail.source_chunks.length > 1 ? t("wiki.graph.openSourcePart", { index: index + 1 }) : t("wiki.graph.openSource")}
              </Button>)}
            </div>
          </section>
          <section className="grid min-w-0 gap-1" aria-label={t("wiki.graph.relations")}>
            <h4 className="text-xs font-semibold text-fg-muted">{t("wiki.graph.relations")}</h4>
            {detail.relations.length ? <DataTable ariaLabel={t("wiki.graph.relations")} columns={columns} rows={detail.relations} getRowKey={(item) => `${item.direction}:${item.scope}:${item.unit.id}`} stickyHeader visibleRows={INFORMATION_TABLE_VISIBLE_ROWS} />
              : <p className="text-sm text-fg-muted">{t("wiki.graph.noRelations")}</p>}
          </section>
          <CitationSourceSheet showScores={false} kind="search" title={t("wiki.graph.sourceTitle", { name: detail.label || detail.document_name })} open={Boolean(opened)} onClose={() => setOpened(null)} index={opened?.index ?? 0}
            chunk={opened ? source.data ?? null : null}
            empty={source.isError ? <ApiErrorState error={source.error} fallback={t("wiki.graph.sourceError")} onRetry={() => void source.refetch()} /> : <TimedLoadingState label={t("wiki.graph.sourceLoading")} operationKey={opened?.chunkId ?? ""}><Skeleton className="h-24 w-full" /></TimedLoadingState>} />
        </> : null}
    </div>
  </GraphSelectionDetails>;
}
