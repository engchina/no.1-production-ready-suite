"use client";

import { FilePlus2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { EmptyState, ErrorState } from "@/components/StateViews";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { SelectField, type SelectFieldOption } from "@/components/ui/select-field";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ApiError, type DocumentSummary, type KnowledgeBaseDetail } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { t } from "@/lib/i18n";
import {
  useAssignDocumentsToKnowledgeBase,
  useDocuments,
  useKnowledgeBase,
  useRemoveDocumentFromKnowledgeBase,
} from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
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
    <div className="space-y-2 border-t border-border pt-4">
      <div className="flex items-end gap-2">
        <SelectField
          id="knowledge-base-add-document"
          label={t("knowledgeBases.assignment.title")}
          value={documentId}
          options={selectOptions}
          onValueChange={setDocumentId}
          placeholder={t("knowledgeBases.assignment.noOptions")}
          className="min-w-0 flex-1"
          buttonClassName="h-9"
        />
        <Button
          type="button"
          variant="secondary"
          size="md"
          onClick={handleAssign}
          loading={assign.isPending}
          disabled={!documentId}
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
    <div className="space-y-2 border-t border-border pt-4">
      <h2 className="text-sm font-medium text-foreground">{t("knowledgeBases.documents.title")}</h2>
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
            <li key={document.id} className="flex items-center justify-between gap-3 px-3 py-2">
              <Link
                to={`${APP_ROUTES.documents}/${document.id}`}
                className="min-w-0 truncate text-sm font-medium text-primary hover:underline"
                title={document.file_name}
              >
                {document.file_name}
              </Link>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void handleRemove(document)}
                loading={remove.isPending && remove.variables?.documentId === document.id}
              >
                <Trash2 size={14} aria-hidden />
                {t("knowledgeBases.actions.remove")}
              </Button>
            </li>
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
