import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, BookOpen, Focus, GitFork, Minus, Plus } from "lucide-react";
import { Banner, Button, ListScrollRegion, OffsetPagination, Skeleton, TabPanel, Tabs, useMediaQuery } from "@production-ready/ui";
import { t } from "@/lib/i18n";
import { type WikiGraphRelation, type WikiGraphRelationItem } from "@/lib/api";
import { WikiRelationLabel, wikiRelationText } from "@/components/knowledge-bases/wiki-graph-labels";
import { ApiErrorState } from "@/components/StateViews";
import { paginationLabels } from "@/lib/pagination-labels";
import { WIKI_GRAPH_SETTINGS, WIKI_PAGE_SIZE_OPTIONS } from "./wiki-graph-settings";
import { relationsKey, treeNode, unitNode } from "./wiki-graph-loaders";
import type { WikiGraphEdge, WikiGraphNode } from "./wiki-graph-model";
import { wikiNodeTitle } from "./WikiGraphView";
import {
  sourceLocation,
  sourcePick,
  MAX_WIKI_SELECTION,
  wikiApi,
  type WikiDirection,
  type WikiPick,
  type WikiScope,
} from "./wiki-api";

function edgeName(edge: WikiGraphEdge) {
  return edge.kind === "contains" ? t("wiki.search.children") : edge.kind === "more" ? t("wiki.graph.kind.more") : wikiRelationText(edge.kind);
}

/** 選んだ関係の詳細（種類・範囲・始点と終点）。 */
export function RelationInspector({ edge, nodes, onPick }: { edge: WikiGraphEdge; nodes: ReadonlyMap<string, WikiGraphNode>; onPick: (node: WikiGraphNode) => void }) {
  const ends = [[t("wiki.graph.from"), edge.source], [t("wiki.graph.to"), edge.target]] as const;
  return (
    <div className="grid gap-3 p-3" data-testid="wiki-edge-detail">
      <p className="text-xs text-fg-muted">{t("wiki.graph.relation")}</p>
      <h3 className="text-base font-semibold">{edgeName(edge)}</h3>
      <dl className="grid gap-2 text-sm">
        {edge.scope && <div className="grid gap-1"><dt className="text-fg-muted">{t("wiki.graph.scope")}</dt><dd>{t(`wiki.graph.scope.${edge.scope}`)}</dd></div>}
        {edge.count !== undefined && <div className="grid gap-1"><dt className="text-fg-muted">{t("wiki.graph.relation")}</dt><dd>{t("wiki.graph.linkCount", { count: edge.count })}</dd></div>}
        {ends.map(([label, id]) => {
          const node = nodes.get(id);
          return node ? (
            <div key={label} className="grid gap-1">
              <dt className="text-fg-muted">{label}</dt>
              <dd><Button variant="secondary" size="sm" className="max-w-full justify-start" onClick={() => onPick(node)}>{wikiNodeTitle(node)}</Button></dd>
            </div>
          ) : null;
        })}
      </dl>
    </div>
  );
}

/** 複数の選択（範囲選択・Shift のクリック）。 */
export function SelectionSummary({ nodes, onPick }: { nodes: WikiGraphNode[]; onPick: (node: WikiGraphNode) => void }) {
  return (
    <div className="grid gap-2 p-3">
      <h3 className="text-base font-semibold">{t("wiki.graph.multiSelected", { count: nodes.length })}</h3>
      <ListScrollRegion label={t("wiki.graph.multiSelected", { count: nodes.length })}>
        <ul className="divide-y divide-border">
          {nodes.map((node) => (
            <li key={node.id}>
              <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => onPick(node)}>
                {t(`wiki.graph.kind.${node.kind}`)} · {node.label}
              </Button>
            </li>
          ))}
        </ul>
      </ListScrollRegion>
    </div>
  );
}

interface Props {
  scope: WikiScope;
  node: WikiGraphNode;
  remaining: number;
  expanded: boolean;
  focus?: () => void;
  select: (node: WikiGraphNode) => void;
  expand: () => void;
  collapse: () => void;
  picks?: WikiPick[];
  toggle?: (pick: WikiPick) => void;
}

/** 選んだノードの詳細。単位は原文・関係・案内、文書と章節は構成、「さらに展開」は残りの件数。 */
export function WikiInspector({ scope, node, remaining, expanded, focus, select, expand, collapse, picks, toggle }: Props) {
  const mobile = useMediaQuery("(max-width: 767px)", false);
  const idPrefix = mobile ? "wiki-sheet-tabs" : "wiki-detail-tabs";
  const unit = node.kind === "unit";
  const [tab, setTab] = useState(unit ? "source" : "children");
  const [more, setMore] = useState(false);
  const [filter, setFilter] = useState<{ relation: WikiGraphRelation; direction: WikiDirection } | null>(null);
  const [offset, setOffset] = useState(0);
  const [pageSize, setPageSize] = useState<number>(WIKI_GRAPH_SETTINGS.resultPage);
  const original = useQuery({
    queryKey: ["wiki-explorer", scope, "original", node.id],
    queryFn: ({ signal }) => wikiApi.original(scope, node.id, signal),
    enabled: unit,
    retry: false,
  });
  // 関係は「関係」のタブを開いたときに読む（選ぶたびに読むと、原文・線の照会と重なって DB の読み取りの
  // 上限に当たる。#5966）。展開は別に読み、同じ鍵の結果を使い回す。
  const relations = useQuery({
    queryKey: relationsKey(scope, node.id),
    queryFn: ({ signal }) => wikiApi.allRelations(scope, node.id, signal),
    staleTime: 60_000,
    enabled: unit && tab === "relations",
    retry: false,
  });
  const children = useQuery({
    queryKey: ["wiki-explorer", scope, "children", node.id, offset, pageSize],
    queryFn: ({ signal }) => wikiApi.tree(scope, node.document_id, node.kind === "section" ? node.id : null, offset, pageSize, signal),
    enabled: node.kind === "document" || node.kind === "section",
    retry: false,
  });
  const chosen = picks?.some((item) => item.id === node.id) ?? false;
  const validAdd = chosen || (picks?.length ?? 0) < MAX_WIKI_SELECTION;
  const items = (relations.data?.items ?? []).filter((row) => !filter || (row.relation === filter.relation && row.direction === filter.direction));
  const groups = new Map<string, { relation: WikiGraphRelation; direction: WikiDirection; count: number }>();
  for (const row of relations.data?.items ?? []) {
    const key = row.direction + row.relation;
    const group = groups.get(key) ?? { relation: row.relation, direction: row.direction, count: 0 };
    group.count += 1;
    groups.set(key, group);
  }
  const outside = scope.kind === "kb" ? t("wiki.graph.outsideKnowledgeBase") : t("wiki.graph.outsideScope");
  const changeFilter = (next: typeof filter) => { setFilter(next); setOffset(0); };

  if (node.kind === "more") {
    return (
      <div className="grid gap-3 p-3">
        <p className="text-xs text-fg-muted">{t("wiki.graph.kind.more")}</p>
        <p className="text-sm">{remaining ? t("wiki.graph.moreDetail", { count: remaining }) : t("wiki.graph.moreNodeUnknown")}</p>
        <Button variant="secondary" size="sm" icon={GitFork} className="w-fit" onClick={expand}>{t("wiki.graph.expandMore")}</Button>
      </div>
    );
  }

  function relationRow(row: WikiGraphRelationItem, index: number) {
    return (
      <li key={[row.direction, row.relation, row.unit?.id ?? row.target_name, index].join(":")} className="grid gap-1 py-1.5">
        <span className="inline-flex items-center gap-1 text-xs text-fg-muted">
          {row.direction === "outgoing" ? <ArrowRight size={14} aria-hidden /> : <ArrowLeft size={14} aria-hidden />}
          <WikiRelationLabel relation={row.relation} />
        </span>
        {row.unit ? (
          <Button variant="ghost" size="sm" className="w-full justify-start" onClick={() => select(unitNode(row.unit!))}>{row.unit.label}</Button>
        ) : (
          <p className="text-sm">{[row.target_name, row.target_section].filter(Boolean).join(" · ")} · {outside}</p>
        )}
        {row.unit && <p className="truncate text-xs text-fg-muted">{row.unit.document_name}</p>}
      </li>
    );
  }

  const detailTabs = unit
    ? [{ id: "source", label: t("wiki.graph.source") }, { id: "relations", label: t("wiki.graph.relation") }, { id: "guide", label: t("wiki.search.guide") }]
    : [{ id: "children", label: t("wiki.search.children") }];
  const loading = (rows: number) => <div className="grid gap-2" aria-hidden>{Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-10 w-full" />)}</div>;
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col [overflow-wrap:anywhere]" data-testid="wiki-node-detail">
      <div className="space-y-1 p-3">
        <p className="text-xs text-fg-muted">{t(`wiki.graph.kind.${node.kind}`)}</p>
        <h3 className="text-base font-semibold">{node.label}</h3>
        <p className="text-xs text-fg-muted">
          {[node.document_name, ...(original.data?.path ?? node.path ?? [])].filter((v, i, all) => v && (i === 0 || v !== all[i - 1])).join(" › ")}
        </p>
        {node.references ? <p className="text-xs text-fg-muted">{t("wiki.graph.references", { count: node.references })}</p> : null}
      </div>
      <Tabs items={detailTabs} idPrefix={idPrefix} value={tab} onChange={setTab} ariaLabel={t("wiki.search.detail")} />
      <div className="relative min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-3">
        {unit && (
          <TabPanel id="source" value={tab} idPrefix={idPrefix}>
            {original.isPending ? loading(3) : original.isError ? (
              <ApiErrorState error={original.error} fallback={t("wiki.graph.sourceError")} onRetry={() => void original.refetch()} />
            ) : (
              <div className="space-y-3">
                {original.data.chunks.slice(0, more ? undefined : 1).map((chunk) => (
                  <div key={chunk.chunk_id}>
                    <p className="mb-1 flex items-start gap-1 text-xs text-fg-muted">
                      <BookOpen size={14} className="shrink-0" aria-hidden />
                      <span className="min-w-0">{original.data.document_name} · {sourceLocation(chunk, original.data.label)}</span>
                    </p>
                    <blockquote className="whitespace-pre-wrap break-words border-l-2 border-accent-fg bg-surface-sunken p-3 text-sm">{chunk.text}</blockquote>
                  </div>
                ))}
                {original.data.chunks.length > 1 && (
                  <Button variant="ghost" size="sm" icon={BookOpen} onClick={() => setMore(!more)}>{t(more ? "wiki.graph.close" : "wiki.search.context")}</Button>
                )}
              </div>
            )}
          </TabPanel>
        )}
        {unit && (
          <TabPanel id="relations" value={tab} idPrefix={idPrefix}>
            {relations.isPending ? loading(4) : relations.isError ? (
              <ApiErrorState error={relations.error} fallback={t("wiki.graph.error")} onRetry={() => void relations.refetch()} />
            ) : (
              <div className="space-y-2">
                {groups.size > 0 && (
                  <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("wiki.search.directRelations")}>
                    {[...groups.values()].map((group) => {
                      const active = filter?.relation === group.relation && filter.direction === group.direction;
                      return (
                        <Button key={group.direction + group.relation} size="sm" variant="secondary" icon={group.direction === "outgoing" ? ArrowRight : ArrowLeft}
                          aria-label={`${t(group.direction === "outgoing" ? "wiki.search.outgoing" : "wiki.search.incoming")} · ${wikiRelationText(group.relation)} ${group.count}`}
                          pressed={active} onClick={() => changeFilter(active ? null : group)}>
                          {wikiRelationText(group.relation)} {group.count}
                        </Button>
                      );
                    })}
                  </div>
                )}
                {!items.length && <p className="text-sm text-fg-muted">{t("wiki.graph.noRelations")}</p>}
                {items.length > 0 && (
                  <ListScrollRegion label={t("wiki.search.relationsList")} testId="wiki-relations-list">
                    <ul className="divide-y divide-border">{items.slice(offset, offset + pageSize).map(relationRow)}</ul>
                  </ListScrollRegion>
                )}
                <OffsetPagination offset={offset} limit={pageSize} total={items.length} count={items.slice(offset, offset + pageSize).length}
                  onPageChange={(_, next) => setOffset(next)} onPageSizeChange={(size) => { setPageSize(size); setOffset(0); }}
                  pageSizeOptions={[...WIKI_PAGE_SIZE_OPTIONS]} labels={paginationLabels()} ariaLabel={t("wiki.search.relationsList")} />
              </div>
            )}
          </TabPanel>
        )}
        {!unit && (
          <TabPanel id="children" value={tab} idPrefix={idPrefix}>
            {children.isPending ? loading(4) : children.isError ? (
              <ApiErrorState error={children.error} fallback={t("wiki.graph.error")} onRetry={() => void children.refetch()} />
            ) : (
              <div className="space-y-2">
                {children.data.items.length === 0 && <p className="text-sm text-fg-muted">{t("wiki.graph.noRelations")}</p>}
                {children.data.items.length > 0 && (
                  <ListScrollRegion label={t("wiki.search.childrenList")} testId="wiki-children-list">
                    <ul className="divide-y divide-border">
                      {children.data.items.map((item, index) => {
                        const position = [item.page && `p. ${item.page}`, item.line && t("wiki.search.lines", { range: String(item.line) })].filter(Boolean).join(" · ");
                        const title = item.kind === "section" ? item.label : item.label || position || t("wiki.search.unitNumber", { number: offset + index + 1 });
                        return (
                          <li key={item.id}>
                            <button type="button" className="grid w-full gap-0.5 px-3 py-2 text-left hover:bg-surface-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring" onClick={() => select(treeNode(item))}>
                              <span className="truncate text-sm">{title}</span>
                              {item.excerpt && <span className="line-clamp-2 whitespace-normal text-xs text-fg-muted">{item.excerpt}</span>}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </ListScrollRegion>
                )}
                <OffsetPagination offset={offset} limit={pageSize} total={children.data.total} count={children.data.items.length}
                  onPageChange={(_, next) => setOffset(next)} onPageSizeChange={(size) => { setPageSize(size); setOffset(0); }}
                  pageSizeOptions={[...WIKI_PAGE_SIZE_OPTIONS]} labels={paginationLabels()} ariaLabel={t("wiki.search.childrenList")} />
              </div>
            )}
          </TabPanel>
        )}
        {unit && (
          <TabPanel id="guide" value={tab} idPrefix={idPrefix}>
            {original.data && (
              <div className="space-y-3">
                <Banner severity="info">{t("wiki.graph.generatedNotice")}</Banner>
                <p className="text-sm">{original.data.context}</p>
                {([["wiki.graph.possibleQuestions", original.data.possible_questions], ["wiki.graph.unresolved", original.data.unresolved]] as const).map(([key, list]) =>
                  list.length > 0 && (
                    <section key={key}>
                      <h4 className="font-semibold">{t(key)}</h4>
                      <ul className="list-disc pl-5">{list.map((s) => <li key={s}>{s}</li>)}</ul>
                    </section>
                  ))}
              </div>
            )}
          </TabPanel>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-border px-3 py-2">
        {focus && <Button variant="secondary" size="sm" icon={Focus} onClick={focus}>{t("wiki.search.focus")}</Button>}
        {unit && toggle && original.data && (
          <Button variant="secondary" size="sm" icon={chosen ? Minus : Plus} disabled={!validAdd}
            tooltip={!validAdd ? t("wiki.search.selectionLimit", { count: MAX_WIKI_SELECTION }) : undefined}
            onClick={() => toggle(sourcePick(original.data))}>
            {t(chosen ? "wiki.search.remove" : "wiki.search.add")}
          </Button>
        )}
        {expanded && <Button variant="secondary" size="sm" icon={Minus} onClick={collapse}>{t("wiki.search.collapse")}</Button>}
        <Button variant="secondary" size="sm" icon={GitFork} onClick={expand}>
          {remaining > 0 && expanded ? t("wiki.graph.expandMore") : t(unit ? "wiki.search.expand" : "wiki.search.expandStructure")}
        </Button>
      </div>
    </div>
  );
}
