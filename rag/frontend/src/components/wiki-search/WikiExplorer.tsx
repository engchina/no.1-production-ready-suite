import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { reachableGraphBranches } from "@production-ready/ui/graph";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  List,
  Maximize,
  Minimize,
  Network,
  PanelRight,
  X,
} from "lucide-react";
import { GraphLegend } from "@production-ready/ui/graph";
import { WIKI_RELATION_ICONS, wikiRelationText } from "@/components/knowledge-bases/wiki-graph-labels";
import {
  Button,
  Card,
  Disclosure,
  SelectField,
  OffsetPagination,
  ListSkeleton,
  ProcessingIndicator,
  REFRESH_REVEAL_DELAY_MS,
  staleContentClass,
  SideSheet,
  Skeleton,
  TimedLoadingState,
  useMediaQuery,
} from "@production-ready/ui";
import { ApiErrorState, EmptyState } from "@/components/StateViews";
import { t } from "@/lib/i18n";
import type { WikiGraphRelation } from "@/lib/api";
import { WikiInspector, RelationInspector, NEIGHBOR_PAGE } from "./WikiInspector";
import {
  edgeId,
  emptyScene,
  joinScenes,
  neighbors,
  WikiScene,
  type Scene,
  type WikiNode,
  type Connection,
} from "./WikiScene";
import { wikiApi, type WikiKb, type WikiPick, type WikiMethod, type WikiScope } from "./wiki-api";

interface WikiBranch extends Scene { owner: string }

interface Props {
  scope: WikiScope;
  question: string;
  method: WikiMethod;
  kbs: WikiKb[];
  picks?: WikiPick[];
  toggle?: (p: WikiPick) => void;
  initialUnit?: string;
  initialSelection?: string;
  onSelectUnit: (id: string) => void;
  initialOffset: number;
  onOffset: (offset: number) => void;
  onRoot?: (id: string) => void;
}
const RESULT_PAGE = 5;
// 一覧と詳細を左右に並べても図の操作領域を保てる幅。サイドバーを除いた実寸で判定する。
const SPLIT_MIN_WIDTH = 768;
function Busy({ name, label, large = false }: { name: string; label: string; large?: boolean }) {
  return (
    <TimedLoadingState label={label} operationKey={name} testId={"wiki-wait-" + name}>
      {large ? <div className="min-h-80"><ListSkeleton rows={{ base: 5, md: 5 }} /></div> : <Skeleton className="h-20 w-full" />}
    </TimedLoadingState>
  );
}
function Refresh({ name, label }: { name: string; label: string }) {
  return <ProcessingIndicator active label={label} operationKey={name} revealDelayMs={REFRESH_REVEAL_DELAY_MS} reserveSlowLine={false} testId={"wiki-refresh-" + name} />;
}
function Failed({ error, retry }: { error: unknown; retry: () => void }) {
  return <ApiErrorState error={error} fallback={t("wiki.graph.error")} onRetry={retry} />;
}
export function WikiExplorer({ scope, question, method, kbs, picks, toggle, initialUnit, initialSelection, onSelectUnit, onRoot, initialOffset, onOffset }: Props) {
  const base = ["wiki-explorer", scope] as const;
  const [small, setSmall] = useState(true);
  const wideScreen = useMediaQuery("(min-width: 768px)", false);
  const [chosenView, setView] = useState<"graph" | "list" | null>(null);
  const view = chosenView ?? (small ? "list" : "graph");
  const [offset, setOffset] = useState(initialOffset);
  const [kind, setKind] = useState("");
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const [resumeUnit, setResumeUnit] = useState(initialSelection);
  const [anchor, setAnchor] = useState<WikiNode | null>(null);
  const [selection, setSelection] = useState<WikiNode | null>(null);
  const [edge, setEdge] = useState<Connection | null>(null);
  const [group, setGroup] = useState<WikiGraphRelation | "">("");
  const [tab, setTab] = useState("source");
  const [relOffset, setRelOffset] = useState(0);
  const [branches, setBranches] = useState<Record<string, WikiBranch>>({});
  const [removed, setRemoved] = useState<Record<string, WikiBranch>>({});
  const [sheet, setSheet] = useState(false),
    [inspector, setInspector] = useState(true);
  const [full, setFull] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const panel = box.current;
    if (!panel) return;
    const measure = () => setSmall(panel.clientWidth < SPLIT_MIN_WIDTH);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(panel);
    return () => observer.disconnect();
  }, []);
  const search = Boolean(question.trim());
  const hits = useQuery({
    queryKey: [...base, "search", question, method, offset],
    queryFn: ({ signal }) => wikiApi.hits(scope, question, method, offset, RESULT_PAGE, signal),
    placeholderData: keepPreviousData,
    enabled: search,
    retry: false,
  });
  const docs = useQuery({
    queryKey: [...base, "docs", offset],
    queryFn: ({ signal }) => wikiApi.documents(scope, offset, RESULT_PAGE, signal),
    placeholderData: keepPreviousData,
    enabled: !search,
    retry: false,
  });
  const count = useQuery({
    queryKey: [...base, "document-count"],
    queryFn: ({ signal }) => wikiApi.docCount(scope, signal),
    enabled: !search && Boolean(docs.data?.has_more),
    retry: false,
  });
  const rows: WikiNode[] = search
    ? (hits.data?.items ?? []).map((n) => ({ ...n, kind: "unit" }))
    : (docs.data?.documents ?? []).map((n) => ({
        kind: "document",
        id: n.id,
        label: n.name,
        document_id: n.document_id,
        document_name: n.name,
      }));
  const total = search ? hits.data?.total : !docs.data ? undefined : docs.data.has_more ? count.data?.total : offset + rows.length;
  const mainResult = search ? hits : docs;
  const resume = useQuery({
    queryKey: [...base, "original", resumeUnit],
    queryFn: ({ signal }) => wikiApi.original(scope, resumeUnit!, signal),
    enabled: !!resumeUnit && !rows.some((n) => n.id === resumeUnit),
    retry: false,
  });
  const restored = rows.find((n) => n.id === resumeUnit) ?? (resumeUnit && resume.data ? { ...resume.data, kind: "unit" as const } : undefined);
  const root = anchor ?? restored ?? rows.find((n) => n.id === initialUnit) ?? rows[0];
  const node = selection ?? root;
  const key = (n: WikiNode | undefined) => n?.id ?? "";
  const firstRing = useQuery({
    queryKey: [...base, "relations", key(root), "", 0],
    queryFn: ({ signal }) => wikiApi.relations(scope, root!.id, "", 0, NEIGHBOR_PAGE, signal),
    enabled: root?.kind === "unit",
    retry: false,
  });
  const rootOriginal = useQuery({
    queryKey: [...base, "original", key(root)],
    queryFn: ({ signal }) => wikiApi.original(scope, root!.id, signal),
    enabled: root?.kind === "unit",
    retry: false,
  });
  const detail = useQuery({
    queryKey: [...base, "original", key(node)],
    queryFn: ({ signal }) => wikiApi.original(scope, node!.id, signal),
    enabled: node?.kind === "unit",
    retry: false,
  });
  const relationQuery = useQuery({
    queryKey: [...base, "relations", key(node), group, relOffset],
    queryFn: ({ signal }) => wikiApi.relations(scope, node!.id, group, relOffset, NEIGHBOR_PAGE, signal),
    enabled: node?.kind === "unit",
    retry: false,
  });
  const childQuery = useQuery({
    queryKey: [...base, "children", key(node), relOffset],
    queryFn: ({ signal }) =>
      wikiApi.tree(
        scope,
        node!.document_id,
        node!.kind === "section" ? node!.id : null,
        relOffset,
        NEIGHBOR_PAGE,
        signal,
      ),
    enabled: !!node && node.kind !== "unit" && (inspector || sheet),
    retry: false,
  });
  const ring =
    root?.kind === "unit"
      ? neighbors(root, firstRing.data?.preview.slice(0, 4) ?? [])
      : { nodes: rows, edges: [] };
  const detailRoot = rootOriginal.data;
  let structure: Scene = emptyScene;
  if (root && detailRoot?.parent_id && detailRoot.path.length) {
    const parent: WikiNode = {
      id: detailRoot.parent_id,
      label: detailRoot.path.at(-1)!,
      kind: "section",
      path: detailRoot.path,
      document_id: detailRoot.document_id,
      document_name: detailRoot.document_name,
    };
    structure = { nodes: [parent], edges: [{ source: parent.id, target: root.id, kind: "contains" }] };
  }
  const pinned = search ? [...rows] : [];
  if (selection) pinned.push(selection);
  const baseScene = joinScenes(ring, structure, { nodes: pinned, edges: [] });
  const activeBranches = reachableGraphBranches(baseScene.nodes.map((n) => n.id), Object.values(branches), (b) => b.owner, (b) => b.nodes.map((n) => n.id));
  const all = joinScenes(baseScene, ...activeBranches);
  const visibleNodes = all.nodes.filter((n) => !kind || n.kind === kind);
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const scene = {
    nodes: visibleNodes,
    edges: all.edges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target) && !hidden.has(e.kind),
    ),
  };
  function pick(n: WikiNode, asRoot = false) {
    if (!asRoot && restored) setAnchor(restored);
    setResumeUnit(undefined);
    onSelectUnit(n.kind === "unit" ? n.id : "");
    if (asRoot) {
      setAnchor(n);
      setBranches({});
      setRemoved({});
      onRoot?.(n.id);
    }
    setSelection(n);
    setEdge(null);
    setGroup("");
    setRelOffset(0);
    setTab(n.kind === "unit" ? "source" : "relations");
    setInspector(true);
    if (small) setSheet(true);
  }
  function changePage(n: number) {
    setResumeUnit(undefined);
    setOffset(n);
    onOffset(n);
    setAnchor(null);
    setSelection(null);
    setEdge(null);
    setBranches({});
    setRemoved({});
    setGroup("");
    setRelOffset(0);
    setTab("source");
  }
  function expand() {
    if (!node) return;
    const branch: Scene =
      node.kind === "unit"
        ? neighbors(node, relationQuery.data?.items ?? [])
        : {
            nodes: [node, ...(childQuery.data?.items ?? [])],
            edges: (childQuery.data?.items ?? []).map((n) => ({
              source: node.id,
              target: n.id,
              kind: "contains",
            })),
          };
    setBranches((prev) => ({
      ...prev,
      [JSON.stringify([node.id, group])]: { ...joinScenes(prev[JSON.stringify([node.id, group])] ?? emptyScene, branch), owner: node.id },
    }));
    setView("graph");
    if (small) setSheet(false);
  }
  function collapse() {
    if (!node) return;
    const entries = Object.entries(branches);
    setRemoved(Object.fromEntries(entries.filter(([, b]) => b.owner === node.id)));
    setBranches(Object.fromEntries(entries.filter(([, b]) => b.owner !== node.id)));
    if (small) setSheet(false);
  }
  function undoCollapse() {
    setBranches((prev) => {
      const next = { ...prev };
      for (const [id, branch] of Object.entries(removed)) {
        next[id] = { owner: branch.owner, ...joinScenes(branch, prev[id] ?? emptyScene) };
      }
      return next;
    });
    setRemoved({});
  }
  function close() {
    setSheet(false);
    setInspector(false);
  }
  useEffect(() => {
    const sync = () => setFull(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);
  const [fullError, setFullError] = useState(false);
  async function toggleFull() {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await box.current?.requestFullscreen();
    } catch {
      setFullError(true);
    }
  }
  const chooseGroup = (g: WikiGraphRelation | "") => {
    setGroup(g);
    setRelOffset(0);
  };
  const section = node?.kind === "unit" ? relationQuery : childQuery;
  const showSheet = small && sheet;
  const needsGraph = view === "graph" && root?.kind === "unit";
  const graphPending = needsGraph && (rootOriginal.isPending || firstRing.isPending);
  const graphError = needsGraph && (rootOriginal.isError || firstRing.isError);
  const detailRequest = node?.kind === "unit" && tab !== "relations" ? detail : section;
  const detailLabel = t(node?.kind === "unit" && tab !== "relations" ? "wiki.search.loadingSource" : "wiki.search.loadingRelations");
  const listLabel = t(search ? "wiki.search.loadingHits" : "wiki.search.loadingDocs");
  const reloadList = mainResult.isFetching && !!mainResult.data;
  let columns = "grid-cols-1";
  if (!small && view === "graph") {
    columns = inspector
      ? "grid-cols-[minmax(11rem,0.65fr)_minmax(0,1.65fr)_minmax(17rem,0.85fr)]"
      : "grid-cols-[minmax(11rem,0.65fr)_minmax(0,2.5fr)]";
  } else if (!small && inspector) {
    columns = "grid-cols-[minmax(0,1fr)_minmax(17rem,0.55fr)]";
  }
  const panel = edge ? (
    <RelationInspector edge={edge} members={scene.edges.filter((e) => e.source === edge.source && e.target === edge.target)} nodes={scene.nodes} onPick={(n) => pick(n)} />
  ) : node ? (
    <WikiInspector
      key={node.id}
      node={node}
      original={detail.data}
      relations={relationQuery.data}
      children={childQuery.data}
      tab={tab}
      setTab={setTab}
      group={group}
      chooseGroup={chooseGroup}
      offset={relOffset}
      onPage={setRelOffset}
      select={(n) => pick(n)}
      expand={expand}
      collapse={activeBranches.some((b) => b.owner === node.id) ? collapse : undefined}
      picks={picks}
      toggle={toggle}
      loading={detailRequest.isPending
        ? (graphPending && !showSheet ? <Skeleton className="h-20 w-full" /> : <Busy name="detail" label={detailLabel} />)
        : detailRequest.isFetching && <Refresh name="detail" label={detailLabel} />}
      error={
        <>
          {node.kind === "unit" && detail.isError && (
            <Failed error={detail.error} retry={() => void detail.refetch()} />
          )}
          {section.isError && <Failed error={section.error} retry={() => void section.refetch()} />}
        </>
      }
    />
  ) : null;
  const left = (
    <div className="flex min-h-0 min-w-0 flex-col">
      <h3 className="shrink-0 border-b border-border p-2 text-sm font-semibold">
        {t(search ? "wiki.search.hits" : "wiki.search.documents")}
      </h3>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {rows.map((n, i) => {
          const match = hits.data?.items[i];
          return (
            <button
              type="button"
              key={n.id}
              disabled={mainResult.isPlaceholderData}
              aria-pressed={node?.id === n.id && !edge}
              data-surface-tint={node?.id === n.id && !edge ? "accent" : undefined}
              onClick={() => pick(n, true)}
              className="grid w-full grid-cols-1 gap-1 border-b border-border border-l-2 border-l-transparent p-3 text-left hover:bg-surface-hover focus-visible:outline-focus-ring aria-pressed:border-l-accent-fg aria-pressed:bg-accent-muted"
            >
              <span className="flex min-w-0 gap-2">
                <span className="text-xs tabular-nums text-fg-muted">{offset + i + 1}</span>
                <strong className="min-w-0 text-sm [overflow-wrap:anywhere]">{n.label}</strong>
              </span>
              {search && (
                <span className="flex min-w-0 items-center gap-1 text-xs text-fg-muted">
                  <BookOpen size={13} className="shrink-0" aria-hidden />
                  <span className="min-w-0 truncate">{n.document_name}</span>
                </span>
              )}
              {match?.knowledge_base_ids.map((id) => (
                <span key={id} className="min-w-0 truncate text-xs text-fg-muted">
                  {kbs.find((k) => k.id === id)?.name ?? id}
                </span>
              ))}
            </button>
          );
        })}
      </div>
      {total !== undefined ? (
        <OffsetPagination
          offset={offset}
          limit={RESULT_PAGE}
          count={rows.length}
          total={total}
          onPageChange={(_, n) => changePage(n)}
          ariaLabel={t("wiki.search.hits")}
        />
      ) : (
        !search &&
        docs.data?.has_more && (
          <div className="flex justify-end gap-1">
            <Button
              size="sm"
              variant="ghost"
              iconOnly
              icon={ChevronLeft}
              aria-label={t("pager.prev")}
              disabled={!offset}
              onClick={() => changePage(offset - RESULT_PAGE)}
            />
            <Button
              size="sm"
              variant="ghost"
              iconOnly
              icon={ChevronRight}
              aria-label={t("pager.next")}
              onClick={() => changePage(offset + RESULT_PAGE)}
            />
          </div>
        )
      )}
    </div>
  );
  return (
    <Card>
      <div ref={box} className="flex min-w-0 flex-col bg-surface">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border p-3">
          <h2 className="font-semibold">
            {t("wiki.search.results")}
            {total !== undefined && (
              <span className="ml-2 text-xs text-fg-muted">
                {t("wiki.documents.count", { count: total })}
              </span>
            )}
          </h2>
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              icon={Network}
              pressed={view === "graph"}
              onClick={() => setView("graph")}
            >
              {t("wiki.search.graph")}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={List}
              pressed={view === "list"}
              onClick={() => setView("list")}
            >
              {t("wiki.search.list")}
            </Button>
            {!small && !inspector && (
              <Button
                variant="ghost"
                iconOnly
                icon={PanelRight}
                size="sm"
                aria-label={t("wiki.search.openDetail")}
                onClick={() => setInspector(true)}
              />
            )}
            {(wideScreen || full) && !fullError && (
              <Button
                variant="ghost"
                iconOnly
                size="sm"
                icon={full ? Minimize : Maximize}
                aria-label={t(full ? "wiki.search.shrink" : "wiki.search.full")}
                onClick={() => void toggleFull()}
              />
            )}
          </div>
        </header>
        {view === "graph" && rows.length > 0 && (
          <Disclosure summary={t("wiki.search.graphFilters")} surface="sunken">
            <div className="flex flex-wrap items-end gap-2">
              <SelectField
                id="wiki-node-kind"
                label={t("wiki.graph.kind")}
                value={kind}
                onValueChange={setKind}
                options={[
                  { value: "", label: t("wiki.graph.allKinds") },
                  ...(["document", "section", "unit"] as const).map((value) => ({
                    value,
                    label: t(`wiki.graph.kind.${value}`),
                  })),
                ]}
              />
              <GraphLegend
                placement="inline"
                label={t("wiki.graph.legend")}
                disabledIds={hidden}
                items={(Object.keys(WIKI_RELATION_ICONS) as WikiGraphRelation[]).map((id) => {
                  const Icon = WIKI_RELATION_ICONS[id];
                  return { id, label: wikiRelationText(id), marker: <Icon size={14} aria-hidden /> };
                })}
                onToggle={(id) =>
                  setHidden((prev) => {
                    const next = new Set(prev);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    return next;
                  })
                }
              />
            </div>
          </Disclosure>
        )}
        {reloadList && <Refresh name="results" label={listLabel} />}
        {mainResult.isError && mainResult.data && <Failed error={mainResult.error} retry={() => void mainResult.refetch()} />}
        {mainResult.isPending ? (
          <Busy name="results" label={listLabel} large />
        ) : mainResult.isError && !mainResult.data ? (
          <Failed error={mainResult.error} retry={() => void mainResult.refetch()} />
        ) : !rows.length && offset > 0 ? (
          <div className="min-h-80">{left}</div>
        ) : !rows.length ? (
          <EmptyState title={t(search ? "search.noResults" : "wiki.graph.empty")} />
        ) : (
          <div
            aria-busy={reloadList || undefined}
            className={`grid min-h-80 ${columns} ${staleContentClass(mainResult.isPlaceholderData)} ${full ? "flex-1" : "h-[clamp(26rem,65dvh,60rem)]"}`}
          >
            {(view === "list" || !small) && left}
            {view === "graph" && (
              <div className="flex min-h-0 min-w-0 flex-col border-x border-border">
                <div className="flex items-center justify-between gap-2 border-b border-border p-2 text-xs text-fg-muted">
                  <strong>{t("wiki.search.graph")}</strong>
                  {!!Object.keys(removed).length && <Button variant="ghost" size="sm" onClick={undoCollapse}>{t("wiki.search.undoCollapse")}</Button>}
                  {!graphPending && !graphError && t("wiki.search.figures", { nodes: scene.nodes.length, edges: scene.edges.length })}
                </div>
                <div className="min-h-0 flex-1">
                  {graphPending ? (showSheet ? <Skeleton className="h-full w-full" /> : <Busy name="graph" label={t("wiki.search.loadingRelations")} large />) : graphError ? (
                    <Failed error={firstRing.error ?? rootOriginal.error} retry={() => { if (firstRing.isError) void firstRing.refetch(); if (rootOriginal.isError) void rootOriginal.refetch(); }} />
                  ) : <WikiScene
                    root={key(root)}
                    matches={new Set(search ? rows.map((n) => n.id) : [])}
                    scene={scene}
                    selected={edge ? undefined : node?.id}
                    edge={(edge && edgeId(edge)) || undefined}
                    onNode={(n) => pick(n)}
                    onEdge={(e) => {
                      setEdge(e);
                      setInspector(true);
                      if (small) setSheet(true);
                    }}
                  />}
                </div>
              </div>
            )}
            {!small && inspector && (
              <aside
                aria-label={t("wiki.search.detail")}
                className="flex min-h-0 min-w-0 flex-col border-l border-border"
              >
                <div className="flex items-center justify-between border-b border-border px-2">
                  <strong className="text-sm">{t("wiki.search.detail")}</strong>
                  <Button
                    iconOnly
                    icon={X}
                    size="sm"
                    variant="ghost"
                    aria-label={t("wiki.graph.clearSelection")}
                    onClick={close}
                  />
                </div>
                {panel}
              </aside>
            )}
          </div>
        )}
        {small && (
          <SideSheet
            side="bottom"
            open={sheet}
            onClose={close}
            initialFocus="dialog"
            title={t("wiki.search.detail")}
            closeLabel={t("wiki.graph.clearSelection")}
            bodyClassName="p-0"
          >
            {sheet && panel}
          </SideSheet>
        )}
      </div>
    </Card>
  );
}
