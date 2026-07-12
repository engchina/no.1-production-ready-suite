import { CheckCircle2, RefreshCw, Save, SlidersHorizontal } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  StatusBadge,
  cn,
} from "@engchina/production-ready-ui";

import { OntologyWorkspace } from "./OntologyWorkspace";
import {
  createProfileOntologyDraftPayload,
  displayProfileOntologyGraph,
  profileOntologyObjectFromNode,
  type ProfileOntologyDraftPayload,
  type ProfileOntologyDraftState,
} from "./ProfileOntologyEditorCore";
import type {
  OntologyCardinality,
  OntologyEdge,
  OntologyGraph,
  OntologyNode,
} from "./types";

type SaveState = "idle" | "saving" | "saved" | "error";

export interface ProfileOntologyEditorLabels {
  graphTitle: string;
  graphDescription: string;
  relationListTitle: string;
  relationListDescription: string;
  graphEmpty: string;
  graphUnavailableTitle: string;
  graphUnavailable: string;
  graphLayoutNoticeTitle: string;
  graphLayoutNotice: string;
  unresolvedTitle: string;
  refreshSchema: string;
  refreshingSchema: string;
  selectedSummary: (tables: number, views: number) => string;
  revisionBadge: (version: number, status: string) => string;
  inspectorTitle: string;
  inspectorDescription: string;
  inspectorEmpty: string;
  nodeInspectorTitle: string;
  relationshipInspectorTitle: string;
  physicalObject: string;
  businessName: string;
  tableUsage: string;
  tableUsagePlaceholder: string;
  relationshipName: string;
  cardinality: string;
  allowedPath: string;
  allowedPathDescription: string;
  physicalMappingRelationshipNotice: string;
  cardinalityLabels: Record<OntologyCardinality, string>;
  saveDraft: string;
  savingDraft: string;
  saveSuccessTitle: string;
  saveSuccess: string;
  saveErrorTitle: string;
  saveError: string;
  saveUnavailable: string;
}

export const DEFAULT_PROFILE_ONTOLOGY_EDITOR_LABELS: ProfileOntologyEditorLabels = {
  graphTitle: "物理・業務モデル",
  graphDescription:
    "Profile が許可した表・ビューと業務エンティティを、公開 Ontology の FK・承認済み関係とともに表示します。",
  relationListTitle: "関連一覧",
  relationListDescription:
    "グラフと同じ関係を一覧で確認できます。複合 Join 条件は列順を保持します。",
  graphEmpty: "表示できる表またはビューがありません。対象オブジェクトを選択して保存してください。",
  graphUnavailableTitle: "物理・業務モデルはまだ表示できません",
  graphUnavailable:
    "上の「AI 構築 → 承認 → Ontology を公開」を完了すると、この Profile が許可した表・ビューと承認済みの関係が、関係グラフとしてここに表示されます。",
  graphLayoutNoticeTitle: "レイアウト変更について",
  graphLayoutNotice:
    "ノードのドラッグは表示位置だけを変更します。関係や Join 条件は変更されません。",
  unresolvedTitle: "公開 Ontology に解決できない対象オブジェクトがあります",
  refreshSchema: "スキーマ情報を更新",
  refreshingSchema: "更新中…",
  selectedSummary: (tables, views) => `選択中: 表 ${tables} 件、ビュー ${views} 件`,
  revisionBadge: (version, status) => `Ontology rev v${version} (${status})`,
  inspectorTitle: "Inspector",
  inspectorDescription:
    "業務名、用途、関係の基数と許可パスを Profile の draft として編集します。",
  inspectorEmpty: "グラフまたは関連一覧からノード・関係を選択してください。",
  nodeInspectorTitle: "業務エンティティ",
  relationshipInspectorTitle: "業務関係",
  physicalObject: "物理オブジェクト",
  businessName: "日本語の業務名",
  tableUsage: "表・ビューの用途",
  tableUsagePlaceholder: "例: 確定済み受注の売上分析に使用",
  relationshipName: "関係名",
  cardinality: "基数",
  allowedPath: "検索時にこの関係パスを許可する",
  allowedPathDescription:
    "許可したパスだけが Ontology に基づく Join 候補になります。正式な FK 自体はここでは変更しません。",
  physicalMappingRelationshipNotice:
    "これは物理オブジェクトと業務概念の対応です。関係の基数や許可パスは編集できません。",
  cardinalityLabels: {
    one_to_one: "1 対 1",
    one_to_many: "1 対 多",
    many_to_one: "多 対 1",
    many_to_many: "多 対 多",
    unknown: "未確認",
  },
  saveDraft: "Draft を保存",
  savingDraft: "保存中…",
  saveSuccessTitle: "Draft を保存しました",
  saveSuccess: "公開 Ontology は変更されていません。レビュー後に反映してください。",
  saveErrorTitle: "Draft を保存できませんでした",
  saveError: "入力内容を保持しています。接続状態を確認して、もう一度実行してください。",
  saveUnavailable: "Draft 保存処理が接続されていません。",
};

export interface ProfileOntologyEditorProps {
  graph: OntologyGraph | null;
  selectedTables: string[];
  selectedViews: string[];
  profileId: string;
  /** backend の GET ontology-view が返す診断 warning(未解決オブジェクト名など) */
  warnings?: string[];
  onSaveDraft?: (payload: ProfileOntologyDraftPayload) => void | Promise<void>;
  onDraftChange?: (draft: ProfileOntologyDraftState) => void;
  /** POST /api/schema/refresh 相当。実行後に ontology-view を再取得する */
  onRefreshSchema?: () => void | Promise<void>;
  refreshingSchema?: boolean;
  initialDraft?: ProfileOntologyDraftState;
  labels?: Partial<ProfileOntologyEditorLabels>;
  className?: string;
}

const inputClass =
  "min-h-11 w-full min-w-0 rounded-md border border-border bg-card px-3 py-2 text-sm outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";
const textareaClass =
  "min-h-28 w-full resize-y rounded-md border border-border bg-card px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-ring/40";

function mergedLabels(
  overrides: Partial<ProfileOntologyEditorLabels> | undefined
): ProfileOntologyEditorLabels {
  return {
    ...DEFAULT_PROFILE_ONTOLOGY_EDITOR_LABELS,
    ...overrides,
    cardinalityLabels: {
      ...DEFAULT_PROFILE_ONTOLOGY_EDITOR_LABELS.cardinalityLabels,
      ...overrides?.cardinalityLabels,
    },
  };
}

function NodeInspector({
  node,
  draft,
  labels,
  onChange,
}: {
  node: OntologyNode;
  draft: ProfileOntologyDraftState;
  labels: ProfileOntologyEditorLabels;
  onChange: (next: ProfileOntologyDraftState) => void;
}) {
  const reference = profileOntologyObjectFromNode(node);
  const value = draft.nodes[node.id] ?? {};
  const update = (patch: { business_name_ja?: string; table_usage?: string }) => {
    onChange({
      ...draft,
      nodes: {
        ...draft.nodes,
        [node.id]: {
          business_name_ja: value.business_name_ja ?? node.business_name_ja,
          table_usage: value.table_usage ?? "",
          ...patch,
        },
      },
    });
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge variant="info" label={labels.nodeInspectorTitle} />
        {reference ? (
          <StatusBadge variant="neutral" label={reference.qualifiedName} />
        ) : null}
      </div>
      {reference ? (
        <div>
          <span className="block text-xs font-semibold text-muted">
            {labels.physicalObject}
          </span>
          <code className="mt-1 block break-all rounded-md bg-muted/30 px-3 py-2 text-xs text-foreground">
            {reference.qualifiedName}
          </code>
        </div>
      ) : null}
      <label className="block space-y-2 text-sm font-medium text-foreground">
        <span>{labels.businessName}</span>
        <input
          className={inputClass}
          value={value.business_name_ja ?? node.business_name_ja}
          onChange={(event) => update({ business_name_ja: event.target.value })}
        />
      </label>
      <label className="block space-y-2 text-sm font-medium text-foreground">
        <span>{labels.tableUsage}</span>
        <textarea
          className={textareaClass}
          value={value.table_usage ?? ""}
          placeholder={labels.tableUsagePlaceholder}
          onChange={(event) => update({ table_usage: event.target.value })}
        />
      </label>
    </div>
  );
}

function isPhysicalStructureEdge(edge: OntologyEdge): boolean {
  return (
    edge.kind === "maps_to" ||
    edge.kind === "contains" ||
    edge.kind === "lineage" ||
    edge.metadata?.relationship_type === "physical_mapping"
  );
}

function EdgeInspector({
  edge,
  draft,
  labels,
  onChange,
}: {
  edge: OntologyEdge;
  draft: ProfileOntologyDraftState;
  labels: ProfileOntologyEditorLabels;
  onChange: (next: ProfileOntologyDraftState) => void;
}) {
  const isPhysicalMapping = isPhysicalStructureEdge(edge);
  const value = draft.edges[edge.id] ?? {};
  const update = (patch: { cardinality?: OntologyCardinality; allowed_path?: boolean }) => {
    onChange({
      ...draft,
      edges: {
        ...draft.edges,
        [edge.id]: {
          cardinality: value.cardinality ?? edge.cardinality ?? "unknown",
          allowed_path: value.allowed_path ?? edge.enabled ?? false,
          ...patch,
        },
      },
    });
  };

  return (
    <div className="space-y-5">
      <StatusBadge variant="info" label={labels.relationshipInspectorTitle} />
      <div>
        <span className="block text-xs font-semibold text-muted">
          {labels.relationshipName}
        </span>
        <p className="mt-1 break-words text-sm font-semibold text-foreground">
          {edge.relationship_name_ja}
        </p>
      </div>
      {isPhysicalMapping ? (
        <Banner severity="info">{labels.physicalMappingRelationshipNotice}</Banner>
      ) : (
        <>
          <label className="block space-y-2 text-sm font-medium text-foreground">
            <span>{labels.cardinality}</span>
            <select
              className={inputClass}
              value={value.cardinality ?? edge.cardinality ?? "unknown"}
              onChange={(event) =>
                update({ cardinality: event.target.value as OntologyCardinality })
              }
            >
              {(
                [
                  "one_to_one",
                  "one_to_many",
                  "many_to_one",
                  "many_to_many",
                  "unknown",
                ] as OntologyCardinality[]
              ).map((cardinality) => (
                <option key={cardinality} value={cardinality}>
                  {labels.cardinalityLabels[cardinality]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex min-h-11 cursor-pointer items-start gap-3 rounded-lg border border-border p-3 outline-none focus-within:ring-2 focus-within:ring-ring/40">
            <input
              type="checkbox"
              className="mt-0.5 h-5 w-5 shrink-0 accent-primary"
              checked={value.allowed_path ?? edge.enabled ?? false}
              onChange={(event) => update({ allowed_path: event.target.checked })}
            />
            <span>
              <span className="block text-sm font-semibold text-foreground">
                {labels.allowedPath}
              </span>
              <span className="mt-1 block text-xs leading-5 text-muted">
                {labels.allowedPathDescription}
              </span>
            </span>
          </label>
        </>
      )}
    </div>
  );
}

// 「物理・業務モデル」セクション。オントロジー構築(AI 提案 → 承認 → 公開)の直下に置き、
// 公開済み Ontology の profile スコープを確認・draft 編集する。
export function ProfileOntologyEditor({
  graph,
  selectedTables,
  selectedViews,
  profileId,
  warnings = [],
  onSaveDraft,
  onDraftChange,
  onRefreshSchema,
  refreshingSchema = false,
  initialDraft,
  labels: labelOverrides,
  className,
}: ProfileOntologyEditorProps) {
  const labels = mergedLabels(labelOverrides);
  const [draft, setDraft] = useState<ProfileOntologyDraftState>(() =>
    initialDraft
      ? {
          nodes: { ...initialDraft.nodes },
          edges: { ...initialDraft.edges },
        }
      : { nodes: {}, edges: {} }
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  useEffect(() => {
    setDraft(
      initialDraft
        ? { nodes: { ...initialDraft.nodes }, edges: { ...initialDraft.edges } }
        : { nodes: {}, edges: {} }
    );
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setSaveState("idle");
  }, [initialDraft, profileId]);
  const displayGraph = useMemo(
    () => displayProfileOntologyGraph(graph, draft),
    [draft, graph]
  );
  const selectedNode =
    displayGraph.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const selectedEdge =
    displayGraph.edges.find((edge) => edge.id === selectedEdgeId) ?? null;
  const selectedSummary = labels.selectedSummary(selectedTables.length, selectedViews.length);
  const revision = graph?.revision;

  const changeDraft = (next: ProfileOntologyDraftState) => {
    setDraft(next);
    setSaveState("idle");
    onDraftChange?.(next);
  };

  const selectNode = (node: OntologyNode) => {
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
  };

  const selectEdge = (edge: OntologyEdge) => {
    setSelectedNodeId(null);
    setSelectedEdgeId(edge.id);
  };

  const saveDraft = async () => {
    if (!onSaveDraft || saveState === "saving") return;
    setSaveState("saving");
    try {
      await onSaveDraft(
        createProfileOntologyDraftPayload(
          profileId,
          graph,
          selectedTables,
          selectedViews,
          draft
        )
      );
      setSaveState("saved");
    } catch {
      setSaveState("error");
    }
  };

  const refreshSchemaButton = onRefreshSchema ? (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      loading={refreshingSchema}
      disabled={refreshingSchema}
      onClick={() => void onRefreshSchema()}
    >
      <RefreshCw size={15} aria-hidden="true" />
      <span>{refreshingSchema ? labels.refreshingSchema : labels.refreshSchema}</span>
    </Button>
  ) : undefined;

  const unresolvedBanner =
    warnings.length > 0 ? (
      <div data-testid="profile-ontology-unresolved">
        <Banner severity="warning" title={labels.unresolvedTitle} action={refreshSchemaButton}>
          <ul className="grid gap-1 pl-4">
            {warnings.map((warning) => (
              <li key={warning} className="list-disc break-words">
                {warning}
              </li>
            ))}
          </ul>
        </Banner>
      </div>
    ) : null;

  if (graph === null) {
    return (
      <section
        className={cn(
          "space-y-4 rounded-md border border-border bg-card p-4 shadow-sm",
          className
        )}
        aria-label={labels.graphTitle}
        data-testid="profile-ontology-empty"
      >
        <header className="space-y-1">
          <h2 className="text-base font-semibold text-foreground">{labels.graphTitle}</h2>
          <p className="text-sm leading-6 text-muted">{labels.graphDescription}</p>
        </header>
        {unresolvedBanner}
        <EmptyState title={labels.graphUnavailableTitle} hint={labels.graphUnavailable} />
      </section>
    );
  }

  return (
    <section className={cn("space-y-4", className)} aria-label={labels.graphTitle}>
      <div className="flex flex-wrap items-center justify-end gap-2">
        {revision ? (
          <StatusBadge
            variant="neutral"
            label={labels.revisionBadge(revision.version, revision.status)}
          />
        ) : null}
        <StatusBadge variant="info" label={selectedSummary} />
      </div>
      {unresolvedBanner}
      <Banner severity="info" title={labels.graphLayoutNoticeTitle}>
        {labels.graphLayoutNotice}
      </Banner>
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_23rem]">
        <OntologyWorkspace
          graph={displayGraph}
          title={labels.graphTitle}
          description={labels.graphDescription}
          selectedNodeId={selectedNodeId}
          selectedEdgeId={selectedEdgeId}
          onSelectNode={selectNode}
          onSelectEdge={selectEdge}
          labels={{
            empty: labels.graphEmpty,
            relationListTitle: labels.relationListTitle,
            relationListDescription: labels.relationListDescription,
          }}
        />
        <Card className="xl:sticky xl:top-4">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <SlidersHorizontal size={17} className="text-primary" aria-hidden="true" />
              {labels.inspectorTitle}
            </CardTitle>
            <CardDescription className="mt-1 leading-6">
              {labels.inspectorDescription}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {selectedNode ? (
              <NodeInspector
                node={selectedNode}
                draft={draft}
                labels={labels}
                onChange={changeDraft}
              />
            ) : selectedEdge ? (
              <EdgeInspector
                edge={selectedEdge}
                draft={draft}
                labels={labels}
                onChange={changeDraft}
              />
            ) : (
              <Banner severity="info">{labels.inspectorEmpty}</Banner>
            )}

            {saveState === "saved" ? (
              <Banner severity="success" title={labels.saveSuccessTitle}>
                {labels.saveSuccess}
              </Banner>
            ) : null}
            {saveState === "error" ? (
              <Banner severity="danger" title={labels.saveErrorTitle}>
                {labels.saveError}
              </Banner>
            ) : null}
            {!onSaveDraft ? (
              <Banner severity="warning">{labels.saveUnavailable}</Banner>
            ) : null}
            <Button
              type="button"
              size="md"
              variant="primary"
              className="w-full"
              disabled={!onSaveDraft || saveState === "saving"}
              onClick={() => void saveDraft()}
            >
              {saveState === "saving" ? (
                <CheckCircle2 size={16} aria-hidden="true" />
              ) : (
                <Save size={16} aria-hidden="true" />
              )}
              {saveState === "saving" ? labels.savingDraft : labels.saveDraft}
            </Button>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}
