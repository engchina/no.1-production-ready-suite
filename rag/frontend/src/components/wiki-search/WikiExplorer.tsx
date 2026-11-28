import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Eraser, Info, List, Maximize, Minimize, Network, PanelRight, Search, X } from "lucide-react";
import type { GraphExplorerHandle } from "@production-ready/ui/graph-explorer";
import {
  Button,
  Card,
  DataTable,
  INFORMATION_TABLE_ROW_CLASS,
  INFORMATION_TABLE_VISIBLE_ROWS,
  ListCount,
  ListScrollRegion,
  ListSkeleton,
  OffsetPagination,
  Pagination,
  ProcessingIndicator,
  RowTitleButton,
  SearchField,
  SideSheet,
  Skeleton,
  TabPanel,
  Tabs,
  staleContentClass,
  useMediaQuery,
  usePagination,
} from "@production-ready/ui";
import { DataLoadFailure } from "@/components/DataLoadStates";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { t } from "@/lib/i18n";
import { paginationLabels } from "@/lib/pagination-labels";
import { WIKI_GRAPH_SETTINGS, WIKI_PAGE_SIZE_OPTIONS } from "./wiki-graph-settings";
import { moreId, remainingNeighbors, wikiGraphRows, type WikiGraphNode, type WikiGraphRow } from "./wiki-graph-model";
import { documentLinks, documentNode, unitNode } from "./wiki-graph-loaders";
import { useWikiGraph } from "./use-wiki-graph";
import { WikiGraphView } from "./WikiGraphView";
import { RelationInspector, SelectionSummary, WikiInspector } from "./WikiInspector";
import { sourcePick, wikiApi, type WikiKb, type WikiMethod, type WikiPick, type WikiScope } from "./wiki-api";

interface Props {
  scope: WikiScope;
  question: string;
  method: WikiMethod;
  kbs: WikiKb[];
  picks?: WikiPick[];
  toggle?: (p: WikiPick) => void;
  /** 選んだ原文を id で外す（Wiki 検索の図で、選んだ原文のノードを外したとき）。 */
  unpick?: (ids: readonly string[]) => void;
  /** 一度に選べる原文の上限（backend の `selection_limit`）。 */
  selectionLimit: number;
  initialSelection?: string;
  onSelectUnit: (id: string) => void;
  initialOffset: number;
  onOffset: (offset: number) => void;
  /** 検索のボタンが同じ読み込みで loading のとき（経過の行の回転を止める。buttons.md §8）。 */
  searchButtonBusy?: boolean;
}

// 詳細を図の右に並べても図の操作領域を保てる幅。サイドバーを除いた実寸で判定する。
const SPLIT_MIN_WIDTH = 768;

/** 選んだ原文を図の節点にする（名前と文書は選んだときに控えたもの。無い値で図の節点の内容を上書きしない）。 */
function pickNode(pick: WikiPick): WikiGraphNode {
  return { id: pick.id, kind: "unit", label: pick.label ?? pick.location, document_id: pick.documentId ?? "", document_name: pick.name };
}

/**
 * Wiki の関係図と一覧（#5922）。「一覧」はいま図にある節点の表で、関係図と同じ集合の別の見方（Neo4j Browser の
 * Graph / Table。画布の内容を読み上げで追える。#5953）。
 *
 * - ナレッジベース詳細: 参照の多い文書の見本から始め、図内検索の当たりを足す。
 * - Wiki 検索（#5981）: 「検索結果」の一覧から始める。行のチェックボックスで原文を選び（上限は backend の
 *   `selection_limit`）、「関係図」と「一覧」は選んだ原文とその次の段だけを出す。関係の照会は、図（関係図・一覧）を
 *   開いている間だけ行う（検索結果のタブにいる間は呼ばない。#5966）。
 */
export function WikiExplorer({ scope, question, method, kbs, picks, toggle, unpick, selectionLimit, initialSelection, onSelectUnit, initialOffset, onOffset, searchButtonBusy = false }: Props) {
  const base = ["wiki-explorer", scope] as const;
  const searchMode = Boolean(question.trim());
  const knowledgeBase = scope.kind === "kb";
  const box = useRef<HTMLDivElement>(null);
  const camera = useRef<GraphExplorerHandle>(null);
  const [narrow, setNarrow] = useState(false);
  const wideScreen = useMediaQuery("(min-width: 768px)", false);
  const [view, setView] = useState(searchMode ? "results" : "graph");
  const [offset, setOffset] = useState(initialOffset);
  const [pageSize, setPageSize] = useState<number>(WIKI_GRAPH_SETTINGS.resultPage);
  const [listSize, setListSize] = useState<number>(WIKI_GRAPH_SETTINGS.resultPage);
  const [selection, setSelection] = useState<string[]>(initialSelection ? [initialSelection] : []);
  const [inspector, setInspector] = useState(true);
  const [sheet, setSheet] = useState(false);
  const [full, setFull] = useState(false);
  const [fullError, setFullError] = useState(false);
  const [find, setFind] = useState("");
  const [cursor, setCursor] = useState(0);
  // 図に無いが詳細に出している原文（Wiki 検索の検索結果の行・詳細の関係の相手。図には足さない）。
  const [inspected, setInspected] = useState<WikiGraphNode | null>(null);
  // チェックボックスで選ぶ途中（原文を照合している）の行と、その失敗。
  const [picking, setPicking] = useState<readonly string[]>([]);
  const [pickFailure, setPickFailure] = useState<{ error: unknown; retry: () => void } | null>(null);
  const limitId = useId();
  const client = useQueryClient();
  const graph = useWikiGraph(scope);
  const picked = useMemo(() => new Set((picks ?? []).map((item) => item.id)), [picks]);
  // 照合の途中の行も数える（上限の直前で続けて押しても、上限を超えて足さない）。
  const selectionFull = (picks?.length ?? 0) + picking.length >= selectionLimit;

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
  const found = useQuery({
    queryKey: [...base, "find", find],
    queryFn: ({ signal }) => wikiApi.hits(scope, find, "keyword", 0, WIKI_GRAPH_SETTINGS.resultPage, signal),
    enabled: knowledgeBase && !searchMode && Boolean(find.trim()),
    retry: false,
  });

  // Wiki 検索の図は、選んだ原文とその次の段（#5981）。図（関係図・一覧）を開いている間だけ足し、検索結果の
  // タブにいる間は関係を照会しない（選ぶたびに読むと、原文の照合と重なって DB の読み取りの上限に当たる。#5966）。
  // 選択から外した原文は、どのタブにいてもすぐ図から外す。
  const placed = useRef("");
  const { replace, add, place, run, reveal, syncPicked } = graph;
  const figureOpen = view !== "results";
  useEffect(() => {
    if (searchMode) syncPicked((picks ?? []).map(pickNode), figureOpen);
  }, [figureOpen, picks, searchMode, syncPicked]);
  // ナレッジベース詳細は、参照の多い文書の見本とその間の参照で開く。
  useEffect(() => {
    if (searchMode || !sample.data || placed.current === "sample") return;
    placed.current = "sample";
    replace(sample.data.documents.map(documentNode), documentLinks(sample.data));
  }, [replace, sample.data, searchMode]);
  // 図内検索の当たりを今の図に足し、強調する（ナレッジベース詳細。#5881 で消えた検索）。当たりと図にある原文の
  // 間の関係も一緒に引く（#5953）。
  const added = useRef("");
  useEffect(() => {
    if (!found.data || added.current === find) return;
    added.current = find;
    const nodes = found.data.items.map((hit) => unitNode(hit));
    setCursor(0);
    // 足した当たりが画面の外に出ないように、全体を収めた表示にしてから足す（画布は、全体を収めたままなら足した後も収め直す）。
    camera.current?.fit();
    void run(async () => {
      await place(nodes, WIKI_GRAPH_SETTINGS.knowledgeBaseSearch, nodes.map((node) => node.id));
      if (!nodes[0]) return;
      await reveal(nodes[0]);
      camera.current?.focusNode(nodes[0].id);
      setSelection([nodes[0].id]);
    });
  }, [find, found.data, place, reveal, run]);

  function moveMatch(step: number) {
    const items = found.data?.items ?? [];
    if (!items.length) return;
    const next = (cursor + step + items.length) % items.length;
    setCursor(next);
    const node = unitNode(items[next]);
    void run(async () => { await reveal(node); camera.current?.focusNode(node.id); setSelection([node.id]); });
  }

  const nodes = graph.state.nodes;
  const hitRows: WikiGraphNode[] = useMemo(() => (hits.data?.items ?? []).map((hit) => unitNode(hit)), [hits.data]);
  // 選択は図の節点。Wiki 検索は、図に無い原文（検索結果の行・詳細から開いた関係の相手）も詳細に出せる。
  const known = (id: string) => nodes.get(id) ?? (inspected?.id === id ? inspected : undefined) ?? (searchMode ? hitRows.find((row) => row.id === id) : undefined);
  const selectedNodes = selection.map((id) => known(id) ?? (id.startsWith("more:") ? moreNode(id) : undefined)).filter((node): node is WikiGraphNode => Boolean(node));
  const selectedEdge = selection.length === 1 ? graph.state.edges.get(selection[0]) : undefined;
  const node = selectedNodes.length === 1 ? selectedNodes[0] : undefined;
  function moreNode(id: string): WikiGraphNode | undefined {
    const owner = nodes.get(id.slice("more:".length));
    return owner ? { id, kind: "more", label: "", owner: owner.id, remaining: remainingNeighbors(graph.state, owner.id), document_id: owner.document_id, document_name: owner.document_name } : undefined;
  }

  function choose(ids: string[], shown?: WikiGraphNode) {
    setSelection(ids);
    const unit = ids.length === 1 ? shown ?? known(ids[0]) : undefined;
    onSelectUnit(unit?.kind === "unit" ? unit.id : "");
  }
  /**
   * 一覧・詳細から選ぶ。ナレッジベース詳細は、図に無ければ足してから選ぶ。Wiki 検索は図に足さず（図は選んだ原文と
   * その次の段だけ）、詳細にだけ出す。
   */
  function pick(item: WikiGraphNode) {
    if (!nodes.has(item.id)) {
      if (searchMode) setInspected(item);
      else add([item], []);
    }
    choose([item.id], item);
    setInspector(true);
    if (narrow) setSheet(true);
    requestAnimationFrame(() => camera.current?.focusNode(item.id));
  }
  /**
   * 検索結果の行のチェックボックス（#5981）。選ぶときは原文を照合してから選択に足す（詳細と同じ照会を使い回す）。
   * 上限に達している間は足さない（自動で入れ替えない）。
   */
  const latestPicked = useRef(picked);
  useEffect(() => { latestPicked.current = picked; }, [picked]);
  function togglePick(row: WikiGraphNode) {
    if (!toggle) return;
    const chosen = picks?.find((item) => item.id === row.id);
    if (chosen) return toggle(chosen);
    if (selectionFull || picking.includes(row.id)) return;
    setPickFailure(null);
    setPicking((old) => [...old, row.id]);
    client.fetchQuery({ queryKey: ["wiki-explorer", scope, "original", row.id], queryFn: ({ signal }) => wikiApi.original(scope, row.id, signal), staleTime: 60_000 })
      .then(
        (original) => { if (!latestPicked.current.has(row.id)) toggle(sourcePick(original)); },
        (error: unknown) => setPickFailure({ error, retry: () => togglePick(row) }),
      )
      .finally(() => setPicking((old) => old.filter((id) => id !== row.id)));
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

  // 「一覧」はいま図にある節点（名前の順）。図を置き換えたときだけ 1 ページ目へ戻す。
  const rows = useMemo(() => wikiGraphRows(graph.state), [graph.state]);
  const listPaging = usePagination(rows, listSize, { resetKey: graph.state.generation });
  const mainQuery = searchMode ? hits : sample;
  // Wiki 検索の図（関係図・一覧）は、選んだ原文が無ければ案内を出す。
  const figureEmpty = searchMode && !picks?.length;
  const figureGuide = (
    <EmptyState title={t("wiki.search.figureEmpty")}
      action={<Button variant="secondary" icon={Search} onClick={() => setView("results")}>{t("wiki.search.showHits")}</Button>} />
  );
  const workingLabel = mainQuery.isFetching ? t(searchMode ? "wiki.search.loadingHits" : "wiki.graph.loadingSample")
    : found.isFetching ? t("wiki.graph.searching")
    : graph.pending ? t("wiki.graph.expanding") : undefined;
  const title = searchMode ? t("wiki.search.results") : t("wiki.graph.browseTitle");
  const showInspector = !narrow && inspector;

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
      // Wiki 検索の図は選んだ原文とその次の段だけなので、図に無い原文からは展開しない（選ぶと図に入る）。
      expand={searchMode && node.kind !== "more" && !nodes.has(node.id) ? undefined
        : () => { if (!nodes.has(node.id) && node.kind !== "more") add([node], []); graph.expand(node.kind === "more" ? moreId(node.owner!) : node.id); setView("graph"); if (narrow) setSheet(false); }}
      collapse={() => graph.collapse(node.id)}
      picks={picks}
      toggle={toggle}
      selectionLimit={selectionLimit}
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

  const graphPanel = figureEmpty ? figureGuide : !searchMode && sample.isPending ? (
    // 読み込み中は画布の形の Skeleton（凡例の行と画布）。
    <div className="grid gap-2" aria-hidden data-testid="wiki-graph-skeleton">
      <Skeleton className="h-9 w-2/3 max-w-[32rem]" />
      <Skeleton className="h-[40rem] w-full max-sm:h-[65dvh]" />
    </div>
  ) : !searchMode && sample.isError && !sample.data ? (
    <ApiErrorState error={sample.error} fallback={t("wiki.graph.error")} onRetry={() => void sample.refetch()} />
  ) : (
    <div className="grid gap-2">
      {!searchMode && sample.data && sample.data.total > 0 && (
        <p className="text-xs text-fg-muted">{t("wiki.graph.sampleHint", { count: sample.data.documents.length, total: sample.data.total })}</p>
      )}
      {searchMode && <p className="text-xs text-fg-muted">{t("wiki.search.figureHint", { count: WIKI_GRAPH_SETTINGS.selectedNeighbors })}</p>}
      {graph.failure && <DataLoadFailure error={graph.failure.error} fallback={t("wiki.graph.expandError")} onRetry={graph.failure.retry} />}
      {found.isError && <DataLoadFailure error={found.error} fallback={t("wiki.graph.searchError")} onRetry={() => void found.refetch()} />}
      {!nodes.size && !searchMode && sample.data?.total === 0 ? (
        <EmptyState title={t("wiki.graph.empty")} />
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
            onRemove={(ids) => {
              // Wiki 検索の図で選んだ原文を外すと、選択からも外す（図は選択に合わせて、その次の段も外す）。
              const chosen = searchMode ? ids.filter((id) => picked.has(id)) : [];
              if (chosen.length) unpick?.(chosen);
              graph.remove(ids.filter((id) => !chosen.includes(id)));
              setSelection((old) => old.filter((id) => !ids.includes(id)));
            }}
            // 「選択したノードだけ残す」は、選んだ原文をまとめて選択から外すことになるので、Wiki 検索の図では出さない。
            onKeepOnly={searchMode ? undefined : graph.keep}
            onConnect={graph.connect}
            onOpen={(id) => { choose([id]); setInspector(true); if (narrow) setSheet(true); }}
            toolbar={toolbar}
            markPicked={searchMode}
          />
        </>
      )}
    </div>
  );

  // 「一覧」: いま図にある節点の表。行を選ぶと図と同じ節点が選ばれ、詳細が出る。
  // Wiki 検索は、選んだ原文を図と同じ「選んだ原文」の種類で出す（図の印を一覧でも読める）。
  const kindLabel = (node: WikiGraphNode) => t(searchMode && node.kind === "unit" && graph.state.matched.has(node.id) ? "wiki.graph.kind.picked" : `wiki.graph.kind.${node.kind}`);
  const listPanel = figureEmpty ? figureGuide : searchMode && !rows.length ? (
    // 選んだ原文を図に置く前の一瞬。
    <ListSkeleton rows={{ base: 5, md: 8 }} />
  ) : !searchMode && sample.isPending ? (
    <ListSkeleton rows={{ base: 5, md: 8 }} />
  ) : !searchMode && sample.isError && !sample.data ? (
    <ApiErrorState error={sample.error} fallback={t("wiki.graph.error")} onRetry={() => void sample.refetch()} />
  ) : !rows.length ? (
    <EmptyState title={t(sample.data?.total === 0 ? "wiki.graph.empty" : "wiki.graph.cleared")} />
  ) : (
    <div className="grid gap-2">
      {searchMode && graph.failure && <DataLoadFailure error={graph.failure.error} fallback={t("wiki.graph.expandError")} onRetry={graph.failure.retry} />}
      <ListCount total={rows.length} />
      <DataTable<WikiGraphRow>
        ariaLabel={t("wiki.graph.nodesList")}
        rows={listPaging.pageItems}
        getRowKey={(row) => row.node.id}
        onRowClick={(row) => pick(row.node)}
        selectedRowKey={selection.length === 1 ? selection[0] : null}
        isRowSelected={(row) => selection.includes(row.node.id)}
        rowProps={() => ({ className: INFORMATION_TABLE_ROW_CLASS })}
        stickyHeader
        visibleRows={INFORMATION_TABLE_VISIBLE_ROWS}
        scrollAriaLabel={t("wiki.graph.nodesList")}
        scrollTestId="wiki-node-list"
        tableClassName="w-full"
        columns={[
          {
            key: "name",
            header: t("wiki.graph.name"),
            rowHeader: true,
            render: (row) => (
              <RowTitleButton
                title={row.node.label}
                // 狭い画面では列を畳み、種類と所属の文書を名前の下に出す。
                subtitle={<span className="md:hidden">{[kindLabel(row.node), row.node.kind === "document" ? "" : row.node.document_name].filter(Boolean).join(" · ")}</span>}
                current={selection.includes(row.node.id)}
                onClick={() => pick(row.node)}
              />
            ),
          },
          { key: "kind", header: t("wiki.graph.kind"), headerClassName: "hidden md:table-cell", className: "hidden whitespace-nowrap md:table-cell", render: (row) => kindLabel(row.node) },
          {
            key: "document",
            header: t("wiki.graph.memberDocument"),
            // 原文・章節の行がある一覧では、文書の名前が語の途中で折り返さない幅を取る（文書だけの一覧は
            // 全行「—」なので取らない。#5976）。
            headerClassName: `hidden md:table-cell${listPaging.pageItems.some((row) => row.node.kind !== "document") ? " md:w-[40%]" : ""}`,
            className: "hidden md:table-cell [overflow-wrap:anywhere]",
            render: (row) => (row.node.kind === "document" ? "—" : row.node.document_name),
          },
          { key: "relations", header: t("wiki.graph.relationCount"), align: "right", className: "tnum whitespace-nowrap", render: (row) => row.relations },
        ]}
      />
      <Pagination page={listPaging.page} totalPages={listPaging.totalPages} onPageChange={listPaging.setPage} range={listPaging.range}
        pageSize={listSize} onPageSizeChange={setListSize} pageSizeOptions={[...WIKI_PAGE_SIZE_OPTIONS]} labels={paginationLabels()} ariaLabel={t("wiki.graph.nodesList")} />
    </div>
  );

  // 「検索結果」（Wiki 検索だけ）: サーバー側のページングの結果の一覧。行のチェックボックスで原文を選び、行を押すと詳細を開く。
  const resultsPanel = hits.isPending ? (
    <ListSkeleton rows={{ base: 6, md: 8 }} />
  ) : hits.isError && !hits.data ? (
    <ApiErrorState error={hits.error} fallback={t("wiki.graph.error")} onRetry={() => void hits.refetch()} />
  ) : !hitRows.length ? (
    <EmptyState title={t("search.noResults")} />
  ) : (
    <div className={`grid gap-2 ${staleContentClass(hits.isPlaceholderData)}`} aria-busy={hits.isFetching || undefined}>
      <ListCount total={hits.data.total} />
      {/* 押せない理由は常に出す（吹き出しに隠さない。messaging.md §3.8）。上限に達しても自動で入れ替えない。 */}
      {toggle && selectionFull && (
        <p id={limitId} role="status" className="flex items-start gap-1 text-xs text-fg-muted" data-testid="wiki-selection-limit">
          <Info size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>{t("wiki.search.selectionLimit", { count: selectionLimit })}{t("wiki.search.selectionLimitHint")}</span>
        </p>
      )}
      {pickFailure && <DataLoadFailure error={pickFailure.error} fallback={t("wiki.graph.sourceError")} onRetry={pickFailure.retry} />}
      <ListScrollRegion label={t("wiki.search.resultsList")} testId="wiki-result-list">
        <ul className="divide-y divide-border">
          {hitRows.map((row, index) => {
            const hit = hits.data.items[index];
            const active = selection.includes(row.id);
            const checked = picked.has(row.id) || picking.includes(row.id);
            const blocked = !checked && selectionFull;
            return (
              <li key={row.id} data-surface-tint={active ? "accent" : undefined}
                className={`flex min-w-0 items-stretch border-l-2 ${active ? "border-l-accent-fg bg-accent-muted" : "border-l-transparent hover:bg-surface-hover"}`}>
                {toggle && (
                  // チェックボックスの押せる範囲は、行の高さ × タッチの最小の幅（44px）。
                  <label className="flex min-w-[var(--control-height-touch)] shrink-0 cursor-pointer items-start justify-center pt-3 has-[:disabled]:cursor-not-allowed">
                    <input type="checkbox" checked={checked} disabled={blocked} aria-busy={picking.includes(row.id) || undefined}
                      aria-label={t("wiki.search.pickRow", { name: row.label })} aria-describedby={blocked ? limitId : undefined}
                      className="cursor-pointer accent-accent-emphasis disabled:cursor-not-allowed" onChange={() => togglePick(row)} />
                  </label>
                )}
                <button type="button" aria-pressed={active}
                  className={`grid min-w-0 flex-1 gap-0.5 py-2 pr-3 text-left focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-focus-ring ${toggle ? "" : "pl-3"}`} onClick={() => pick(row)}>
                  <span className="grid min-w-0 gap-0.5">
                    <span className="flex min-w-0 gap-2">
                      <span className="text-xs tabular-nums text-fg-muted">{offset + index + 1}</span>
                      <strong className="min-w-0 text-sm [overflow-wrap:anywhere]">{row.label}</strong>
                    </span>
                    <span className="flex min-w-0 items-center gap-1 text-xs font-normal text-fg-muted">
                      <BookOpen size={14} className="shrink-0" aria-hidden />
                      <span className="min-w-0 truncate">{row.document_name}</span>
                    </span>
                    {hit.knowledge_base_ids.map((id) => (
                      <span key={id} className="min-w-0 truncate text-xs font-normal text-fg-muted">{kbs.find((k) => k.id === id)?.name ?? id}</span>
                    ))}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </ListScrollRegion>
      <OffsetPagination offset={offset} limit={pageSize} total={hits.data.total} count={hitRows.length}
        onPageChange={(_, next) => changePage(next)} onPageSizeChange={(size) => { setPageSize(size); changePage(0); }}
        pageSizeOptions={[...WIKI_PAGE_SIZE_OPTIONS]} labels={paginationLabels()} ariaLabel={t("wiki.search.resultsList")} />
    </div>
  );

  return (
    <Card>
      <div ref={box} className={`flex min-w-0 flex-col bg-surface ${full ? "h-full overflow-auto" : ""}`}>
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 pt-2">
          <h2 className="font-semibold">{title}</h2>
          <div className="flex min-w-0 flex-wrap items-center gap-1">
            <Tabs idPrefix="wiki-view" ariaLabel={title} value={view} onChange={setView}
              // Wiki 検索は一覧から始めるので、「検索結果」を先頭に置く（最初に開くタブ = 先頭。選ぶ → 図を見るの順）。
              items={[
                ...(searchMode ? [{ id: "results", label: t("wiki.search.hits"), icon: Search }] : []),
                { id: "graph", label: t("wiki.search.graph"), icon: Network },
                { id: "list", label: t("wiki.search.list"), icon: List },
              ]} />
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
            {searchMode && <TabPanel id="results" value={view} idPrefix="wiki-view">{resultsPanel}</TabPanel>}
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
