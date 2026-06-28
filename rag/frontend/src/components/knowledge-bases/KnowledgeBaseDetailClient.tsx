"use client";

import { ChevronRight, FilePlus2, Files, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState } from "@/components/StateViews";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  type DocumentChunkSet,
  type DocumentChunkSetLayerStatuses,
  type DocumentMaterializationLayerStatus,
  type DocumentSummary,
  type KnowledgeBaseDetail,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t, type I18nKey } from "@/lib/i18n";
import {
  useAssignDocumentsToKnowledgeBase,
  useDocumentChunkSets,
  useDocuments,
  useEnqueueDocumentIngestionJob,
  useKnowledgeBase,
  useRemoveDocumentFromKnowledgeBase,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { KnowledgeBaseAdapterConfigPanel } from "./KnowledgeBaseAdapterConfigPanel";
import { KnowledgeBaseSearchTestPanel } from "./KnowledgeBaseSearchTestPanel";
import { KnowledgeBaseStatusPill } from "./KnowledgeBaseStatusPill";

/** ナレッジベース詳細ページ。概要・所属文書・構築設定(構築フロー + フォーム)を全幅で扱う。 */
export function KnowledgeBaseDetailClient({ knowledgeBaseId }: { knowledgeBaseId: string }) {
  const detail = useKnowledgeBase(knowledgeBaseId);

  if (detail.isPending) {
    return (
      <Card className="h-64 animate-pulse" role="status" aria-label={t("knowledgeBases.detail.loading")} />
    );
  }
  if (detail.isError || !detail.data) {
    return (
      <ErrorState
        message={
          detail.error instanceof ApiError ? detail.error.message : t("knowledgeBases.error.load")
        }
        onRetry={() => void detail.refetch()}
      />
    );
  }

  const kb = detail.data;
  const isActive = kb.status === "ACTIVE";

  return (
    <div className="space-y-5">
      {/* 概要: 名称・状態・メトリクス(この KB が何か) */}
      <Card>
        <CardContent className="space-y-5 pt-6">
          <div>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h1 className="min-w-0 truncate text-xl font-semibold text-foreground">{kb.name}</h1>
              <KnowledgeBaseStatusPill status={kb.status} />
            </div>
            {kb.description ? <p className="mt-1 text-sm text-muted">{kb.description}</p> : null}
          </div>

          <div className="grid grid-cols-3 gap-2 sm:max-w-md">
            <Metric label={t("knowledgeBases.metric.documents")} value={kb.document_count} />
            <Metric label={t("knowledgeBases.metric.indexed")} value={kb.indexed_document_count} />
            <Metric label={t("knowledgeBases.metric.errors")} value={kb.error_document_count} />
          </div>
        </CardContent>
      </Card>

      {/* 所属文書: 追加ツールバー(左寄せ)+ 一覧。追加操作は対象一覧の直上に置く。 */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Files className="size-4 text-muted" aria-hidden />
            {t("knowledgeBases.documents.title")}
            <span className="tnum rounded-md bg-muted/10 px-2 py-0.5 text-xs font-medium text-muted">
              {kb.document_count}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {isActive ? (
            <DocumentAssignment knowledgeBase={kb} />
          ) : (
            <p className="rounded-md border border-border bg-background px-3 py-2 text-sm text-muted">
              {t("knowledgeBases.detail.archivedHint")}
            </p>
          )}

          <KnowledgeBaseDocuments knowledgeBase={kb} />
        </CardContent>
      </Card>

      {/* このナレッジ単体で検索の手応えを確認(業務ビュー不要)。文書追加→検証→構築設定 の流れ。 */}
      <KnowledgeBaseSearchTestPanel
        knowledgeBaseId={kb.id}
        indexedDocumentCount={kb.indexed_document_count}
        disabled={!isActive}
      />

      <KnowledgeBaseAdapterConfigPanel
        knowledgeBaseId={kb.id}
        adapterConfig={kb.adapter_config}
        effectiveConfig={kb.effective_adapter_config}
        disabled={!isActive}
      />
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-background p-3">
      <p className="text-xs text-muted">{label}</p>
      <p className="tnum mt-1 text-lg font-semibold text-foreground">{formatNumber(value)}</p>
    </div>
  );
}

function DocumentAssignment({ knowledgeBase }: { knowledgeBase: KnowledgeBaseDetail }) {
  const allDocuments = useDocuments({ limit: 100, offset: 0 });
  const assign = useAssignDocumentsToKnowledgeBase();
  const [documentId, setDocumentId] = useState("");

  const options = useMemo(() => {
    const documents = allDocuments.data?.items ?? [];
    return documents.filter((document) => !documentHasKnowledgeBase(document, knowledgeBase.id));
  }, [allDocuments.data?.items, knowledgeBase.id]);

  const selectOptions = useMemo<SelectFieldOption[]>(
    () => options.map((document) => ({ value: document.id, label: document.file_name })),
    [options]
  );

  useEffect(() => {
    if (!documentId && options[0]) {
      setDocumentId(options[0].id);
      return;
    }
    if (documentId && options.length > 0 && !options.some((document) => document.id === documentId)) {
      setDocumentId(options[0].id);
    }
  }, [documentId, options]);

  const handleAssign = () => {
    if (!documentId) return;
    assign.mutate(
      { id: knowledgeBase.id, documentIds: [documentId] },
      {
        onSuccess: () => {
          setDocumentId("");
          toast.success(t("knowledgeBases.toast.assigned"));
        },
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.assign")),
      }
    );
  };

  return (
    <div className="space-y-2">
      {/* 追加ツールバー: コンボボックスは幅制約し、追加ボタンを入力のすぐ隣へ左寄せ(右端に孤立させない)。 */}
      <div className="flex flex-wrap items-end gap-2">
        <SelectField
          id="knowledge-base-add-document"
          label={t("knowledgeBases.assignment.title")}
          value={documentId}
          options={selectOptions}
          onValueChange={setDocumentId}
          placeholder={t("knowledgeBases.assignment.noOptions")}
          className="w-full min-w-0 sm:w-80"
          buttonClassName="h-9"
        />
        <Button
          type="button"
          variant="secondary"
          size="md"
          onClick={handleAssign}
          loading={assign.isPending}
          disabled={!documentId}
          className="h-9 shrink-0"
        >
          <FilePlus2 size={15} aria-hidden />
          {t("knowledgeBases.actions.assign")}
        </Button>
      </div>
      {allDocuments.isError ? (
        <FormStatus
          tone="danger"
          message={
            allDocuments.error instanceof ApiError
              ? allDocuments.error.message
              : t("knowledgeBases.error.documents")
          }
        />
      ) : null}
    </div>
  );
}

function KnowledgeBaseDocuments({ knowledgeBase }: { knowledgeBase: KnowledgeBaseDetail }) {
  const confirm = useConfirm();
  const documents = useDocuments({ knowledge_base_id: knowledgeBase.id, limit: 50, offset: 0 });
  const remove = useRemoveDocumentFromKnowledgeBase();

  const handleRemove = async (document: DocumentSummary) => {
    const ok = await confirm({
      title: t("knowledgeBases.confirm.remove.title"),
      description: t("knowledgeBases.confirm.remove.description", {
        fileName: document.file_name,
        name: knowledgeBase.name,
      }),
      confirmLabel: t("knowledgeBases.actions.remove"),
      tone: "warning",
    });
    if (!ok) return;
    remove.mutate(
      { knowledgeBaseId: knowledgeBase.id, documentId: document.id },
      {
        onSuccess: () => toast.success(t("knowledgeBases.toast.removed")),
        onError: (error) =>
          toast.error(error instanceof ApiError ? error.message : t("knowledgeBases.error.remove")),
      }
    );
  };

  return (
    <div className="space-y-2">
      {documents.isError ? (
        <ErrorState
          message={
            documents.error instanceof ApiError
              ? documents.error.message
              : t("knowledgeBases.error.documents")
          }
          onRetry={() => void documents.refetch()}
        />
      ) : documents.isPending ? (
        <KnowledgeBaseDocumentsSkeleton />
      ) : documents.data.items.length > 0 ? (
        <ul className="bounded-scroll-area divide-y divide-border rounded-md border border-border">
          {documents.data.items.map((document) => (
            <KnowledgeBaseDocumentRow
              key={document.id}
              document={document}
              onRemove={() => void handleRemove(document)}
              removing={remove.isPending && remove.variables?.documentId === document.id}
            />
          ))}
        </ul>
      ) : (
        <EmptyState
          title={t("knowledgeBases.documents.empty.title")}
          hint={t("knowledgeBases.documents.empty.hint")}
        />
      )}
    </div>
  );
}

/** 所属文書 1 行。展開でチャンク構成(chunk_set)を遅延取得して比較表示する。 */
function KnowledgeBaseDocumentRow({
  document,
  onRemove,
  removing,
}: {
  document: DocumentSummary;
  onRemove: () => void;
  removing: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const chunkSets = useDocumentChunkSets(document.id, expanded);
  const reingest = useEnqueueDocumentIngestionJob();
  const needsReingest = documentChunkSetsNeedReingest(chunkSets.data ?? []);
  const reingestReason = documentChunkSetReingestReason(chunkSets.data ?? []);

  const handleReingest = () => {
    reingest.mutate(
      { id: document.id, force: true },
      {
        onSuccess: () => toast.success(t("knowledgeBases.variant.reingestQueued")),
        onError: (error) =>
          toast.error(
            error instanceof ApiError
              ? error.message
              : t("knowledgeBases.variant.reingestFailed")
          ),
      }
    );
  };

  return (
    <li className="px-3 py-2">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          aria-label={t(
            expanded ? "knowledgeBases.variant.collapse" : "knowledgeBases.variant.expand"
          )}
          className="shrink-0 rounded p-0.5 text-muted transition-colors hover:bg-border/60 hover:text-foreground"
        >
          <ChevronRight
            size={16}
            className={cn("transition-transform", expanded && "rotate-90")}
            aria-hidden
          />
        </button>
        <Link
          to={`${APP_ROUTES.documents}/${document.id}`}
          className="min-w-0 flex-1 truncate text-sm font-medium text-primary hover:underline"
          title={document.file_name}
        >
          {document.file_name}
        </Link>
        <Button
          variant="ghost"
          size="sm"
          onClick={onRemove}
          loading={removing}
          className="shrink-0 whitespace-nowrap"
        >
          <Trash2 size={14} aria-hidden />
          {t("knowledgeBases.actions.remove")}
        </Button>
      </div>
      {expanded ? (
        <div className="mt-2 pl-6">
          {chunkSets.isPending ? (
            <p className="text-xs text-muted">{t("knowledgeBases.variant.loading")}</p>
          ) : chunkSets.isError ? (
            <p className="text-xs text-danger">{t("knowledgeBases.variant.error")}</p>
          ) : chunkSets.data && chunkSets.data.length > 0 ? (
            <div className="space-y-2">
              {needsReingest ? (
                <Banner severity="warning" title={t("knowledgeBases.variant.reingestTitle")}>
                  <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                    <p className="min-w-0 text-sm">
                      {reingestReason ?? t("knowledgeBases.variant.reingestDescription")}
                    </p>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={handleReingest}
                      loading={reingest.isPending}
                      className="min-h-9 shrink-0 self-start sm:self-center"
                    >
                      {!reingest.isPending ? <RefreshCw size={14} aria-hidden /> : null}
                      {t("knowledgeBases.variant.reingestAction")}
                    </Button>
                  </div>
                </Banner>
              ) : null}
              <ul className="space-y-1.5" aria-label={t("knowledgeBases.variant.title")}>
                {chunkSets.data.map((chunkSet) => (
                  <li
                    key={chunkSet.chunk_set_id}
                    className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-background px-2.5 py-1.5 text-xs"
                  >
                    <span className="font-mono text-muted" title={chunkSet.chunk_set_id}>
                      {chunkSet.chunk_set_id.slice(0, 10)}
                    </span>
                    {chunkSet.extraction_recipe_id ? (
                      <span
                        className="font-mono text-muted"
                        title={chunkSet.extraction_recipe_id}
                      >
                        {t("knowledgeBases.variant.extractionRecipe")}:{" "}
                        {chunkSet.extraction_recipe_id.slice(0, 10)}
                      </span>
                    ) : null}
                    <span className="rounded-sm bg-muted/10 px-1.5 py-0.5 text-muted">
                      {chunkSet.status}
                    </span>
                    <LayerStatusPill
                      label={t("knowledgeBases.variant.extractionStatus")}
                      status={{
                        layer_id: chunkSet.extraction_recipe_id,
                        requested: Boolean(chunkSet.extraction_recipe_id),
                        status: chunkSet.extraction_status,
                        reason: chunkSet.extraction_reason,
                      }}
                    />
                    <span className="tnum text-muted">
                      {t("knowledgeBases.variant.chunkCount", { count: chunkSet.chunk_count })}
                    </span>
                    <span className="tnum text-muted">
                      {t("knowledgeBases.variant.servingCount", {
                        count: chunkSet.serving_knowledge_base_ids.length,
                      })}
                    </span>
                    <DerivedLayerStatuses statuses={chunkSet.layer_statuses} />
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-xs text-muted">{t("knowledgeBases.variant.empty")}</p>
          )}
          {!chunkSets.isPending && !chunkSets.isError ? (
            <p className="mt-2 text-xs text-muted">
              {t("knowledgeBases.variant.addHint")}{" "}
              <Link
                to={APP_ROUTES.knowledgeBases}
                className="inline-flex items-center gap-1 font-medium text-primary hover:underline"
              >
                <FilePlus2 size={12} aria-hidden />
                {t("knowledgeBases.variant.addAction")}
              </Link>
            </p>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

const DERIVED_LAYER_KEYS: Array<{
  key: keyof DocumentChunkSetLayerStatuses;
  labelKey: I18nKey;
}> = [
  { key: "metadata", labelKey: "knowledgeBases.variant.layer.metadata" },
  { key: "graph", labelKey: "knowledgeBases.variant.layer.graph" },
  { key: "navigation", labelKey: "knowledgeBases.variant.layer.navigation" },
];

const LAYER_STATUS_LABEL_KEYS: Record<DocumentMaterializationLayerStatus["status"], I18nKey> = {
  not_requested: "knowledgeBases.variant.layerStatus.not_requested",
  planned_only: "knowledgeBases.variant.layerStatus.planned_only",
  materialized: "knowledgeBases.variant.layerStatus.materialized",
  needs_reingest: "knowledgeBases.variant.layerStatus.needs_reingest",
  error: "knowledgeBases.variant.layerStatus.error",
};

const DEFAULT_LAYER_STATUS: DocumentMaterializationLayerStatus = {
  layer_id: null,
  requested: false,
  status: "not_requested",
  reason: null,
};

function DerivedLayerStatuses({
  statuses,
}: {
  statuses?: Partial<DocumentChunkSetLayerStatuses> | null;
}) {
  return (
    <div
      className="flex basis-full flex-wrap items-center gap-1.5 pt-0.5"
      aria-label={t("knowledgeBases.variant.layers")}
    >
      {DERIVED_LAYER_KEYS.map(({ key, labelKey }) => (
        <LayerStatusPill
          key={key}
          label={t(labelKey)}
          status={statuses?.[key] ?? DEFAULT_LAYER_STATUS}
        />
      ))}
    </div>
  );
}

function LayerStatusPill({
  label,
  status,
}: {
  label: string;
  status: DocumentMaterializationLayerStatus;
}) {
  return (
    <span
      className={cn(
        "inline-flex min-h-6 items-center gap-1 rounded border px-1.5 text-[11px] leading-none",
        status.requested
          ? "border-primary/30 bg-primary/5 text-primary"
          : "border-border bg-muted/5 text-muted"
      )}
      title={status.reason ?? undefined}
    >
      <span>{label}</span>
      <span className="font-medium">{t(LAYER_STATUS_LABEL_KEYS[status.status])}</span>
    </span>
  );
}

export function documentChunkSetsNeedReingest(chunkSets: DocumentChunkSet[]) {
  return chunkSets.some(
    (chunkSet) =>
      chunkSet.extraction_status === "needs_reingest" ||
      Object.values(chunkSet.layer_statuses ?? {}).some(
        (status) => status.status === "needs_reingest"
      )
  );
}

export function documentChunkSetReingestReason(chunkSets: DocumentChunkSet[]) {
  for (const chunkSet of chunkSets) {
    if (chunkSet.extraction_status === "needs_reingest" && chunkSet.extraction_reason) {
      return chunkSet.extraction_reason;
    }
    for (const status of Object.values(chunkSet.layer_statuses ?? {})) {
      if (status.status === "needs_reingest" && status.reason) return status.reason;
    }
  }
  return null;
}

function documentHasKnowledgeBase(document: DocumentSummary, knowledgeBaseId: string) {
  return document.knowledge_bases.some((knowledgeBase) => knowledgeBase.id === knowledgeBaseId);
}

function KnowledgeBaseDocumentsSkeleton() {
  return (
    <div className="space-y-2" role="status" aria-label={t("knowledgeBases.documents.loading")}>
      <div className="h-9 rounded-md bg-background" />
      <div className="h-9 rounded-md bg-background" />
      <div className="h-9 rounded-md bg-background" />
    </div>
  );
}
