import { useState } from "react";
import { ArrowLeft, ArrowRight, BookOpen, Focus, GitFork, Minus, Plus } from "lucide-react";
import { Banner, Button, OffsetPagination, TabPanel, Tabs, useMediaQuery } from "@production-ready/ui";
import { t } from "@/lib/i18n";
import { type WikiGraphRelation, type WikiGraphRelationItem } from "@/lib/api";
import { WikiRelationLabel, wikiRelationText } from "@/components/knowledge-bases/wiki-graph-labels";
import {
  sourceLocation,
  sourcePick,
  MAX_WIKI_SELECTION,
  type WikiDirection,
  type WikiOriginal,
  type WikiPick,
  type WikiRelations,
} from "./wiki-api";
import { edgeName, type Connection, type WikiGroupPoint, type WikiNode } from "./WikiScene";

export const NEIGHBOR_PAGE = 6;
export function RelationInspector({
  edge,
  members,
  nodes,
  onPick,
}: {
  edge: Connection;
  members: Connection[];
  nodes: WikiNode[];
  onPick: (n: WikiNode) => void;
}) {
  return (
    <div className="grid gap-3 p-3">
      <h3 className="font-semibold">{members.length > 1 ? t("wiki.search.edgeCount", { count: members.length }) : edgeName(edge)}</h3>
      {members.length > 1 && <ul className="list-inside list-disc text-sm">{members.map((e) => <li key={e.kind}>{edgeName(e)}</li>)}</ul>}
      {[edge.source, edge.target].map((id) => {
        const n = nodes.find((item) => item.id === id);
        return (
          n && (
            <Button key={n.id} variant="secondary" onClick={() => onPick(n)}>
              {n.label}
            </Button>
          )
        );
      })}
      {edge.cross && <p className="text-sm text-fg-muted">{t("wiki.graph.scope.cross_document")}</p>}
    </div>
  );
}
interface Props {
  node: WikiNode;
  original?: WikiOriginal;
  relations?: WikiRelations;
  children?: { items: WikiNode[]; total: number };
  tab: string;
  setTab: (s: string) => void;
  group: WikiGraphRelation | "";
  direction?: WikiDirection;
  selectedGroup?: WikiGroupPoint;
  focus?: () => void;
  chooseGroup: (g: WikiGraphRelation | "", d?: WikiDirection) => void;
  offset: number;
  onPage: (n: number) => void;
  select: (node: WikiNode) => void;
  expand: () => void;
  collapse?: () => void;
  picks?: WikiPick[];
  toggle?: (pick: WikiPick) => void;
  loading: React.ReactNode;
  error: React.ReactNode;
}
export function WikiInspector({
  node,
  original,
  relations,
  children,
  tab,
  setTab,
  group,
  chooseGroup,
  direction,
  selectedGroup,
  focus,
  offset,
  onPage,
  select,
  expand,
  collapse,
  picks,
  toggle,
  loading,
  error,
}: Props) {
  const [more, setMore] = useState(false);
  const mobile = useMediaQuery("(max-width: 767px)", false);
  const unit = node.kind === "unit";
  const chosen = picks?.some((item) => item.id === node.id) ?? false;
  const validAdd = chosen || (picks?.length ?? 0) < MAX_WIKI_SELECTION;
  const counts = Object.entries(relations?.counts ?? {}) as [WikiGraphRelation, number][];
  const count = unit ? relations?.total : children?.total;
  const displayed = unit ? relations?.items.length : children?.items.length;
  const choose = (r: WikiGraphRelation, d?: WikiDirection) => {
    const clear = group === r && direction === d && tab === "relations";
    chooseGroup(clear ? "" : r, clear ? undefined : d);
    setTab("relations");
  };
  const detailTabs = unit
    ? [
        { id: "source", label: t("wiki.graph.source") },
        { id: "relations", label: t("wiki.graph.relation") },
        { id: "guide", label: t("wiki.search.guide") },
      ]
    : [{ id: "relations", label: t("wiki.search.children") }];
  function related(row: WikiGraphRelationItem, i: number) {
    return (
      <li
        key={[row.direction, row.relation, row.unit?.id ?? row.target_name, i].join(":")}
        className="border-b border-border py-1.5"
      >
        <span className="text-xs text-fg-muted">
          {row.direction === "outgoing" ? "→" : "←"} <WikiRelationLabel relation={row.relation} />
        </span>
        {row.unit ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            onClick={() => select({ ...row.unit!, kind: "unit" })}
          >
            {row.unit.label}
          </Button>
        ) : (
          <p className="text-sm">
            {[row.target_name, row.target_section].filter(Boolean).join(" · ")} ·{" "}
            {t("wiki.graph.outsideKnowledgeBase")}
          </p>
        )}
        {row.unit && <p className="truncate text-xs text-fg-muted">{row.unit.document_name}</p>}
      </li>
    );
  }
  const chips = counts.length > 0 && (
    <section className="space-y-2" aria-label={t("wiki.search.directRelations")}>
      <h4 className="font-semibold">{t("wiki.search.directRelations")}</h4>
      <div className="flex flex-wrap items-center gap-2">
        {relations?.groups?.length ? relations.groups.map(g => (
          <Button key={g.direction + g.relation} size="sm" variant="secondary"
            icon={g.direction === "outgoing" ? ArrowRight : ArrowLeft}
            aria-label={`${t(g.direction === "outgoing" ? "wiki.search.outgoing" : "wiki.search.incoming")} · ${wikiRelationText(g.relation)} ${g.count}`}
            pressed={group === g.relation && direction === g.direction && tab === "relations"}
            onClick={() => choose(g.relation, g.direction)}>{wikiRelationText(g.relation)} {g.count}</Button>
        )) : counts.map(([r, n]) => (
          <Button key={r} size="sm" variant="secondary" pressed={group === r && tab === "relations"}
            onClick={() => choose(r)}>{wikiRelationText(r)} {n}</Button>
        ))}
      </div>
    </section>
  );
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col [overflow-wrap:anywhere]">
      <div className="space-y-1 p-3">
        <p className="text-xs text-fg-muted">{selectedGroup ? t("wiki.search.grouped") : t(`wiki.graph.kind.${node.kind}`)}</p>
        <h3 className="text-base font-semibold">{selectedGroup ? selectedGroup.label : node.label}</h3>
        {selectedGroup && <p className="text-sm">{t("wiki.search.groupLeft", { count: selectedGroup.remaining })}</p>}
        <p className="text-xs text-fg-muted">
          {[node.document_name, ...(original?.path ?? node.path ?? [])]
            .filter((v, i, all) => i === 0 || v !== all[i - 1])
            .join(" › ")}
        </p>
      </div>
      <Tabs
        items={detailTabs}
        idPrefix={mobile ? "wiki-sheet-tabs" : "wiki-detail-tabs"}
        value={unit ? tab : "relations"}
        onChange={setTab}
        ariaLabel={t("wiki.search.detail")}
      />
      <div className="relative min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain p-3">
        {error}
        {loading}
        {unit && (
          <TabPanel id="source" value={tab} idPrefix={mobile ? "wiki-sheet-tabs" : "wiki-detail-tabs"}>
            {original && (
              <div className="space-y-3">
                <div className="space-y-2">
                  {original.chunks.slice(0, more ? undefined : 1).map((chunk) => (
                    <div key={chunk.chunk_id}>
                      <p className="mb-1 flex items-start gap-1 text-xs text-fg-muted">
                        <BookOpen size={14} className="shrink-0" aria-hidden />
                        <span className="min-w-0">
                          {original.document_name} · {sourceLocation(chunk, original.label)}
                        </span>
                      </p>
                      <blockquote className="whitespace-pre-wrap break-words border-l-2 border-accent-fg bg-surface-sunken p-3 text-sm">
                        {chunk.text}
                      </blockquote>
                    </div>
                  ))}
                </div>
                {original.chunks.length > 1 && (
                  <Button variant="ghost" size="sm" icon={BookOpen} onClick={() => setMore(!more)}>
                    {t(more ? "wiki.graph.close" : "wiki.search.context")}
                  </Button>
                )}
                {chips}
              </div>
            )}
          </TabPanel>
        )}
        <TabPanel
          id="relations"
          value={unit ? tab : "relations"}
          idPrefix={mobile ? "wiki-sheet-tabs" : "wiki-detail-tabs"}
        >
          <div className="space-y-2">
            {unit && chips}
            {count === 0 && <p className="text-sm text-fg-muted">{t("wiki.graph.noRelations")}</p>}
            <ul>
              {unit
                ? relations?.items.map(related)
                : children?.items.map((n, index) => {
                    const heading = n.label === n.document_name ? "" : n.label;
                    const position = [
                      n.page && `p. ${n.page}`,
                      n.line && t("wiki.search.lines", { range: String(n.line) }),
                    ].filter(Boolean).join(" · ");
                    const title = n.kind === "section" ? n.label : heading || position ||
                      t("wiki.search.unitNumber", { number: offset + index + 1 });
                    return (
                      <li key={n.id} className="border-b border-border">
                        <button type="button" className="grid min-h-11 w-full gap-1 px-3 py-2 text-left hover:bg-surface-hover focus-visible:outline-focus-ring" onClick={() => select(n)}>
                            <span className="truncate">{title}</span>
                            {heading && position && <span className="text-xs text-fg-muted">{position}</span>}
                            {n.excerpt && <span className="line-clamp-2 whitespace-normal break-words text-sm font-normal text-fg-muted">{n.excerpt}</span>}
                        </button>
                      </li>
                    );
                  })}
            </ul>
            {typeof count === "number" && (
              <OffsetPagination
                offset={offset}
                limit={NEIGHBOR_PAGE}
                total={count}
                count={displayed ?? 0}
                onPageChange={(_, n) => onPage(n)}
                ariaLabel={t("wiki.graph.relation")}
              />
            )}
          </div>
        </TabPanel>
        {unit && (
          <TabPanel id="guide" value={tab} idPrefix={mobile ? "wiki-sheet-tabs" : "wiki-detail-tabs"}>
            {original && (
              <div className="space-y-3">
                <Banner severity="info">{t("wiki.graph.generatedNotice")}</Banner>
                <p className="text-sm">{original.context}</p>
                {(
                  [
                    ["wiki.graph.possibleQuestions", original.possible_questions],
                    ["wiki.graph.unresolved", original.unresolved],
                  ] as const
                ).map(
                  ([key, items]) =>
                    items.length > 0 && (
                      <section key={key}>
                        <h4 className="font-semibold">{t(key)}</h4>
                        <ul className="list-disc pl-5">
                          {items.map((s) => (
                            <li key={s}>{s}</li>
                          ))}
                        </ul>
                      </section>
                    ),
                )}
              </div>
            )}
          </TabPanel>
        )}
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-t border-border px-3 py-2">
        {focus && <Button variant="secondary" size="sm" icon={Focus} onClick={focus}>{t("wiki.search.focus")}</Button>}
        {unit && !selectedGroup && toggle && original && (
          <Button
            variant="secondary"
            size="sm"
            icon={chosen ? Minus : Plus}
            disabled={!validAdd}
            tooltip={!validAdd ? t("wiki.search.selectionLimit", { count: MAX_WIKI_SELECTION }) : undefined}
            onClick={() => toggle(sourcePick(original))}
          >
            {t(chosen ? "wiki.search.remove" : "wiki.search.add")}
          </Button>
        )}
        {collapse && <Button variant="secondary" size="sm" icon={Minus} onClick={collapse}>{t("wiki.search.collapse")}</Button>}
        <Button variant="secondary" size="sm" icon={GitFork} disabled={!displayed} onClick={expand}>
          {selectedGroup ? t("wiki.search.expandBatch", { count: Math.min(NEIGHBOR_PAGE, selectedGroup.remaining) }) : t("wiki.search.expand")}
        </Button>
      </div>
    </div>
  );
}
