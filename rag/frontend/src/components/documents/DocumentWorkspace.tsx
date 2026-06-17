"use client";

import {
  FileSearch,
  FileText,
  ListTree,
  LocateFixed,
  RotateCcw,
  Route,
  Save,
  Send,
  TriangleAlert,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

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
import {
  ApiError,
  type DocumentElement,
  type DocumentChunkView,
  type IngestionSegment,
  type KnowledgeBaseRef,
  type SourceProfile,
} from "@/lib/api";
import { parseStructuredExtraction } from "@/lib/extraction";
import {
  useDocument,
  useDocumentChunks,
  useDocumentIngestionSegments,
  useDocumentKnowledgeBases,
  useEnqueueDocumentIngestionJob,
  useIngestionJob,
  useReplaceDocumentKnowledgeBases,
} from "@/lib/queries";
import { t } from "@/lib/i18n";
import { formatBytes, formatDateTime } from "@/lib/format";
import { scrollFocusedControlIntoView } from "@/lib/focus-scroll";
import { bboxCoordinateModeFromMetadata } from "@/lib/bbox";
import {
  parserProfileKey,
  sourceModalityKey,
  sourcePreviewKey,
  sourceWarningKey,
  unsupportedReasonLabel,
} from "@/lib/source-profile-labels";

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function workspaceElementKey(element: DocumentElement): string {
  return element.element_id || `el-${String(element.order).padStart(4, "0")}`;
}

type WorkspaceFocusRequest = {
  key: string;
  target: "chunk" | "element";
};

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
  const chunksQuery = useDocumentChunks(documentId);
  const segmentsQuery = useDocumentIngestionSegments(documentId);
  const [searchParams] = useSearchParams();
  const enqueueIngestion = useEnqueueDocumentIngestionJob();
  const queuedJob = useIngestionJob(enqueueIngestion.data?.id ?? null);
  const [localWatchProcessing, setLocalWatchProcessing] = useState(false);
  const [selectedElementId, setSelectedElementId] = useState<string | null>(null);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [previewFocusSource, setPreviewFocusSource] = useState<"chunk" | "element">("chunk");
  const [focusRequest, setFocusRequest] = useState<WorkspaceFocusRequest | null>(null);
  const appliedFocusRequestRef = useRef<string | null>(null);
  const requestedChunkId = searchParams.get("chunk_id");
  const requestedElementId = searchParams.get("element_id");
  const status = query.data?.status;
  const parsedExtraction = useMemo(
    () => parseStructuredExtraction(query.data?.extraction ?? {}),
    [query.data?.extraction]
  );
  const selectedChunk = useMemo(
    () => chunksQuery.data?.find((chunk) => chunk.chunk_id === selectedChunkId) ?? null,
    [chunksQuery.data, selectedChunkId]
  );
  const selectedElement = useMemo(
    () =>
      parsedExtraction.elements.find(
        (element) => workspaceElementKey(element) === selectedElementId
      ) ??
      null,
    [parsedExtraction.elements, selectedElementId]
  );
  const focusPage =
    previewFocusSource === "element"
      ? selectedElement?.page_number ?? selectedChunk?.page_start ?? null
      : selectedChunk?.page_start ?? selectedElement?.page_number ?? null;
  const focusBbox =
    previewFocusSource === "element"
      ? selectedElement?.bbox ?? selectedChunk?.bbox ?? null
      : selectedChunk?.bbox ?? selectedElement?.bbox ?? null;
  const focusBboxMode =
    previewFocusSource === "element"
      ? bboxCoordinateModeFromMetadata(selectedElement?.metadata) ??
        bboxCoordinateModeFromMetadata(selectedChunk?.metadata)
      : bboxCoordinateModeFromMetadata(selectedChunk?.metadata) ??
        bboxCoordinateModeFromMetadata(selectedElement?.metadata);
  const focusPageSize = useMemo(() => {
    if (!focusPage) return null;
    const page = parsedExtraction.pages.find((item) => item.page_number === focusPage);
    if (!page?.width || !page?.height) return null;
    return { width: page.width, height: page.height };
  }, [focusPage, parsedExtraction.pages]);
  function selectElement(elementId: string) {
    const linkedChunk = chunksQuery.data?.find((chunk) => chunk.element_ids.includes(elementId));
    setSelectedElementId(elementId);
    if (chunksQuery.data) {
      setSelectedChunkId(linkedChunk?.chunk_id ?? null);
    }
    setPreviewFocusSource("element");
  }

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

  useEffect(() => {
    const chunks = chunksQuery.data ?? [];
    if (!chunks.length && !requestedElementId) return;
    const requestedFocusKey = requestedChunkId
      ? `chunk:${requestedChunkId}\u0000${requestedElementId ?? ""}`
      : requestedElementId
        ? `element:${requestedElementId}`
        : null;
    if (requestedFocusKey && appliedFocusRequestRef.current !== requestedFocusKey) {
      if (requestedChunkId) {
        const requestedChunk = chunks.find((chunk) => chunk.chunk_id === requestedChunkId);
        if (requestedChunk) {
          setSelectedChunkId(requestedChunk.chunk_id);
          setSelectedElementId(
            requestedElementId && requestedChunk.element_ids.includes(requestedElementId)
              ? requestedElementId
              : requestedChunk.element_ids[0] ?? null
          );
          setPreviewFocusSource("chunk");
          setFocusRequest({ key: requestedFocusKey, target: "chunk" });
          appliedFocusRequestRef.current = requestedFocusKey;
          return;
        }
      } else if (requestedElementId) {
        const requestedElement = parsedExtraction.elements.find(
          (element) => workspaceElementKey(element) === requestedElementId
        );
        const linkedChunk = chunks.find((chunk) =>
          chunk.element_ids.includes(requestedElementId)
        );
        if (requestedElement || linkedChunk) {
          setSelectedChunkId(linkedChunk?.chunk_id ?? null);
          setSelectedElementId(requestedElementId);
          setPreviewFocusSource("element");
          setFocusRequest({ key: requestedFocusKey, target: "element" });
          appliedFocusRequestRef.current = requestedFocusKey;
          return;
        }
      }
    }
    if (requestedElementId && selectedElementId === requestedElementId) {
      const linkedChunk = chunks.find((chunk) => chunk.element_ids.includes(requestedElementId));
      if (!selectedChunkId && linkedChunk) {
        setSelectedChunkId(linkedChunk.chunk_id);
      }
      return;
    }
    if (selectedChunkId && chunks.some((chunk) => chunk.chunk_id === selectedChunkId)) {
      return;
    }
    const firstChunk = chunks[0];
    if (!firstChunk) return;
    setSelectedChunkId(firstChunk.chunk_id);
    setSelectedElementId(firstChunk.element_ids[0] ?? null);
  }, [
    chunksQuery.data,
    parsedExtraction.elements,
    requestedChunkId,
    requestedElementId,
    selectedChunkId,
    selectedElementId,
  ]);

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

        <IngestionSegmentsPanel
          segments={segmentsQuery.data ?? []}
          loading={segmentsQuery.isPending}
          error={segmentsQuery.isError}
          retrying={enqueueIngestion.isPending}
          onRetryFailedSegments={() =>
            enqueueIngestion.mutate(
              { id: documentId, force: doc.status === "INDEXED" },
              {
                onSuccess: (job) => {
                  setLocalWatchProcessing(job.status === "QUEUED" || job.status === "RUNNING");
                },
              }
            )
          }
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

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">{t("flow.preview")}</h3>
            <DocumentPreview
              documentId={documentId}
              fileName={doc.file_name}
              sourceProfile={sourceProfile}
              focusPage={focusPage}
              focusBbox={focusBbox}
              focusBboxMode={focusBboxMode}
              focusPageSize={focusPageSize}
            />
          </section>
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">
              {t("flow.extraction.title")}
            </h3>
            <DocumentExtraction
              extraction={doc.extraction}
              selectedElementId={selectedElementId}
              focusRequestKey={focusRequest?.key ?? null}
              focusSelectedElement={focusRequest?.target === "element"}
              onElementSelect={selectElement}
            />
          </section>
          <section>
            <h3 className="mb-2 text-sm font-semibold text-foreground">
              {t("flow.chunks.title")}
            </h3>
            <DocumentChunksPanel
              chunks={chunksQuery.data ?? []}
              loading={chunksQuery.isPending}
              error={chunksQuery.isError}
              selectedChunkId={selectedChunkId}
              focusRequestKey={focusRequest?.target === "chunk" ? focusRequest.key : null}
              onSelect={(chunk) => {
                setSelectedChunkId(chunk.chunk_id);
                setSelectedElementId(chunk.element_ids[0] ?? null);
                setPreviewFocusSource("chunk");
              }}
            />
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

function IngestionSegmentsPanel({
  segments,
  loading,
  error,
  retrying,
  onRetryFailedSegments,
}: {
  segments: IngestionSegment[];
  loading: boolean;
  error: boolean;
  retrying: boolean;
  onRetryFailedSegments: () => void;
}) {
  if (loading) return <Skeleton className="h-24 w-full rounded-md" />;
  if (error) {
    return (
      <Banner severity="warning" title={t("flow.segments.loadError")}>
        {t("flow.segments.loadErrorHint")}
      </Banner>
    );
  }
  if (!segments.length) return null;

  const hasFailedSegments = segments.some((segment) => segment.status === "FAILED");

  return (
    <section className="rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Route size={16} className="text-primary" aria-hidden />
          {t("flow.segments.title")}
        </h3>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted">
            {t("flow.segments.count", { count: segments.length })}
          </span>
          {hasFailedSegments ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              loading={retrying}
              onClick={onRetryFailedSegments}
            >
              {!retrying ? <RotateCcw size={14} aria-hidden /> : null}
              {t("flow.segments.retryFailed")}
            </Button>
          ) : null}
        </div>
      </div>
      <ol className="mt-3 grid grid-cols-1 gap-2 lg:grid-cols-2">
        {segments.map((segment) => (
          <li
            key={segment.segment_id}
            className="rounded-md border border-border bg-card p-3 text-sm"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className={segmentStatusClass(segment.status)}>
                {segmentStatusLabel(segment.status)}
              </span>
              <span className="tnum text-xs text-muted">
                {segment.page_start
                  ? t("flow.segments.pageRange", {
                      start: segment.page_start,
                      end: segment.page_end ?? segment.page_start,
                    })
                  : t("flow.segments.source")}
              </span>
              {segment.error_code ? (
                <span
                  className="inline-flex items-center gap-1 rounded-full bg-warning-bg px-2 py-0.5 text-xs text-warning"
                  title={t("flow.segments.errorCode", { code: segment.error_code })}
                >
                  <TriangleAlert size={12} aria-hidden />
                  {segment.error_code}
                </span>
              ) : null}
            </div>
            <p className="mt-2 break-all text-xs text-muted">
              {segment.parser_backend} / {segment.parser_profile}
            </p>
            {segment.status === "FAILED" && segment.error_message ? (
              <div className="mt-2 space-y-1 rounded-md border border-danger/20 bg-danger-bg px-2.5 py-2 text-xs text-danger">
                <p className="font-medium text-danger">{t("flow.segments.errorReason")}</p>
                <p className="break-words text-danger/90">{segment.error_message}</p>
              </div>
            ) : null}
            {segment.status === "FAILED" ? (
              <p className="mt-2 text-xs leading-relaxed text-muted">
                {t("flow.segments.errorRecovery")}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function DocumentChunksPanel({
  chunks,
  loading,
  error,
  selectedChunkId,
  focusRequestKey,
  onSelect,
}: {
  chunks: DocumentChunkView[];
  loading: boolean;
  error: boolean;
  selectedChunkId: string | null;
  focusRequestKey?: string | null;
  onSelect: (chunk: DocumentChunkView) => void;
}) {
  const selectedChunkRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!focusRequestKey || !selectedChunkId || !selectedChunkRef.current) return;
    scrollFocusedControlIntoView(selectedChunkRef.current, { focus: true });
  }, [focusRequestKey, selectedChunkId]);

  if (loading) return <Skeleton className="h-80 w-full rounded-md" />;
  if (error) {
    return (
      <Banner severity="warning" title={t("flow.chunks.loadError")}>
        {t("flow.chunks.loadErrorHint")}
      </Banner>
    );
  }
  if (!chunks.length) {
    return (
      <div className="rounded-md border border-border bg-background p-4 text-sm text-muted">
        {t("flow.chunks.empty")}
      </div>
    );
  }

  return (
    <ol className="max-h-[680px] space-y-3 overflow-auto rounded-lg border border-border bg-background p-3">
      {chunks.map((chunk) => {
        const selected = chunk.chunk_id === selectedChunkId;
        return (
          <li key={chunk.chunk_id}>
            <button
              ref={selected ? selectedChunkRef : undefined}
              type="button"
              className={`w-full rounded-md border p-3 text-left transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring ${
                selected ? "border-primary bg-primary/5" : "border-border bg-card hover:bg-background"
              }`}
              aria-pressed={selected}
              onClick={() => onSelect(chunk)}
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="tnum rounded-full bg-background px-2 py-0.5 text-xs text-muted">
                  #{chunk.chunk_index + 1}
                </span>
                {chunk.content_kind ? (
                  <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
                    {chunk.content_kind}
                  </span>
                ) : null}
                {chunk.page_start ? (
                  <span className="tnum rounded-full bg-info-bg px-2 py-0.5 text-xs text-info">
                    {t("flow.chunks.pageRange", {
                      start: chunk.page_start,
                      end: chunk.page_end ?? chunk.page_start,
                    })}
                  </span>
                ) : null}
                {chunk.bbox ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-success-bg px-2 py-0.5 text-xs text-success">
                    <LocateFixed size={12} aria-hidden />
                    bbox
                  </span>
                ) : null}
              </div>
              {chunk.section_path ? (
                <p className="mt-2 break-words text-xs text-muted">{chunk.section_path}</p>
              ) : null}
              <p className="mt-2 max-h-24 overflow-hidden whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/90">
                {chunk.text}
              </p>
              <p className="mt-2 inline-flex items-center gap-1 break-all text-xs text-muted">
                <ListTree size={13} aria-hidden />
                {chunk.element_ids.length
                  ? chunk.element_ids.join(", ")
                  : t("flow.chunks.noElements")}
              </p>
            </button>
          </li>
        );
      })}
    </ol>
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
      <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 xl:grid-cols-6">
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.parser")}</dt>
          <dd className="mt-0.5 font-medium text-foreground">
            {t(parserProfileKey(profile.parser_profile))}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.parserBackend")}</dt>
          <dd className="mt-0.5 break-all font-medium text-foreground">
            {profile.parser_backend}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.parserVersion")}</dt>
          <dd className="tnum mt-0.5 font-medium text-foreground">
            {profile.parser_version}
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
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.previewKind")}</dt>
          <dd className="mt-0.5 font-medium text-foreground">
            {t(sourcePreviewKey(profile.preview_kind))}
          </dd>
        </div>
      </dl>
      {profile.unsupported_reason ? (
        <p className="mt-3 text-xs text-warning">
          {t("sourceProfile.unsupportedReason")}:{" "}
          {unsupportedReasonLabel(profile.unsupported_reason)}
        </p>
      ) : null}
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

function segmentStatusLabel(status: string): string {
  switch (status) {
    case "QUEUED":
      return t("flow.segments.status.queued");
    case "RUNNING":
      return t("flow.segments.status.running");
    case "SUCCEEDED":
      return t("flow.segments.status.succeeded");
    case "FAILED":
      return t("flow.segments.status.failed");
    case "CANCELLED":
      return t("flow.segments.status.cancelled");
    default:
      return status;
  }
}

function segmentStatusClass(status: string): string {
  const base = "rounded-full px-2 py-0.5 text-xs font-medium";
  switch (status) {
    case "SUCCEEDED":
      return `${base} bg-success-bg text-success`;
    case "FAILED":
    case "CANCELLED":
      return `${base} bg-danger-bg text-danger`;
    case "RUNNING":
      return `${base} bg-info-bg text-info`;
    default:
      return `${base} bg-background text-muted`;
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
