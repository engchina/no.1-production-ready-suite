"use client";

import { FileSearch, FileText, RotateCcw, Save, Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DocumentPreview } from "./DocumentPreview";
import { DocumentExtraction } from "./DocumentExtraction";
import { KnowledgeBaseScopePicker } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import { FlowStepper } from "@/components/upload/FlowStepper";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { ErrorState } from "@/components/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, type KnowledgeBaseRef, type SourceProfile } from "@/lib/api";
import {
  useDocument,
  useDocumentKnowledgeBases,
  useEnqueueDocumentIngestionJob,
  useIngestionJob,
  useReplaceDocumentKnowledgeBases,
} from "@/lib/queries";
import { t } from "@/lib/i18n";
import { formatBytes, formatDateTime } from "@/lib/format";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/** 文書プレビュー作業領域：原本プレビュー｜抽出本文＋取込アクション。 */
export function DocumentWorkspace({
  documentId,
  watchProcessing = false,
  initialSourceProfile = null,
}: {
  documentId: string;
  watchProcessing?: boolean;
  initialSourceProfile?: SourceProfile | null;
}) {
  const query = useDocument(documentId);
  const enqueueIngestion = useEnqueueDocumentIngestionJob();
  const queuedJob = useIngestionJob(enqueueIngestion.data?.id ?? null);
  const [localWatchProcessing, setLocalWatchProcessing] = useState(false);
  const status = query.data?.status;

  useEffect(() => {
    const shouldPoll =
      status === "INGESTING" ||
      ((watchProcessing || localWatchProcessing) &&
        status !== "INDEXED" &&
        status !== "ERROR");
    if (!shouldPoll) return;
    const timer = window.setInterval(() => {
      void query.refetch();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [localWatchProcessing, query, status, watchProcessing]);

  useEffect(() => {
    if (status === "INDEXED" || status === "ERROR") {
      setLocalWatchProcessing(false);
    }
  }, [status]);

  if (query.isPending) return <Skeleton className="h-80 w-full rounded-lg" />;
  if (query.isError) {
    return (
      <ErrorState
        message={errorMessage(query.error, t("workspace.notFound"))}
        onRetry={() => void query.refetch()}
      />
    );
  }

  const doc = query.data;
  const sourceProfile = doc.source_profile ?? initialSourceProfile;

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <FileText size={18} className="text-primary" aria-hidden />
            <span className="truncate" title={doc.file_name}>
              {doc.file_name}
            </span>
          </CardTitle>
          <StatusBadge status={doc.status} />
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {doc.duplicate_of_document_id ? (
          <Banner severity="warning">{t("upload.duplicate")}</Banner>
        ) : null}

        <FlowStepper status={doc.status} />
        {watchProcessing && doc.status !== "INDEXED" && doc.status !== "ERROR" ? (
          <Banner severity="info">{t("upload.autoIngest.running")}</Banner>
        ) : null}

        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-xs text-muted">{t("flow.size")}</dt>
            <dd className="tnum mt-0.5 font-medium text-foreground">
              {formatBytes(doc.file_size_bytes)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">{t("flow.uploadedAt")}</dt>
            <dd className="tnum mt-0.5 font-medium text-foreground">
              {formatDateTime(doc.uploaded_at)}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted">{t("flow.indexedAt")}</dt>
            <dd className="tnum mt-0.5 font-medium text-foreground">
              {formatDateTime(doc.indexed_at)}
            </dd>
          </div>
        </dl>

        {sourceProfile ? <SourceProfilePanel profile={sourceProfile} /> : null}

        <DocumentKnowledgeBaseEditor
          documentId={documentId}
          initialKnowledgeBases={doc.knowledge_bases}
        />

        {doc.error_message ? <Banner severity="danger">{doc.error_message}</Banner> : null}
        {enqueueIngestion.isError ? (
          <Banner severity="danger">
            {errorMessage(enqueueIngestion.error, t("flow.ingestFailed"))}
          </Banner>
        ) : null}
        {enqueueIngestion.data ? (
          <FormStatus
            tone={enqueueIngestion.data.status === "SKIPPED" ? "warning" : "success"}
            message={
              enqueueIngestion.data.status === "SKIPPED"
                ? t("flow.ingestionSkipped")
                : t("flow.ingestionQueued")
            }
          />
        ) : null}
        {queuedJob.data?.status === "FAILED" ? (
          <Banner severity="danger">
            {queuedJob.data.error_message ?? t("flow.ingestFailed")}
          </Banner>
        ) : null}

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">{t("flow.preview")}</h3>
            <DocumentPreview documentId={documentId} fileName={doc.file_name} />
          </section>
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">
              {t("flow.extraction.title")}
            </h3>
            <DocumentExtraction extraction={doc.extraction} />
          </section>
        </div>

        {doc.status === "INDEXED" ? (
          <Banner severity="success">{t("flow.indexed")}</Banner>
        ) : null}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
          {(doc.status === "UPLOADED" || doc.status === "ERROR") && (
            <Button
              onClick={() =>
                enqueueIngestion.mutate(
                  { id: documentId },
                  {
                    onSuccess: (job) => {
                      setLocalWatchProcessing(job.status === "QUEUED" || job.status === "RUNNING");
                    },
                  }
                )
              }
              loading={enqueueIngestion.isPending}
            >
              {!enqueueIngestion.isPending ? <Send size={15} aria-hidden /> : null}
              {enqueueIngestion.isPending ? t("action.queueing") : t("action.enqueueIngestion")}
            </Button>
          )}
          {doc.status === "INDEXED" && (
            <Button
              variant="secondary"
              onClick={() =>
                enqueueIngestion.mutate(
                  { id: documentId, force: true },
                  {
                    onSuccess: (job) => {
                      setLocalWatchProcessing(job.status === "QUEUED" || job.status === "RUNNING");
                    },
                  }
                )
              }
              loading={enqueueIngestion.isPending}
            >
              {!enqueueIngestion.isPending ? <RotateCcw size={15} aria-hidden /> : null}
              {enqueueIngestion.isPending ? t("action.queueing") : t("action.requeueIngestion")}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function SourceProfilePanel({ profile }: { profile: SourceProfile }) {
  const warnings = profile.quality_warnings ?? [];
  return (
    <section className="rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <FileSearch size={16} className="text-primary" aria-hidden />
          {t("sourceProfile.title")}
        </h3>
        <span className="rounded-full border border-border bg-card px-2 py-0.5 text-xs font-medium text-foreground">
          {t(sourceModalityKey(profile.modality))}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.parser")}</dt>
          <dd className="mt-0.5 font-medium text-foreground">
            {t(parserProfileKey(profile.parser_profile))}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.contentType")}</dt>
          <dd className="mt-0.5 break-all font-medium text-foreground">
            {profile.content_type}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.extension")}</dt>
          <dd className="mt-0.5 font-medium text-foreground">
            {profile.extension ?? "—"}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.hash")}</dt>
          <dd className="tnum mt-0.5 font-medium text-foreground">
            {profile.content_sha256.slice(0, 12)}
          </dd>
        </div>
      </dl>
      {warnings.length > 0 ? (
        <ul className="mt-3 space-y-1 text-xs text-warning">
          {warnings.map((warning) => (
            <li key={warning}>{t(sourceWarningKey(warning))}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-xs text-muted">{t("sourceProfile.ready")}</p>
      )}
    </section>
  );
}

function sourceModalityKey(modality: SourceProfile["modality"]) {
  return `sourceProfile.modality.${modality}` as const;
}

function parserProfileKey(profile: string) {
  switch (profile) {
    case "enterprise_ai_pdf_layout":
      return "sourceProfile.parser.pdf";
    case "enterprise_ai_image_ocr":
      return "sourceProfile.parser.image";
    case "enterprise_ai_text_structure":
      return "sourceProfile.parser.text";
    case "enterprise_ai_office_structure":
      return "sourceProfile.parser.office";
    default:
      return "sourceProfile.parser.generic";
  }
}

function sourceWarningKey(warning: string) {
  switch (warning) {
    case "duplicate_content":
      return "sourceProfile.warning.duplicate";
    case "content_type_missing":
      return "sourceProfile.warning.contentTypeMissing";
    case "content_type_extension_mismatch":
      return "sourceProfile.warning.contentTypeMismatch";
    case "large_file":
      return "sourceProfile.warning.largeFile";
    case "unknown_modality":
      return "sourceProfile.warning.unknown";
    default:
      return "sourceProfile.warning.generic";
  }
}

function DocumentKnowledgeBaseEditor({
  documentId,
  initialKnowledgeBases,
}: {
  documentId: string;
  initialKnowledgeBases: KnowledgeBaseRef[];
}) {
  const membership = useDocumentKnowledgeBases(documentId);
  const replace = useReplaceDocumentKnowledgeBases();
  const initialIds = useMemo(
    () => initialKnowledgeBases.map((knowledgeBase) => knowledgeBase.id),
    [initialKnowledgeBases]
  );
  const initialIdsKey = useMemo(() => idSetKey(initialIds), [initialIds]);
  const [selectedIds, setSelectedIds] = useState(initialIds);
  const [savedIds, setSavedIds] = useState(initialIds);

  useEffect(() => {
    if (membership.data) return;
    setSelectedIds(initialIds);
    setSavedIds(initialIds);
  }, [initialIdsKey, initialIds, membership.data]);

  useEffect(() => {
    if (!membership.data) return;
    const ids = membership.data.map((knowledgeBase) => knowledgeBase.id);
    setSelectedIds(ids);
    setSavedIds(ids);
  }, [membership.data]);

  const isDirty = !isSameIdSet(selectedIds, savedIds);
  const canSave = selectedIds.length > 0 && isDirty && !membership.isPending;

  const onSave = () => {
    if (!canSave) return;
    replace.mutate(
      {
        id: documentId,
        payload: { knowledge_base_ids: selectedIds },
      },
      {
        onSuccess: (refs) => {
          const ids = refs.map((knowledgeBase) => knowledgeBase.id);
          setSelectedIds(ids);
          setSavedIds(ids);
        },
      }
    );
  };

  return (
    <section className="space-y-3 border-t border-border pt-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">
          {t("documents.knowledgeBases.title")}
        </h3>
        <p className="mt-1 text-xs text-muted">{t("documents.knowledgeBases.description")}</p>
      </div>

      {membership.isError ? (
        <Banner severity="warning" title={t("documents.knowledgeBases.loadWarning")}>
          <p>{errorMessage(membership.error, t("documents.knowledgeBases.loadWarningHint"))}</p>
        </Banner>
      ) : null}

      <KnowledgeBaseScopePicker
        selectedIds={selectedIds}
        onChange={setSelectedIds}
        disabled={replace.isPending || membership.isPending}
        label={t("documents.knowledgeBases.pickerLabel")}
        helper={t("documents.knowledgeBases.helper")}
        emptySelectionText={t("documents.knowledgeBases.noneSelected")}
      />

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          size="md"
          onClick={onSave}
          loading={replace.isPending}
          disabled={!canSave}
        >
          <Save size={15} aria-hidden />
          {t("documents.knowledgeBases.save")}
        </Button>
        {selectedIds.length === 0 ? (
          <FormStatus tone="warning" message={t("documents.knowledgeBases.required")} />
        ) : null}
        {replace.isSuccess && !isDirty && selectedIds.length > 0 ? (
          <FormStatus tone="success" message={t("documents.knowledgeBases.saved")} />
        ) : null}
        {replace.isError ? (
          <FormStatus
            tone="danger"
            message={errorMessage(replace.error, t("documents.knowledgeBases.saveError"))}
          />
        ) : null}
      </div>
    </section>
  );
}

function idSetKey(ids: string[]) {
  return [...ids].sort().join("\u0000");
}

function isSameIdSet(left: string[], right: string[]) {
  if (left.length !== right.length) return false;
  return idSetKey(left) === idSetKey(right);
}
