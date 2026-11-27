import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { BookOpen, Eraser, List, Maximize, Minimize, Network, PanelRight, Search, X } from "lucide-react";
import type { GraphExplorerHandle } from "@production-ready/ui/graph-explorer";
import {
  Button,
  Card,
  ListCount,
  ListScrollRegion,
  ListSkeleton,
  OffsetPagination,
  ProcessingIndicator,
  SearchField,
  SideSheet,
  Skeleton,
  TabPanel,
  Tabs,
  staleContentClass,
  useMediaQuery,
} from "@production-ready/ui";
import { DataLoadFailure } from "@/components/DataLoadStates";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { t } from "@/lib/i18n";
import { paginationLabels } from "@/lib/pagination-labels";
import { WIKI_GRAPH_SETTINGS, WIKI_PAGE_SIZE_OPTIONS } from "./wiki-graph-settings";
import { edgeKey, moreId, remainingNeighbors, type WikiGraphNode } from "./wiki-graph-model";
import { documentLinks, documentNode, unitNode } from "./wiki-graph-loaders";
import { useWikiGraph } from "./use-wiki-graph";
import { WikiGraphView } from "./WikiGraphView";
import { RelationInspector, SelectionSummary, WikiInspector } from "./WikiInspector";
import { wikiApi, type WikiKb, type WikiMethod, type WikiPick, type WikiScope } from "./wiki-api";

interface Props {
  scope: WikiScope;
  question: string;
  method: WikiMethod;
  kbs: WikiKb[];
  picks?: WikiPick[];
  toggle?: (p: WikiPick) => void;
  initialSelection?: string;
  onSelectUnit: (id: string) => void;
  initialOffset: number;
  onOffset: (offset: number) => void;
  /** 検索のボタンが同じ読み込みで loading のとき（経過の行の回転を止める。buttons.md §8）。 */
  searchButtonBusy?: boolean;
}

// 詳細を図の右に並べても図の操作領域を保てる幅。サイドバーを除いた実寸で判定する。
const SPLIT_MIN_WIDTH = 768;

/**
 * Wiki の関係図と一覧（#5922）。検索した結果（Wiki 検索）と、ナレッジベース詳細の参照の多い文書の見本を、
 * 共有の Cytoscape の画布に出す。一覧は画布の内容を読み上げで追える代わりの表示。
 */
export function WikiExplorer({ scope, question, method, kbs, picks, toggle, initialSelection, onSelectUnit, initialOffset, onOffset, searchButtonBusy = false }: Props) {
  const base = ["wiki-explorer", scope] as const;
  const searchMode = Boolean(question.trim());
  const knowledgeBase = scope.kind === "kb";
  const box = useRef<HTMLDivElement>(null);
  const camera = useRef<GraphExplorerHandle>(null);
  const [narrow, setNarrow] = useState(false);
  const wideScreen = useMediaQuery("(min-width: 768px)", false);
  const [view, setView] = useState("graph");
  const [offset, setOffset] = useState(initialOffset);
  const [pageSize, setPageSize] = useState<number>(WIKI_GRAPH_SETTINGS.resultPage);
  const [selection, setSelection] = useState<string[]>(initialSelection ? [initialSelection] : []);
  const [inspector, setInspector] = useState(true);
  const [sheet, setSheet] = useState(false);
  const [full, setFull] = useState(false);
  const [fullError, setFullError] = useState(false);
  const [find, setFind] = useState("");
  const [cursor, setCursor] = useState(0);
  const graph = useWikiGraph(scope);

  useLayoutEffect(() => {
    const panel = box.current;
    if (!panel) return;
    const measure = () => setNarrow(panel.clientWidth < SPLIT_MIN_WIDTH);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const sync = () => setFull(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  const hits = useQuery({
    queryKey: [...base, "search", question, method, offset, pageSize],
    queryFn: ({ signal }) => wikiApi.hits(scope, question, method, offset, pageSize, signal),
    placeholderData: keepPreviousData,
    enabled: searchMode,
    retry: false,
  });
  const sample = useQuery({
    queryKey: [...base, "document-sample", WIKI_GRAPH_SETTINGS.sampleDocuments],
    queryFn: ({ signal }) => wikiApi.documentGraph(scope, 0, WIKI_GRAPH_SETTINGS.sampleDocuments, undefined, signal),
    enabled: !searchMode,
    retry: false,
  });
  const docs = useQuery({
    queryKey: [...base, "docs", offset, pageSize],
    queryFn: ({ signal }) => wikiApi.documents(scope, offset, pageSize, signal),
    placeholderData: keepPreviousData,
    enabled: !searchMode && view === "list",
    retry: false,
  });
  const count = useQuery({
    queryKey: [...base, "document-count"],
    queryFn: ({ signal }) => wikiApi.docCount(scope, signal),
    enabled: !searchMode && view === "list",
    retry: false,
  });
  const found = useQuery({
    queryKey: [...base, "find", find],
    queryFn: ({ signal }) => wikiApi.hits(scope, find, "keyword", 0, WIKI_GRAPH_SETTINGS.resultPage, signal),
    enabled: knowledgeBase && !searchMode && Boolean(find.trim()),
    retry: false,
  });

  // 検索の結果（ページ）ごとに図を置き換え、結果の間の関係をつなぐ（Wiki 検索は新しい検索で置き換える）。
  const placed = useRef("");
  const { replace, add, run, reveal } = graph;
  useEffect(() => {
    if (!searchMode || !hits.data || hits.isPlaceholderData) return;
    const key = JSON.stringify([question, method, offset, pageSize]);
    if (placed.current === key) return;
    placed.current = key;
    const nodes = hits.data.items.map((hit) => unitNode(hit));
    const ids = nodes.map((node) => node.id);
    // 置き換えると図のすべてが結果なので、一致の輪は付けない（足すときだけ、今の図の中の結果を強調する）。
    if (WIKI_GRAPH_SETTINGS.wikiSearch === "replace") replace(nodes, []);
    else add(nodes, [], ids);
    if (ids.length > 1) {
      void run(async () => {
        const links = await wikiApi.connections(scope, ids);
        add([], links.connections.map((link) => ({
          id: edgeKey(link.source_id, link.relation, link.target_id), source: link.source_id, target: link.target_id, kind: link.relation, scope: link.scope,
        })));
      });
    }
  }, [add, hits.data, hits.isPlaceholderData, method, offset, pageSize, question, replace, run, scope, searchMode]);
  // ナレッジベース詳細は、参照の多い文書の見本とその間の参照で開く。
  useEffect(() => {
    if (searchMode || !sample.data || placed.current === "sample") return;
    placed.current = "sample";
    replace(sample.data.documents.map(documentNode), documentLinks(sample.data));
  }, [replace, sample.data, searchMode]);
  // 図内検索の当たりを今の図に足し、強調する（ナレッジベース詳細。#5881 で消えた検索）。
  const added = useRef("");
  useEffect(() => {
    if (!found.data || added.current === find) return;
    added.current = find;
    const nodes = found.data.items.map((hit) => unitNode(hit));
    if (WIKI_GRAPH_SETTINGS.knowledgeBaseSearch === "replace") replace(nodes, [], nodes.map((node) => node.id));
    else add(nodes, [], nodes.map((node) => node.id));
    setCursor(0);
    if (nodes[0]) void run(async () => { await reveal(nodes[0]); camera.current?.focusNode(nodes[0].id); setSelection([nodes[0].id]); });
  }, [add, find, found.data, replace, reveal, run]);

  function moveMatch(step: number) {
    const items = found.data?.items ?? [];
    if (!items.length) return;
    const next = (cursor + step + items.length) % items.length;
    setCursor(next);
    const node = unitNode(items[next]);
    void run(async () => { await reveal(node); camera.current?.focusNode(node.id); setSelection([node.id]); });
  }

  const nodes = graph.state.nodes;
  const selectedNodes = selection.map((id) => nodes.get(id) ?? (id.startsWith("more:") ? moreNode(id) : undefined)).filter((node): node is WikiGraphNode => Boolean(node));
  const selectedEdge = selection.length === 1 ? graph.state.edges.get(selection[0]) : undefined;
  const node = selectedNodes.length === 1 ? selectedNodes[0] : undefined;
  function moreNode(id: string): WikiGraphNode | undefined {
    const owner = nodes.get(id.slice("more:".length));
    return owner ? { id, kind: "more", label: "", owner: owner.id, remaining: remainingNeighbors(graph.state, owner.id), document_id: owner.document_id, document_name: owner.document_name } : undefined;
  }

  function choose(ids: string[]) {
    setSelection(ids);
    const unit = ids.length === 1 ? nodes.get(ids[0]) : undefined;
    onSelectUnit(unit?.kind === "unit" ? unit.id : "");
  }
  /** 一覧・詳細から選ぶ。図に無ければ足してから選ぶ。 */
  function pick(item: WikiGraphNode) {
    if (!nodes.has(item.id)) add([item], []);
    choose([item.id]);
    setInspector(true);
    if (narrow) setSheet(true);
    requestAnimationFrame(() => camera.current?.focusNode(item.id));
  }
  function changePage(next: number) {
    setOffset(next);
    onOffset(next);
    setSelection([]);
  }
  async function toggleFull() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await box.current?.requestFullscreen();
    } catch {
      setFullError(true);
    }
  }

  const listRows: WikiGraphNode[] = searchMode
    ? (hits.data?.items ?? []).map((hit) => unitNode(hit))
    : (docs.data?.documents ?? []).map((doc) => documentNode(doc));
  const listTotal = searchMode ? hits.data?.total : docs.data ? (docs.data.has_more ? count.data?.total : offset + listRows.length) : undefined;
  const mainQuery = searchMode ? hits : sample;
  const listQuery = searchMode ? hits : docs;
  const workingLabel = mainQuery.isFetching ? t(searchMode ? "wiki.search.loadingHits" : "wiki.graph.loadingSample")
    : found.isFetching ? t("wiki.graph.searching")
    : graph.pending ? t("wiki.graph.expanding")
    : view === "list" && listQuery.isFetching ? t("wiki.search.loadingDocs") : undefined;
  const title = searchMode ? t("wiki.search.results") : t("wiki.graph.browseTitle");
  const showInspector = !narrow && inspector && view === "graph" || !narrow && inspector && view === "list";

  const detail = selectedEdge ? (
    <RelationInspector edge={selectedEdge} nodes={nodes} onPick={pick} />
  ) : node ? (
    <WikiInspector
      key={node.id}
      scope={scope}
      node={node}
      remaining={remainingNeighbors(graph.state, node.kind === "more" ? node.owner! : node.id)}
      expanded={[...graph.state.owners.values()].includes(node.id)}
      focus={view === "graph" && nodes.has(node.id) ? () => { camera.current?.focusNode(node.id); if (narrow) setSheet(false); } : undefined}
      select={pick}
      expand={() => { if (!nodes.has(node.id) && node.kind !== "more") add([node], []); graph.expand(node.kind === "more" ? moreId(node.owner!) : node.id); setView("graph"); if (narrow) setSheet(false); }}
      collapse={() => graph.collapse(node.id)}
      picks={picks}
      toggle={toggle}
    />
  ) : selectedNodes.length > 1 ? (
    <SelectionSummary nodes={selectedNodes} onPick={pick} />
  ) : (
    <p className="p-3 text-sm text-fg-muted">{t("wiki.search.selectHint")}</p>
  );

  const toolbar = knowledgeBase && !searchMode ? (
    <>
      <div className="w-full min-w-0 sm:w-auto" data-testid="wiki-graph-find">
        <SearchField id="wiki-graph-find" label={t("wiki.graph.search")} labelHidden width="lg" value={find}
          onSearch={(value) => { setFind(value.trim()); added.current = ""; }} debounceMs={400}
          placeholder={t("wiki.graph.search")} clearLabel={t("wiki.search.clearFind")} aria-describedby="wiki-graph-find-scope" />
      </div>
      {find && found.data && (
        <div className="flex items-center gap-1" data-testid="wiki-graph-find-nav">
          <span className="px-1 text-xs tabular-nums text-fg-muted" aria-live="polite">
            {found.data.items.length ? `${cursor + 1} / ${found.data.items.length}` : t("wiki.graph.noMatch")}
          </span>
          <Button size="md" variant="secondary" disabled={!found.data.items.length} onClick={() => moveMatch(-1)}>{t("wiki.graph.previousMatch")}</Button>
          <Button size="md" variant="secondary" disabled={!found.data.items.length} onClick={() => moveMatch(1)}>{t("wiki.graph.nextMatch")}</Button>
        </div>
      )}
      <Button size="md" variant="secondary" icon={Eraser} disabled={!nodes.size} onClick={() => { graph.clear(); setSelection([]); }}>{t("wiki.graph.clearCanvas")}</Button>
      <p id="wiki-graph-find-scope" className="w-full text-xs text-fg-muted">{t("wiki.graph.searchScope")}</p>
    </>
  ) : null;

  const graphPanel = mainQuery.isPending ? (
    // 読み込み中は画布の形の Skeleton（凡例の行と画布）。
    <div className="grid gap-2" aria-hidden data-testid="wiki-graph-skeleton">
      <Skeleton className="h-9 w-2/3 max-w-[32rem]" />
      <Skeleton className="h-[40rem] w-full max-sm:h-[65dvh]" />
    </div>
  ) : mainQuery.isError && !mainQuery.data ? (
    <ApiErrorState error={mainQuery.error} fallback={t("wiki.graph.error")} onRetry={() => void mainQuery.refetch()} />
  ) : (
    <div className="grid gap-2">
      {!searchMode && sample.data && sample.data.total > 0 && (
        <p className="text-xs text-fg-muted">{t("wiki.graph.sampleHint", { count: sample.data.documents.length, total: sample.data.total })}</p>
      )}
      {graph.failure && <DataLoadFailure error={graph.failure.error} fallback={t("wiki.graph.expandError")} onRetry={graph.failure.retry} />}
      {found.isError && <DataLoadFailure error={found.error} fallback={t("wiki.graph.searchError")} onRetry={() => void found.refetch()} />}
      {!nodes.size && !searchMode && sample.data?.total === 0 ? (
        <EmptyState title={t("wiki.graph.empty")} />
      ) : !nodes.size && searchMode && hits.data?.total === 0 ? (
        <EmptyState title={t("search.noResults")} />
      ) : (
        <>
          {!nodes.size && !searchMode && <p className="text-sm text-fg-muted">{t("wiki.graph.cleared")}</p>}
          <WikiGraphView
            ref={camera}
            scope={scope}
            state={graph.state}
            selected={selection}
            onSelect={choose}
            onExpand={graph.expand}
            onCollapse={graph.collapse}
            onRemove={(ids) => { graph.remove(ids); setSelection((old) => old.filter((id) => !ids.includes(id))); }}
            onKeepOnly={graph.keep}
            onConnect={graph.connect}
            onOpen={(id) => { choose([id]); setInspector(true); if (narrow) setSheet(true); }}
            toolbar={toolbar}
          />
        </>
      )}
    </div>
  );

  const listPanel = listQuery.isPending ? (
    <ListSkeleton rows={{ base: 6, md: 8 }} />
  ) : listQuery.isError && !listQuery.data ? (
    <ApiErrorState error={listQuery.error} fallback={t("wiki.graph.error")} onRetry={() => void listQuery.refetch()} />
  ) : !listRows.length ? (
    <EmptyState title={t(searchMode ? "search.noResults" : "wiki.graph.empty")} />
  ) : (
    <div className={`grid gap-2 ${staleContentClass(listQuery.isPlaceholderData)}`} aria-busy={listQuery.isFetching || undefined}>
      {listTotal !== undefined && <ListCount total={listTotal} />}
      <ListScrollRegion label={t(searchMode ? "wiki.search.resultsList" : "wiki.search.documentsList")} testId="wiki-result-list">
        <ul className="divide-y divide-border">
          {listRows.map((row, index) => {
            const hit = searchMode ? hits.data?.items[index] : undefined;
            const active = selection.includes(row.id);
            return (
              <li key={row.id}>
                <button type="button" aria-pressed={active} data-surface-tint={active ? "accent" : undefined}
                  className="grid w-full gap-0.5 px-3 py-2 text-left hover:bg-surface-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring border-l-2 border-l-transparent aria-pressed:border-l-accent-fg aria-pressed:bg-accent-muted" onClick={() => pick(row)}>
                  <span className="grid min-w-0 gap-0.5">
                    <span className="flex min-w-0 gap-2">
                      <span className="text-xs tabular-nums text-fg-muted">{offset + index + 1}</span>
                      <strong className="min-w-0 text-sm [overflow-wrap:anywhere]">{row.label}</strong>
                    </span>
                    {searchMode && (
                      <span className="flex min-w-0 items-center gap-1 text-xs font-normal text-fg-muted">
                        <BookOpen size={14} className="shrink-0" aria-hidden />
                        <span className="min-w-0 truncate">{row.document_name}</span>
                      </span>
                    )}
                    {hit?.knowledge_base_ids.map((id) => (
                      <span key={id} className="min-w-0 truncate text-xs font-normal text-fg-muted">{kbs.find((k) => k.id === id)?.name ?? id}</span>
                    ))}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </ListScrollRegion>
      {listTotal !== undefined && (
        <OffsetPagination offset={offset} limit={pageSize} total={listTotal} count={listRows.length}
          onPageChange={(_, next) => changePage(next)} onPageSizeChange={(size) => { setPageSize(size); changePage(0); }}
          pageSizeOptions={[...WIKI_PAGE_SIZE_OPTIONS]} labels={paginationLabels()} ariaLabel={t(searchMode ? "wiki.search.resultsList" : "wiki.search.documentsList")} />
      )}
    </div>
  );

  return (
    <Card>
      <div ref={box} className={`flex min-w-0 flex-col bg-surface ${full ? "h-full overflow-auto" : ""}`}>
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 pt-2">
          <h2 className="font-semibold">{title}</h2>
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            <Tabs idPrefix="wiki-view" ariaLabel={title} value={view} onChange={setView}
              items={[{ id: "graph", label: t("wiki.search.graph"), icon: Network }, { id: "list", label: t("wiki.search.list"), icon: List }]} />
            {!narrow && !inspector && (
              <Button variant="ghost" size="sm" iconOnly icon={PanelRight} aria-label={t("wiki.search.openDetail")} onClick={() => setInspector(true)} />
            )}
            {(wideScreen || full) && !fullError && (
              <Button variant="ghost" size="sm" iconOnly icon={full ? Minimize : Maximize} aria-label={t(full ? "wiki.search.shrink" : "wiki.search.full")} onClick={() => void toggleFull()} />
            )}
          </div>
        </header>
        {workingLabel && !(narrow && sheet) && (
          <div className="px-3 pt-3">
            <ProcessingIndicator active label={workingLabel} testId="wiki-wait-results" reserveSlowLine={false}
              activityIcon={searchButtonBusy && mainQuery.isFetching ? "none" : "spinner"} />
          </div>
        )}
        <div className={`grid min-w-0 gap-3 p-3 ${showInspector ? "lg:grid-cols-[minmax(0,1fr)_minmax(17rem,22rem)]" : ""}`}>
          <div className="min-w-0">
            <TabPanel id="graph" value={view} idPrefix="wiki-view">{graphPanel}</TabPanel>
            <TabPanel id="list" value={view} idPrefix="wiki-view">{listPanel}</TabPanel>
          </div>
          {showInspector && (
            <aside aria-label={t("wiki.search.detail")} className="flex min-h-0 min-w-0 flex-col rounded-md border border-border max-lg:max-h-[40rem] lg:max-h-[46rem]">
              <div className="flex shrink-0 items-center justify-between border-b border-border px-3 py-1">
                <strong className="text-sm font-semibold">{t("wiki.search.detail")}</strong>
                <Button iconOnly icon={X} size="sm" variant="ghost" aria-label={t("wiki.graph.clearSelection")} onClick={() => setInspector(false)} />
              </div>
              {detail}
            </aside>
          )}
        </div>
        {narrow && (node || selectedEdge || selectedNodes.length > 1) && !sheet && (
          <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2">
            <span className="min-w-0 truncate text-sm">{node ? node.label : selectedEdge ? t("wiki.graph.selectedEdge") : t("wiki.graph.multiSelected", { count: selectedNodes.length })}</span>
            <Button size="sm" variant="secondary" icon={Search} onClick={() => setSheet(true)}>{t("wiki.graph.openDetail")}</Button>
          </div>
        )}
        {narrow && (
          <SideSheet side="bottom" open={sheet} onClose={() => setSheet(false)} initialFocus="dialog" title={t("wiki.search.detail")}
            closeLabel={t("wiki.graph.clearSelection")} bodyClassName="p-0">
            {sheet && detail}
          </SideSheet>
        )}
      </div>
    </Card>
  );
}
