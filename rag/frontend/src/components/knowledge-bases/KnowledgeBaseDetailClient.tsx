"use client";

import { ChevronRight, FilePlus2, Files, Layers, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  ApiError,
  type DocumentChunkSet,
  type DocumentSummary,
  type KnowledgeBaseDetail,
} from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useAssignDocumentsToKnowledgeBase,
  useDocumentChunkSets,
  useDocuments,
  useKnowledgeBase,
  useRemoveDocumentFromKnowledgeBase,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { KnowledgeBaseAdapterConfigPanel } from "./KnowledgeBaseAdapterConfigPanel";
import { KnowledgeBaseStatusPill } from "./KnowledgeBaseStatusPill";

/** ナレッジベース詳細ページ。概要・所属文書・アダプター設定(パイプライン地図 + フォーム)を全幅で扱う。 */
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

/** 抽出(parser×前処理)ごとに chunk_set をまとめた 2 階層の variant グループ。 */
export interface ChunkSetExtractionGroup {
  extractionId: string | null;
  parser: string | null;
  preprocess: string | null;
  chunkSets: DocumentChunkSet[];
}

/**
 * chunk_set 群を親抽出(extraction_id)でグルーピングする。
 *
 * parser×前処理 が同じ chunk_set は 1 抽出を共有する(extract 1 回・chunking 違いで分裂)。
 * extraction_id を持たない旧 chunk_set は各々単独グループにして表示を欠落させない。
 * backend の created_at 昇順(挿入順)を保つ。
 */
export function groupChunkSetsByExtraction(
  chunkSets: DocumentChunkSet[]
): ChunkSetExtractionGroup[] {
  const groups = new Map<string, ChunkSetExtractionGroup>();
  for (const chunkSet of chunkSets) {
    const key = chunkSet.extraction_id ?? `__cs__${chunkSet.chunk_set_id}`;
    let group = groups.get(key);
    if (!group) {
      group = {
        extractionId: chunkSet.extraction_id,
        parser: chunkSet.parser,
        preprocess: chunkSet.preprocess,
        chunkSets: [],
      };
      groups.set(key, group);
    }
    group.chunkSets.push(chunkSet);
  }
  return [...groups.values()];
}

/** 所属文書 1 行。展開で variant(extraction▸chunk_set の 2 階層)を遅延取得して比較表示する。 */
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
            <ul className="space-y-2" aria-label={t("knowledgeBases.variant.extractionTitle")}>
              {groupChunkSetsByExtraction(chunkSets.data).map((group) => (
                <li
                  key={group.extractionId ?? group.chunkSets[0].chunk_set_id}
                  className="overflow-hidden rounded-md border border-border bg-background"
                >
                  {/* 上位: 抽出(parser×前処理)= extract 1 回の単位 */}
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/60 bg-muted/5 px-2.5 py-1.5 text-xs">
                    <Layers size={13} className="shrink-0 text-muted" aria-hidden />
                    <span className="font-medium text-foreground">
                      {t("knowledgeBases.variant.parser")}:{" "}
                      {group.parser ?? t("knowledgeBases.variant.unknownRecipe")}
                    </span>
                    {group.preprocess ? (
                      <span className="text-muted">
                        {t("knowledgeBases.variant.preprocess")}: {group.preprocess}
                      </span>
                    ) : null}
                    {group.extractionId ? (
                      <span className="font-mono text-muted" title={group.extractionId}>
                        {group.extractionId.slice(0, 10)}
                      </span>
                    ) : null}
                    <span className="tnum ml-auto text-muted">
                      {t("knowledgeBases.variant.chunkSetCount", {
                        count: group.chunkSets.length,
                      })}
                    </span>
                  </div>
                  {/* 下位: chunk_set(chunking 違い)= 抽出を共有する変種 */}
                  <ul className="space-y-1 p-1.5">
                    {group.chunkSets.map((chunkSet) => (
                      <li
                        key={chunkSet.chunk_set_id}
                        className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-sm bg-muted/5 px-2 py-1 text-xs"
                      >
                        <span className="font-mono text-muted" title={chunkSet.chunk_set_id}>
                          {chunkSet.chunk_set_id.slice(0, 10)}
                        </span>
                        <span className="rounded-sm bg-muted/10 px-1.5 py-0.5 text-muted">
                          {chunkSet.status}
                        </span>
                        <span className="tnum text-muted">
                          {t("knowledgeBases.variant.chunkCount", { count: chunkSet.chunk_count })}
                        </span>
                        <span className="tnum text-muted">
                          {t("knowledgeBases.variant.servingCount", {
                            count: chunkSet.serving_knowledge_base_ids.length,
                          })}
                        </span>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted">{t("knowledgeBases.variant.empty")}</p>
          )}
        </div>
      ) : null}
    </li>
  );
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
