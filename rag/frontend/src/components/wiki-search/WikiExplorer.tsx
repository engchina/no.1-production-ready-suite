import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
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

interface Props {
  scope: WikiScope;
  question: string;
  method: WikiMethod;
  kbs: WikiKb[];
  picks?: WikiPick[];
  toggle?: (p: WikiPick) => void;
  initialUnit?: string;
  onRoot?: (id: string) => void;
}
const RESULT_PAGE = 5;
function Busy({ name }: { name: string }) {
  return (
    <TimedLoadingState label={t("wiki.graph.loading")} operationKey={name}>
      <Skeleton className="h-20 w-full" />
    </TimedLoadingState>
  );
}
function Failed({ error, retry }: { error: unknown; retry: () => void }) {
  return <ApiErrorState error={error} fallback={t("wiki.graph.error")} onRetry={retry} />;
}
export function WikiExplorer({ scope, question, method, kbs, picks, toggle, initialUnit, onRoot }: Props) {
  const base = ["wiki-explorer", scope] as const;
  const small = useMediaQuery("(max-width: 767px)", false);
  const [view, setView] = useState<"graph" | "list">(() =>
    window.matchMedia("(max-width: 767px)").matches ? "list" : "graph",
  );
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState("");
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const [anchor, setAnchor] = useState<WikiNode | null>(null);
  const [selection, setSelection] = useState<WikiNode | null>(null);
  const [edge, setEdge] = useState<Connection | null>(null);
  const [group, setGroup] = useState<WikiGraphRelation | "">("");
  const [tab, setTab] = useState("source");
  const [relOffset, setRelOffset] = useState(0);
  const [branches, setBranches] = useState<Record<string, Scene>>({});
  const [sheet, setSheet] = useState(false),
    [inspector, setInspector] = useState(true);
  const [full, setFull] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const search = Boolean(question.trim());
  const hits = useQuery({
    queryKey: [...base, "search", question, method, offset],
    queryFn: ({ signal }) => wikiApi.hits(scope, question, method, offset, RESULT_PAGE, signal),
    enabled: search,
    retry: false,
  });
  const docs = useQuery({
    queryKey: [...base, "docs", offset],
    queryFn: ({ signal }) => wikiApi.documents(scope, offset, RESULT_PAGE, signal),
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
  const total = search ? hits.data?.total : docs.data?.has_more ? count.data?.total : offset + rows.length;
  const mainResult = search ? hits : docs;
  const root = anchor ?? rows.find((n) => n.id === initialUnit) ?? rows[0];
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
  const all = joinScenes(ring, structure, ...Object.values(branches));
  const visibleNodes = all.nodes.filter((n) => !kind || n.kind === kind);
  const visibleIds = new Set(visibleNodes.map((n) => n.id));
  const scene = {
    nodes: visibleNodes,
    edges: all.edges.filter(
      (e) => visibleIds.has(e.source) && visibleIds.has(e.target) && !hidden.has(e.kind),
    ),
  };
  function pick(n: WikiNode, asRoot = false) {
    if (asRoot) {
      setAnchor(n);
      setBranches({});
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
    setOffset(n);
    setAnchor(null);
    setSelection(null);
    setEdge(null);
    setBranches({});
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
      [node.id + ":" + group]: joinScenes(prev[node.id + ":" + group] ?? emptyScene, branch),
    }));
    setView("graph");
    if (small) setSheet(false);
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
  const panel = edge ? (
    <RelationInspector edge={edge} nodes={scene.nodes} onPick={(n) => pick(n)} />
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
      picks={picks}
      toggle={toggle}
      loading={((node.kind === "unit" && detail.isPending) || section.isFetching) && <Busy name={node.id} />}
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
            {!small && !fullError && (
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
        {mainResult.isPending ? (
          <Busy name="wiki-results" />
        ) : mainResult.isError ? (
          <Failed error={mainResult.error} retry={() => void mainResult.refetch()} />
        ) : !rows.length ? (
          <EmptyState title={t(search ? "search.noResults" : "wiki.graph.empty")} />
        ) : (
          <div
            className={`grid min-h-80 ${full ? "flex-1" : "h-[clamp(26rem,65dvh,60rem)]"} ${view === "graph" ? (inspector ? "md:grid-cols-[minmax(11rem,0.65fr)_minmax(0,1.65fr)_minmax(17rem,0.85fr)]" : "md:grid-cols-[minmax(11rem,0.65fr)_minmax(0,2.5fr)]") : inspector ? "md:grid-cols-[minmax(0,1fr)_minmax(17rem,0.55fr)]" : "grid-cols-1"}`}
          >
            {(view === "list" || !small) && left}
            {view === "graph" && (
              <div className="flex min-h-0 min-w-0 flex-col border-x border-border">
                <div className="flex items-center justify-between gap-2 border-b border-border p-2 text-xs text-fg-muted">
                  <strong>{t("wiki.search.graph")}</strong>
                  {t("wiki.search.figures", { nodes: scene.nodes.length, edges: scene.edges.length })}
                </div>
                <div className="min-h-0 flex-1">
                  <WikiScene
                    root={key(root)}
                    scene={scene}
                    selected={edge ? undefined : node?.id}
                    edge={(edge && edgeId(edge)) || undefined}
                    onNode={(n) => pick(n)}
                    onEdge={(e) => {
                      setEdge(e);
                      setInspector(true);
                      if (small) setSheet(true);
                    }}
                  />
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
