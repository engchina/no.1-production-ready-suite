"use client";

import {
  Braces,
  Check,
  Clock3,
  Download,
  FileSearch,
  FileText,
  GitBranch,
  ListTree,
  LocateFixed,
  Pencil,
  RotateCcw,
  Route,
  Save,
  Send,
  TriangleAlert,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";

import { ChunkSetExperimentPanel } from "./ChunkSetExperimentPanel";
import { DocumentPreview } from "./DocumentPreview";
import { DocumentProcessingConfigPanel } from "./DocumentProcessingConfigPanel";
import { IngestionConfigDriftBanner } from "@/components/knowledge-bases/IngestionConfigDriftBanner";
import { DocumentExtraction, DocumentRawText } from "./DocumentExtraction";
import { ExtractedText, IndexBadge, InfoChip } from "./extraction-bits";
import {
  type IngestionParserDisplay,
  type IngestionProgressSummary,
  type ProgressUnit,
  ingestConflictBannerIsStale,
  resolveIngestionParserDisplay,
  resolveIngestionProgressSummary,
  shouldShowProcessingWatchBanner,
} from "./DocumentWorkspace.logic";
import {
  normalizeIngestionErrorMessage,
  resolveDocumentFailureView,
  resolveIngestionErrorDisplayPlan,
} from "./ingestion-error-display";
import { ReviewTextEditor } from "./ReviewTextEditor";
import { KnowledgeBaseScopePicker } from "@/components/knowledge-bases/KnowledgeBaseScopePicker";
import { FlowStepper, STEP_LABEL_KEY } from "@/components/upload/FlowStepper";
import { StatusBadge } from "@/components/StatusBadge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { FormStatus } from "@/components/ui/form-status";
import { EmptyState, ErrorState } from "@/components/StateViews";
import { Skeleton } from "@/components/ui/skeleton";
import {
  api,
  ApiError,
  type DocumentElement,
  type DocumentChunkView,
  type DocumentExtractionExportFormat,
  type DocumentReviewEditsRequest,
  type ExtractionTable,
  type ExtractionTableCell,
  type IngestionJob,
  type IngestionJobPhase,
  type IngestionSegment,
  type KnowledgeBaseRef,
  type SourceProfile,
} from "@/lib/api";
import { parseStructuredExtraction, type SourceDerivationView } from "@/lib/extraction";
import {
  documentWorkspaceShouldRefresh,
  ingestionJobIsActive,
  useDocument,
  useDocumentChunks,
  useDocumentChunkSets,
  useDocumentExtractionExport,
  useDocumentIngestionConfig,
  useDocumentIngestionJobs,
  useDocumentIngestionSegments,
  useDocumentKnowledgeBases,
  useApproveDocument,
  useEnqueueDocumentIngestionJob,
  useIngestionJob,
  useRejectDocument,
  useReplaceDocumentKnowledgeBases,
  useRetryFailedDocumentIngestionSegments,
  useSaveDocumentReviewEdits,
} from "@/lib/queries";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { toast } from "@/lib/toast";
import { t, type I18nKey } from "@/lib/i18n";
import { formatBytes, formatDateTime, formatNumber, parseApiDateTime } from "@/lib/format";
import { scrollFocusedControlIntoView } from "@/lib/focus-scroll";
import {
  type BboxCoordinateMode,
  type BboxOverlayUnit,
  type BboxPageSize,
  bboxCoordinateModeFromMetadata,
  bboxFromMetadata,
  bboxPageRotationFromMetadata,
  bboxPageSizeFromMetadata,
  bboxUnitFromMetadata,
  withBboxPageRotation,
} from "@/lib/bbox";
import {
  isSameParserBackend,
  parserBackendLabel,
  parserProfileKey,
  sourceModalityKey,
  sourcePreviewKey,
  sourceWarningKey,
  unsupportedReasonLabel,
} from "@/lib/source-profile-labels";
import {
  findTableCellTarget,
  tableCellKey,
  type TableCellFocusTarget,
} from "@/lib/table-cell-focus";
import { cn } from "@/lib/utils";

const DOCUMENT_WORKSPACE_REFETCH_INTERVAL_MS = 4000;

function emptyReviewEdits(): DocumentReviewEditsRequest {
  return { element_edits: [], table_cell_edits: [] };
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function workspaceElementKey(element: DocumentElement): string {
  return element.element_id || `el-${String(element.order).padStart(4, "0")}`;
}

function integerSearchParam(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

function numberSearchParam(value: string | null): number | null {
  if (!value) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function bboxSearchParam(value: string | null): number[] | null {
  return bboxFromMetadata(value ? { bbox: value } : null);
}

function bboxModeSearchParam(value: string | null): BboxCoordinateMode | null {
  return value === "xyxy" || value === "xywh" ? value : null;
}

function bboxUnitSearchParam(value: string | null): BboxOverlayUnit | null {
  return value === "ratio" || value === "percent" || value === "absolute" ? value : null;
}

function pageSizeSearchParams(
  width: string | null,
  height: string | null,
  rotation: string | null
): BboxPageSize | null {
  const parsedWidth = numberSearchParam(width);
  const parsedHeight = numberSearchParam(height);
  return withBboxPageRotation(
    parsedWidth && parsedHeight ? { width: parsedWidth, height: parsedHeight } : null,
    integerSearchParam(rotation)
  );
}

function findTableCellByKey(
  tables: ExtractionTable[],
  key: string | null
): TableCellFocusTarget | null {
  if (!key) return null;
  for (const table of tables) {
    for (const cell of table.cells) {
      const candidateKey = tableCellKey(table.table_id, cell);
      if (candidateKey === key) return { key, table, cell };
    }
  }
  return null;
}

type WorkspaceFocusRequest = {
  key: string;
  target: "chunk" | "element" | "table_cell";
};

type UrlFallbackFocus = {
  key: string;
  page: number | null;
  bbox: number[] | null;
  bboxMode: BboxCoordinateMode | null;
  bboxUnit: BboxOverlayUnit | null;
  pageSize: BboxPageSize | null;
};

/** 文書プレビュー作業領域：原本プレビュー｜本文・構造化要素＋取込アクション。 */
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
  const chunkSetsQuery = useDocumentChunkSets(documentId);
  const ingestionConfigQuery = useDocumentIngestionConfig(documentId);
  const documentJobsQuery = useDocumentIngestionJobs(documentId);
  const segmentsQuery = useDocumentIngestionSegments(documentId);
  const [exportFormat, setExportFormat] =
    useState<DocumentExtractionExportFormat>("markdown");
  const extractionExportQuery = useDocumentExtractionExport(documentId, exportFormat);
  const [searchParams] = useSearchParams();
  const enqueueIngestion = useEnqueueDocumentIngestionJob();
  const approveDocument = useApproveDocument();
  const saveReviewEdits = useSaveDocumentReviewEdits();
  const rejectDocument = useRejectDocument();
  const confirm = useConfirm();
  const retryFailedSegments = useRetryFailedDocumentIngestionSegments();
  const queuedJob = useIngestionJob(enqueueIngestion.data?.id ?? null);
  const approvedJob = useIngestionJob(approveDocument.data?.id ?? null);
  const retriedSegmentJob = useIngestionJob(retryFailedSegments.data?.id ?? null);
  const [localWatchProcessing, setLocalWatchProcessing] = useState(false);
  const [editingReview, setEditingReview] = useState(false);
  const [reviewEdits, setReviewEdits] =
    useState<DocumentReviewEditsRequest>(emptyReviewEdits);
  const [selectedElementId, setSelectedElementId] = useState<string | null>(null);
  const [selectedChunkId, setSelectedChunkId] = useState<string | null>(null);
  const [selectedTableCellKey, setSelectedTableCellKey] = useState<string | null>(null);
  const [previewVariant, setPreviewVariant] = useState<"original" | "prepared">("original");
  const [previewFocusSource, setPreviewFocusSource] =
    useState<"chunk" | "element" | "table_cell">("chunk");
  const [focusRequest, setFocusRequest] = useState<WorkspaceFocusRequest | null>(null);
  const [urlFallbackFocus, setUrlFallbackFocus] = useState<UrlFallbackFocus | null>(null);
  const appliedFocusRequestRef = useRef<string | null>(null);
  const requestedChunkId = searchParams.get("chunk_id");
  const requestedElementId = searchParams.get("element_id");
  const requestedTableId = searchParams.get("table_id");
  const requestedCellRefParam = searchParams.get("cell_ref");
  const requestedFormulaCellRef = searchParams.get("formula_cell_ref");
  const requestedCellRef = requestedFormulaCellRef ?? requestedCellRefParam;
  const requestedCellRow = integerSearchParam(searchParams.get("cell_row"));
  const requestedCellCol = integerSearchParam(searchParams.get("cell_col"));
  const hasReviewEdits =
    (reviewEdits.element_edits?.length ?? 0) > 0 ||
    (reviewEdits.table_cell_edits?.length ?? 0) > 0;
  const allowReviewNavigationRef = useRef(false);
  useEffect(() => {
    if (!hasReviewEdits) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      if (allowReviewNavigationRef.current) return;
      event.preventDefault();
      event.returnValue = "";
    };
    const handleLinkClick = (event: MouseEvent) => {
      if (allowReviewNavigationRef.current || event.defaultPrevented || event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target instanceof Element ? event.target : null;
      const anchor = target?.closest<HTMLAnchorElement>("a[href]");
      if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
      const next = new URL(anchor.href, window.location.href);
      const current = new URL(window.location.href);
      if (
        next.origin === current.origin &&
        next.pathname === current.pathname &&
        next.search === current.search
      ) {
        return;
      }
      event.preventDefault();
      void confirm({
        title: t("flow.review.edit.leaveTitle"),
        description: t("flow.review.edit.leaveDescription"),
        confirmLabel: t("flow.review.edit.leaveConfirm"),
        tone: "warning",
      }).then((confirmed) => {
        if (!confirmed) return;
        allowReviewNavigationRef.current = true;
        window.location.assign(next.href);
      });
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    document.addEventListener("click", handleLinkClick, true);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      document.removeEventListener("click", handleLinkClick, true);
    };
  }, [confirm, hasReviewEdits]);
  // インスペクタ右ペインのタブ。要素/表セル指定の deep-link は構造化要素を、
  // chunk のみの引用 deep-link は Chunk タブを初期表示する。
  const [inspectorTab, setInspectorTab] = useState<"text" | "extraction" | "chunks" | "export">(() => {
    if (requestedElementId || requestedTableId || requestedCellRef) return "extraction";
    return requestedChunkId ? "chunks" : "text";
  });
  const requestedUrlFocus = useMemo<UrlFallbackFocus | null>(() => {
    const page = integerSearchParam(searchParams.get("page"));
    const bbox = bboxSearchParam(searchParams.get("bbox"));
    if (!page && !bbox) return null;
    return {
      key: [
        "citation",
        requestedChunkId ?? "",
        requestedElementId ?? "",
        requestedTableId ?? "",
        requestedFormulaCellRef ?? "",
        requestedCellRef ?? "",
        String(requestedCellRow ?? ""),
        String(requestedCellCol ?? ""),
        String(page ?? ""),
        searchParams.get("bbox") ?? "",
      ].join("\u0000"),
      page,
      bbox,
      bboxMode: bboxModeSearchParam(searchParams.get("bbox_mode")),
      bboxUnit: bboxUnitSearchParam(searchParams.get("bbox_unit")),
      pageSize: pageSizeSearchParams(
        searchParams.get("page_width"),
        searchParams.get("page_height"),
        searchParams.get("page_rotation")
      ),
    };
  }, [
    requestedCellCol,
    requestedCellRef,
    requestedCellRow,
    requestedChunkId,
    requestedElementId,
    requestedFormulaCellRef,
    requestedTableId,
    searchParams,
  ]);
  const status = query.data?.status;
  const latestDocumentJob = documentJobsQuery.data?.[0] ?? null;
  const latestDocumentJobActive = ingestionJobIsActive(latestDocumentJob?.status);
  const [elapsedNowMs, setElapsedNowMs] = useState(() => Date.now());
  const queuedIngestionJobStatus = queuedJob.data?.status ?? enqueueIngestion.data?.status;
  const approvedIngestionJobStatus = approvedJob.data?.status ?? approveDocument.data?.status;
  const retriedSegmentJobStatus = retriedSegmentJob.data?.status ?? retryFailedSegments.data?.status;
  const queuedJobErrorMessage =
    queuedJob.data?.status === "FAILED"
      ? queuedJob.data.error_message ?? t("flow.ingestFailed")
      : null;
  const retriedSegmentJobErrorMessage =
    retriedSegmentJob.data?.status === "FAILED"
      ? retriedSegmentJob.data.error_message ?? t("flow.ingestFailed")
      : null;
  const activeSubmittedJob = [
    latestDocumentJob?.status,
    queuedIngestionJobStatus,
    approvedIngestionJobStatus,
    retriedSegmentJobStatus,
  ].some(ingestionJobIsActive);
  const autoRefreshActive = documentWorkspaceShouldRefresh({
    documentStatus: status,
    watchProcessing,
    localWatchProcessing,
    jobStatuses: [
      latestDocumentJob?.status,
      queuedIngestionJobStatus,
      approvedIngestionJobStatus,
      retriedSegmentJobStatus,
    ],
    segmentStatuses: segmentsQuery.data?.map((segment) => segment.status) ?? [],
  });
  // 文書失敗を 1 本化（messaging-spec §9 P2/P5）: 原因 1 本 + 失敗工程の導出。
  const documentFailure = useMemo(
    () =>
      resolveDocumentFailureView({
        documentStatus: status,
        latestJobStatus: latestDocumentJob?.status,
        latestJobPhase: latestDocumentJob?.phase,
        latestJobErrorMessage: latestDocumentJob?.error_message,
        segments: segmentsQuery.data ?? [],
        documentErrorMessage: query.data?.error_message,
      }),
    [
      status,
      latestDocumentJob?.status,
      latestDocumentJob?.phase,
      latestDocumentJob?.error_message,
      segmentsQuery.data,
      query.data?.error_message,
    ]
  );
  const ingestionErrorDisplays = useMemo(
    () =>
      resolveIngestionErrorDisplayPlan({
        latestJobErrorMessage: latestDocumentJob?.error_message,
        segments: segmentsQuery.data ?? [],
        documentErrorMessage: query.data?.error_message,
        queuedJobErrorMessage,
        retriedSegmentJobErrorMessage,
        // 上部の原因バナーに昇格した本文は詳細側で再掲しない（§9 P2）。
        suppressMessages: [documentFailure.primaryMessage],
      }),
    [
      latestDocumentJob?.error_message,
      segmentsQuery.data,
      query.data?.error_message,
      queuedJobErrorMessage,
      retriedSegmentJobErrorMessage,
      documentFailure.primaryMessage,
    ]
  );
  // 取込・診断の折りたたみ。通常は閉じておき、取込中/失敗/エラー/セグメント失敗時のみ自動展開する。
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const diagnosticsAutoOpenedRef = useRef(false);
  const diagnosticsHasActivity =
    status === "INGESTING" ||
    status === "ERROR" ||
    latestDocumentJobActive ||
    latestDocumentJob?.status === "FAILED" ||
    ingestionErrorDisplays.segmentIds.size > 0;
  useEffect(() => {
    if (diagnosticsHasActivity && !diagnosticsAutoOpenedRef.current) {
      diagnosticsAutoOpenedRef.current = true;
      setDiagnosticsOpen(true);
    }
  }, [diagnosticsHasActivity]);
  const approveErrorText = approveDocument.isError
    ? errorMessage(approveDocument.error, t("flow.approveFailed"))
    : "";
  const saveReviewErrorText = saveReviewEdits.isError
    ? errorMessage(saveReviewEdits.error, t("flow.review.edit.saveError"))
    : "";
  const approveNeedsReingest =
    approveErrorText.includes("再取込") || approveErrorText.includes("再取り込み");
  const parsedExtraction = useMemo(
    () => parseStructuredExtraction(query.data?.extraction ?? {}),
    [query.data?.extraction]
  );
  const latestChunkSet = chunkSetsQuery.data?.[chunkSetsQuery.data.length - 1] ?? null;
  const handleReprocessPhase = async (phase: IngestionJobPhase) => {
    const confirmed = await confirm({
      title: t(`flow.reprocess.${phase}.title` as I18nKey),
      description: t(`flow.reprocess.${phase}.description` as I18nKey),
      confirmLabel: t("flow.reprocess.confirm"),
      tone: "warning",
    });
    if (!confirmed) return;
    enqueueIngestion.mutate(
      { id: documentId, force: true, phase },
      {
        onSuccess: (job) => {
          setLocalWatchProcessing(job.status === "QUEUED" || job.status === "RUNNING");
          toast.success(t("flow.reingestQueued"));
        },
      }
    );
  };
  const refetchDocument = query.refetch;
  const refetchChunks = chunksQuery.refetch;
  const refetchChunkSets = chunkSetsQuery.refetch;
  const refetchDocumentJobs = documentJobsQuery.refetch;
  const refetchSegments = segmentsQuery.refetch;
  const refetchExtractionExport = extractionExportQuery.refetch;
  useEffect(() => {
    if (!query.data?.preprocess_artifact && previewVariant === "prepared") {
      setPreviewVariant("original");
    }
  }, [previewVariant, query.data?.preprocess_artifact]);
  const resetEnqueueIngestion = enqueueIngestion.reset;
  useEffect(() => {
    const errorStatus =
      enqueueIngestion.error instanceof ApiError ? enqueueIngestion.error.status : null;
    if (ingestConflictBannerIsStale({ errorStatus, hasActiveJob: activeSubmittedJob })) {
      resetEnqueueIngestion();
    }
  }, [enqueueIngestion.error, activeSubmittedJob, resetEnqueueIngestion]);
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
  const selectedTableCell = useMemo(
    () => findTableCellByKey(parsedExtraction.tables, selectedTableCellKey),
    [parsedExtraction.tables, selectedTableCellKey]
  );
  const focusPage =
    previewFocusSource === "table_cell"
      ? selectedTableCell?.cell.page_number ??
        selectedTableCell?.table.page_number ??
        selectedElement?.page_number ??
        selectedChunk?.page_start ??
        null
      : previewFocusSource === "element"
        ? selectedElement?.page_number ?? selectedChunk?.page_start ?? null
        : selectedChunk?.page_start ?? selectedElement?.page_number ?? null;
  const effectiveFocusPage = focusPage ?? urlFallbackFocus?.page ?? null;
  const selectedChunkBbox = selectedChunk?.bbox ?? bboxFromMetadata(selectedChunk?.metadata);
  const selectedElementBbox =
    selectedElement?.bbox ?? bboxFromMetadata(selectedElement?.metadata);
  const selectedTableCellBbox =
    selectedTableCell?.cell.bbox ?? bboxFromMetadata(selectedTableCell?.cell.metadata);
  const focusBbox =
    previewFocusSource === "table_cell"
      ? selectedTableCellBbox ?? selectedElementBbox ?? selectedChunkBbox ?? null
      : previewFocusSource === "element"
        ? selectedElementBbox ?? selectedChunkBbox ?? null
        : selectedChunkBbox ?? selectedElementBbox ?? null;
  const effectiveFocusBbox = focusBbox ?? urlFallbackFocus?.bbox ?? null;
  const focusBboxMode =
    previewFocusSource === "table_cell"
      ? bboxCoordinateModeFromMetadata(selectedTableCell?.cell.metadata) ??
        bboxCoordinateModeFromMetadata(selectedTableCell?.table.metadata) ??
        bboxCoordinateModeFromMetadata(selectedElement?.metadata) ??
        bboxCoordinateModeFromMetadata(selectedChunk?.metadata)
      : previewFocusSource === "element"
        ? bboxCoordinateModeFromMetadata(selectedElement?.metadata) ??
          bboxCoordinateModeFromMetadata(selectedChunk?.metadata)
        : bboxCoordinateModeFromMetadata(selectedChunk?.metadata) ??
          bboxCoordinateModeFromMetadata(selectedElement?.metadata);
  const effectiveFocusBboxMode = focusBboxMode ?? urlFallbackFocus?.bboxMode ?? null;
  const focusBboxUnit =
    previewFocusSource === "table_cell"
      ? bboxUnitFromMetadata(selectedTableCell?.cell.metadata) ??
        bboxUnitFromMetadata(selectedTableCell?.table.metadata) ??
        bboxUnitFromMetadata(selectedElement?.metadata) ??
        bboxUnitFromMetadata(selectedChunk?.metadata)
      : previewFocusSource === "element"
        ? bboxUnitFromMetadata(selectedElement?.metadata) ??
          bboxUnitFromMetadata(selectedChunk?.metadata)
        : bboxUnitFromMetadata(selectedChunk?.metadata) ??
          bboxUnitFromMetadata(selectedElement?.metadata);
  const effectiveFocusBboxUnit = focusBboxUnit ?? urlFallbackFocus?.bboxUnit ?? null;
  const focusPageSizeFromMetadata =
    previewFocusSource === "table_cell"
      ? bboxPageSizeFromMetadata(selectedTableCell?.cell.metadata) ??
        bboxPageSizeFromMetadata(selectedTableCell?.table.metadata) ??
        bboxPageSizeFromMetadata(selectedElement?.metadata) ??
        bboxPageSizeFromMetadata(selectedChunk?.metadata)
      : previewFocusSource === "element"
        ? bboxPageSizeFromMetadata(selectedElement?.metadata) ??
          bboxPageSizeFromMetadata(selectedChunk?.metadata)
        : bboxPageSizeFromMetadata(selectedChunk?.metadata) ??
          bboxPageSizeFromMetadata(selectedElement?.metadata);
  const focusPageRotationFromMetadata =
    previewFocusSource === "table_cell"
      ? bboxPageRotationFromMetadata(selectedTableCell?.cell.metadata) ??
        bboxPageRotationFromMetadata(selectedTableCell?.table.metadata) ??
        bboxPageRotationFromMetadata(selectedElement?.metadata) ??
        bboxPageRotationFromMetadata(selectedChunk?.metadata)
      : previewFocusSource === "element"
        ? bboxPageRotationFromMetadata(selectedElement?.metadata) ??
          bboxPageRotationFromMetadata(selectedChunk?.metadata)
        : bboxPageRotationFromMetadata(selectedChunk?.metadata) ??
          bboxPageRotationFromMetadata(selectedElement?.metadata);
  const focusPageSize = useMemo(() => {
    if (!effectiveFocusPage) return null;
    const page = parsedExtraction.pages.find((item) => item.page_number === effectiveFocusPage);
    if (page?.width && page?.height) {
      return withBboxPageRotation(
        { width: page.width, height: page.height },
        page.rotation ?? focusPageRotationFromMetadata ?? urlFallbackFocus?.pageSize?.rotation
      );
    }
    return withBboxPageRotation(
      focusPageSizeFromMetadata ?? urlFallbackFocus?.pageSize ?? null,
      focusPageRotationFromMetadata ?? urlFallbackFocus?.pageSize?.rotation
    );
  }, [
    effectiveFocusPage,
    focusPageRotationFromMetadata,
    focusPageSizeFromMetadata,
    parsedExtraction.pages,
    urlFallbackFocus,
  ]);
  function selectElement(elementId: string) {
    const linkedChunk = chunksQuery.data?.find((chunk) => chunk.element_ids.includes(elementId));
    setUrlFallbackFocus(null);
    setSelectedElementId(elementId);
    setSelectedTableCellKey(null);
    if (chunksQuery.data) {
      setSelectedChunkId(linkedChunk?.chunk_id ?? null);
    }
    setPreviewFocusSource("element");
  }

  function selectTableCell(table: ExtractionTable, cell: ExtractionTableCell) {
    const key = tableCellKey(table.table_id, cell);
    const linkedElementId = table.element_id ?? null;
    const linkedChunk = linkedElementId
      ? chunksQuery.data?.find((chunk) => chunk.element_ids.includes(linkedElementId))
      : chunksQuery.data?.find((chunk) => chunk.content_kind === "table");
    setSelectedTableCellKey(key);
    setUrlFallbackFocus(null);
    setSelectedElementId(linkedElementId);
    if (chunksQuery.data) {
      setSelectedChunkId(linkedChunk?.chunk_id ?? selectedChunkId ?? null);
    }
    setPreviewFocusSource("table_cell");
  }

  useEffect(() => {
    if (!autoRefreshActive) return;
    const timer = window.setInterval(() => {
      void refetchDocument();
      void refetchDocumentJobs();
      void refetchSegments();
      void refetchChunks();
      void refetchChunkSets();
      void refetchExtractionExport();
    }, DOCUMENT_WORKSPACE_REFETCH_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [
    autoRefreshActive,
    refetchChunkSets,
    refetchChunks,
    refetchDocument,
    refetchDocumentJobs,
    refetchExtractionExport,
    refetchSegments,
  ]);

  useEffect(() => {
    if (!latestDocumentJobActive) return;
    setElapsedNowMs(Date.now());
    const timer = window.setInterval(() => setElapsedNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [latestDocumentJob?.id, latestDocumentJob?.started_at, latestDocumentJobActive]);

  useEffect(() => {
    if (
      !activeSubmittedJob &&
      (status === "INDEXED" ||
        status === "ERROR" ||
        status === "PREPROCESSED" ||
        status === "REVIEW")
    ) {
      setLocalWatchProcessing(false);
    }
  }, [activeSubmittedJob, status]);

  useEffect(() => {
    const chunks = chunksQuery.data ?? [];
    if (
      !chunks.length &&
      !requestedChunkId &&
      !requestedElementId &&
      !requestedTableId &&
      !requestedUrlFocus
    ) {
      return;
    }
    const requestedCellLocator =
      requestedCellRef || requestedCellRow != null || requestedCellCol != null
        ? {
            tableId: requestedTableId,
            cellRef: requestedCellRef,
            row: requestedCellRow,
            col: requestedCellCol,
          }
        : null;
    const requestedCell = requestedCellLocator
      ? findTableCellTarget(parsedExtraction.tables, requestedCellLocator)
      : null;
    const hasRequestedStructuredFocus = Boolean(
      requestedChunkId ||
        requestedElementId ||
        requestedTableId ||
        requestedCellRef ||
        requestedCellRow != null ||
        requestedCellCol != null
    );
    const requestedFocusKey = requestedCell
      ? `table_cell:${requestedCell.key}\u0000${requestedChunkId ?? ""}\u0000${
          requestedElementId ?? ""
        }`
      : requestedChunkId
      ? `chunk:${requestedChunkId}\u0000${requestedElementId ?? ""}`
      : requestedElementId
        ? `element:${requestedElementId}`
        : null;
    if (requestedFocusKey && appliedFocusRequestRef.current !== requestedFocusKey) {
      if (requestedCell) {
        const linkedElementId = requestedCell.table.element_id ?? requestedElementId;
        const linkedChunk = requestedChunkId
          ? chunks.find((chunk) => chunk.chunk_id === requestedChunkId)
          : linkedElementId
            ? chunks.find((chunk) => chunk.element_ids.includes(linkedElementId))
            : null;
        setSelectedChunkId(linkedChunk?.chunk_id ?? null);
        setSelectedElementId(linkedElementId ?? null);
        setSelectedTableCellKey(requestedCell.key);
        setUrlFallbackFocus(requestedUrlFocus);
        setPreviewFocusSource("table_cell");
        setFocusRequest({ key: requestedFocusKey, target: "table_cell" });
        appliedFocusRequestRef.current = requestedFocusKey;
        return;
      }
      if (requestedChunkId) {
        const requestedChunk = chunks.find((chunk) => chunk.chunk_id === requestedChunkId);
        if (requestedChunk) {
          setSelectedChunkId(requestedChunk.chunk_id);
          setSelectedElementId(
            requestedElementId && requestedChunk.element_ids.includes(requestedElementId)
              ? requestedElementId
              : requestedChunk.element_ids[0] ?? null
          );
          setSelectedTableCellKey(null);
          setUrlFallbackFocus(requestedUrlFocus);
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
          setSelectedTableCellKey(null);
          setUrlFallbackFocus(requestedUrlFocus);
          setPreviewFocusSource("element");
          setFocusRequest({ key: requestedFocusKey, target: "element" });
          appliedFocusRequestRef.current = requestedFocusKey;
          return;
        }
      }
      if (requestedUrlFocus) {
        setUrlFallbackFocus(requestedUrlFocus);
        appliedFocusRequestRef.current = requestedUrlFocus.key;
        return;
      }
    }
    if (
      requestedUrlFocus &&
      !requestedFocusKey &&
      appliedFocusRequestRef.current !== requestedUrlFocus.key
    ) {
      setUrlFallbackFocus(requestedUrlFocus);
      appliedFocusRequestRef.current = requestedUrlFocus.key;
      return;
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
    if (hasRequestedStructuredFocus) {
      return;
    }
    const firstChunk = chunks[0];
    if (!firstChunk) return;
    setSelectedChunkId(firstChunk.chunk_id);
    setSelectedElementId(firstChunk.element_ids[0] ?? null);
    setSelectedTableCellKey(null);
  }, [
    chunksQuery.data,
    parsedExtraction.elements,
    parsedExtraction.tables,
    requestedCellCol,
    requestedCellRef,
    requestedCellRow,
    requestedChunkId,
    requestedElementId,
    requestedFormulaCellRef,
    requestedTableId,
    requestedUrlFocus,
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
  const preparedArtifact = doc.preprocess_artifact;
  const hasPreparedArtifact = Boolean(preparedArtifact?.object_storage_path);
  // PREPROCESSED なのに使える処理後ファイル(object_storage_path)が無い状態。承認(EXTRACT)は
  // 必ず 409 になるため、converted の真偽や artifact の有無に関わらず「ファイル準備から再処理」へ
  // 誘導する(null artifact / passthrough・path欠落 / 変換成功・保存失敗 を全て包含)。
  const preparedArtifactMissing = !hasPreparedArtifact;
  const preprocessStepSkipped =
    ingestionConfigQuery.data?.effective_preprocess_profile === "passthrough" ||
    (preparedArtifact?.profile === "passthrough" && preparedArtifact.converted === false) ||
    (parsedExtraction.sourceDerivation?.preprocessProfile === "passthrough" &&
      parsedExtraction.sourceDerivation.converted === false);
  const selectedPreviewVariant = previewVariant === "prepared" && hasPreparedArtifact
    ? "prepared"
    : "original";
  const selectedPreviewFileName =
    selectedPreviewVariant === "prepared" ? preparedArtifact?.file_name ?? doc.file_name : doc.file_name;
  const selectedPreviewSourceProfile =
    selectedPreviewVariant === "original" ? sourceProfile : null;
  const selectedPreviewDownloadUrl = api.documentContentUrl(documentId, {
    ...(selectedPreviewVariant === "prepared" ? { variant: selectedPreviewVariant } : {}),
    disposition: "attachment",
  });
  const duplicateSource = doc.duplicate_source;
  const duplicateMessage = duplicateSource
    ? t("upload.duplicateDetail", {
        name: duplicateSource.file_name,
        status: t(`status.${duplicateSource.status}` as I18nKey),
        uploadedAt: formatDateTime(duplicateSource.uploaded_at),
      })
    : t("upload.duplicate");
  const ingestionParser = resolveIngestionParserDisplay({
    segments: segmentsQuery.data ?? [],
    extractionBackend: extractionExportQuery.data?.parser_backend,
    extractionProfile: extractionExportQuery.data?.parser_profile,
    loading: segmentsQuery.isPending || extractionExportQuery.isPending,
  });

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
          <Banner severity="warning">
            <div className="space-y-1">
              <p>{duplicateMessage}</p>
              <p className="text-xs">{t("upload.duplicateForceHint")}</p>
            </div>
          </Banner>
        ) : null}

        <FlowStepper
          status={doc.status}
          skippedSteps={preprocessStepSkipped ? ["PREPROCESSING"] : []}
          failedStep={documentFailure.failedStep}
        />
        {documentFailure.errored ? (
          <Banner
            severity="danger"
            title={
              documentFailure.failedStep
                ? t("flow.error.atStep", { step: t(STEP_LABEL_KEY[documentFailure.failedStep]) })
                : t("flow.error.title")
            }
          >
            {documentFailure.primaryMessage ?? t("flow.error.fallback")}
          </Banner>
        ) : null}
        <DocumentProcessingConfigPanel
          documentId={documentId}
          data={ingestionConfigQuery.data ?? null}
          loading={ingestionConfigQuery.isPending}
          error={ingestionConfigQuery.error}
          onRetry={() => void ingestionConfigQuery.refetch()}
          disabled={
            activeSubmittedJob || !["UPLOADED", "INDEXED", "ERROR"].includes(doc.status)
          }
        />
        {shouldShowProcessingWatchBanner({
          watchProcessing,
          documentStatus: doc.status,
          latestJobStatus: latestDocumentJob?.status,
        }) ? (
          <Banner severity="info">{t("upload.ingestion.watch")}</Banner>
        ) : null}
        <IngestionConfigDriftBanner documentId={documentId} />

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

        <DocumentKnowledgeBaseEditor
          documentId={documentId}
          initialKnowledgeBases={doc.knowledge_bases}
        />

        {/* 文書失敗の原因は上部の原因バナー(documentFailure)に集約済み（§9 P2）。 */}
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
        {ingestionErrorDisplays.queuedJobMessage ? (
          <Banner severity="danger">{ingestionErrorDisplays.queuedJobMessage}</Banner>
        ) : null}
        {retryFailedSegments.isError ? (
          <Banner severity="danger">
            {errorMessage(retryFailedSegments.error, t("flow.segments.retryFailedError"))}
          </Banner>
        ) : null}
        {retryFailedSegments.data ? (
          <FormStatus
            tone="success"
            message={t("flow.segments.retryQueued")}
          />
        ) : null}
        {ingestionErrorDisplays.retriedSegmentJobMessage ? (
          <Banner severity="danger">{ingestionErrorDisplays.retriedSegmentJobMessage}</Banner>
        ) : null}

        <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
          {/* 左ペイン: 原本プレビュー(desktop は引用照合のアンカーとして sticky 固定) */}
          <section className="min-w-0 xl:sticky xl:top-4 xl:self-start">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-foreground">{t("flow.preview")}</h3>
              <div className="flex flex-wrap items-center justify-end gap-2">
                <div
                  role="group"
                  aria-label={t("flow.preview")}
                  className="inline-flex rounded-md border border-border bg-background p-0.5"
                >
                  <Button
                    type="button"
                    size="sm"
                    variant={selectedPreviewVariant === "original" ? "secondary" : "ghost"}
                    className="whitespace-nowrap"
                    onClick={() => setPreviewVariant("original")}
                  >
                    {t("flow.preview.before")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={selectedPreviewVariant === "prepared" ? "secondary" : "ghost"}
                    className="whitespace-nowrap"
                    onClick={() => setPreviewVariant("prepared")}
                    disabled={!hasPreparedArtifact}
                    title={!hasPreparedArtifact ? t("flow.preview.preparedUnavailable") : undefined}
                  >
                    {t("flow.preview.after")}
                  </Button>
                </div>
                <a
                  href={selectedPreviewDownloadUrl}
                  download={selectedPreviewFileName}
                  className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-md border border-border bg-background px-3 text-sm font-medium text-foreground transition-colors hover:bg-card focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <Download size={14} aria-hidden />
                  {t("flow.preview.download")}
                </a>
              </div>
            </div>
            <DocumentPreview
              documentId={documentId}
              fileName={selectedPreviewFileName}
              variant={selectedPreviewVariant}
              sourceProfile={selectedPreviewSourceProfile}
              preparedPdfAvailable={hasPreparedArtifact}
              focusPage={effectiveFocusPage}
              focusBbox={effectiveFocusBbox}
              focusBboxMode={effectiveFocusBboxMode}
              focusBboxUnit={effectiveFocusBboxUnit}
              focusPageSize={focusPageSize}
            />
          </section>

          {/* 右ペイン: 本文 / 構造化要素 / Chunk / エクスポート をタブ切替 */}
          <section className="min-w-0">
            <div
              role="tablist"
              aria-label={t("flow.inspector.tabs")}
              className="mb-3 flex flex-wrap items-center gap-1"
            >
              <InspectorTab
                id="inspector-tab-text"
                controls="inspector-panel-text"
                active={inspectorTab === "text"}
                onSelect={() => setInspectorTab("text")}
              >
                {t("flow.extraction.rawText")}
              </InspectorTab>
              <InspectorTab
                id="inspector-tab-extraction"
                controls="inspector-panel-extraction"
                active={inspectorTab === "extraction"}
                onSelect={() => setInspectorTab("extraction")}
              >
                {t("flow.extraction.title")}
              </InspectorTab>
              <InspectorTab
                id="inspector-tab-chunks"
                controls="inspector-panel-chunks"
                active={inspectorTab === "chunks"}
                onSelect={() => setInspectorTab("chunks")}
              >
                {t("flow.chunks.title")}
                {chunksQuery.data?.length ? (
                  <span className="tnum ml-1.5 opacity-70">
                    {formatNumber(chunksQuery.data.length)}
                  </span>
                ) : null}
              </InspectorTab>
              <InspectorTab
                id="inspector-tab-export"
                controls="inspector-panel-export"
                active={inspectorTab === "export"}
                onSelect={() => setInspectorTab("export")}
              >
                {t("flow.extractionExport.title")}
              </InspectorTab>
            </div>

            {inspectorTab === "text" ? (
              <div
                role="tabpanel"
                id="inspector-panel-text"
                aria-labelledby="inspector-tab-text"
                tabIndex={0}
                className="xl:h-[60vh] xl:overflow-y-auto xl:overscroll-contain xl:pr-1 xl:[scrollbar-gutter:stable]"
              >
                <DocumentRawText extraction={doc.extraction} />
              </div>
            ) : null}

            {inspectorTab === "extraction" ? (
              <div
                role="tabpanel"
                id="inspector-panel-extraction"
                aria-labelledby="inspector-tab-extraction"
                tabIndex={0}
                className="xl:h-[60vh] xl:overflow-y-auto xl:overscroll-contain xl:pr-1 xl:[scrollbar-gutter:stable]"
              >
                {doc.status === "REVIEW" ? (
                  <div className="mb-2 xl:sticky xl:top-0 xl:z-10 xl:bg-background xl:pb-2">
                    {editingReview ? (
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div>
                          {hasReviewEdits ? (
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={saveReviewEdits.isPending}
                              onClick={async () => {
                                const confirmed = await confirm({
                                  title: t("flow.review.edit.discardTitle"),
                                  description: t("flow.review.edit.discardDescription"),
                                  confirmLabel: t("flow.review.edit.discardConfirm"),
                                  tone: "warning",
                                });
                                if (!confirmed) return;
                                saveReviewEdits.reset();
                                setReviewEdits(emptyReviewEdits());
                                setEditingReview(false);
                              }}
                            >
                              <RotateCcw size={14} aria-hidden />
                              {t("flow.review.edit.discard")}
                            </Button>
                          ) : null}
                        </div>
                        <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() =>
                              saveReviewEdits.mutate(
                                { id: documentId, payload: reviewEdits },
                                {
                                  onSuccess: () => {
                                    setReviewEdits(emptyReviewEdits());
                                    toast.success(t("flow.review.edit.saved"));
                                  },
                                }
                              )
                            }
                            loading={saveReviewEdits.isPending}
                            disabled={
                              !hasReviewEdits ||
                              approveDocument.isPending ||
                              rejectDocument.isPending
                            }
                          >
                            {!saveReviewEdits.isPending ? <Save size={14} aria-hidden /> : null}
                            {t("flow.review.edit.save")}
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={saveReviewEdits.isPending}
                            onClick={() => setEditingReview(false)}
                          >
                            <X size={14} aria-hidden />
                            {t("flow.review.edit.close")}
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <div className="flex justify-end">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setEditingReview(true)}
                        >
                          <Pencil size={14} aria-hidden />
                          {t("flow.review.edit.structuredOpen")}
                        </Button>
                      </div>
                    )}
                    {editingReview && saveReviewEdits.isError ? (
                      <div className="mt-2">
                        <FormStatus tone="danger" message={saveReviewErrorText} />
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {doc.status === "REVIEW" && editingReview ? (
                  <ReviewTextEditor
                    extraction={doc.extraction}
                    edits={reviewEdits}
                    onChange={(edits) => {
                      saveReviewEdits.reset();
                      setReviewEdits(edits);
                    }}
                  />
                ) : (
                  <DocumentExtraction
                    extraction={doc.extraction}
                    selectedElementId={selectedElementId}
                    selectedTableCellKey={selectedTableCellKey}
                    focusRequestKey={focusRequest?.key ?? null}
                    focusSelectedElement={focusRequest?.target === "element"}
                    focusSelectedTableCell={focusRequest?.target === "table_cell"}
                    onElementSelect={selectElement}
                    onTableCellSelect={selectTableCell}
                  />
                )}
              </div>
            ) : null}

            {inspectorTab === "chunks" ? (
              <div
                role="tabpanel"
                id="inspector-panel-chunks"
                aria-labelledby="inspector-tab-chunks"
                tabIndex={0}
                className="xl:h-[60vh] xl:overflow-y-auto xl:overscroll-contain xl:pr-1 xl:[scrollbar-gutter:stable]"
              >
                <DocumentChunksPanel
                  chunks={chunksQuery.data ?? []}
                  loading={chunksQuery.isPending}
                  error={chunksQuery.isError}
                  selectedChunkId={selectedChunkId}
                  focusRequestKey={focusRequest?.target === "chunk" ? focusRequest.key : null}
                  onSelect={(chunk) => {
                    setUrlFallbackFocus(null);
                    setSelectedChunkId(chunk.chunk_id);
                    setSelectedElementId(chunk.element_ids[0] ?? null);
                    setSelectedTableCellKey(null);
                    setPreviewFocusSource("chunk");
                  }}
                />
              </div>
            ) : null}

            {inspectorTab === "export" ? (
              <div
                role="tabpanel"
                id="inspector-panel-export"
                aria-labelledby="inspector-tab-export"
                tabIndex={0}
                className="xl:h-[60vh] xl:overflow-y-auto xl:overscroll-contain xl:pr-1 xl:[scrollbar-gutter:stable]"
              >
                <DocumentExtractionExportPanel
                  format={exportFormat}
                  onFormatChange={setExportFormat}
                  content={extractionExportQuery.data?.content ?? ""}
                  loading={extractionExportQuery.isPending}
                  error={extractionExportQuery.isError}
                  pageCount={extractionExportQuery.data?.page_count ?? 0}
                  elementCount={extractionExportQuery.data?.element_count ?? 0}
                  chunkCount={extractionExportQuery.data?.chunks.length ?? 0}
                />
              </div>
            ) : null}
          </section>
        </div>

        <details
          className="rounded-md border border-border bg-card px-4 py-1"
          open={diagnosticsOpen}
          onToggle={(event) => setDiagnosticsOpen((event.target as HTMLDetailsElement).open)}
        >
          <summary className="flex min-h-10 cursor-pointer items-center gap-2 text-sm font-semibold text-foreground">
            <Wrench size={15} className="text-primary" aria-hidden />
            {t("flow.inspector.details")}
          </summary>
          <div className="space-y-5 pb-3 pt-3">
            {sourceProfile ? (
              <SourceProfilePanel profile={sourceProfile} ingestionParser={ingestionParser} />
            ) : null}
            {parsedExtraction.sourceDerivation ? (
              <SourceDerivationPanel
                derivation={parsedExtraction.sourceDerivation}
                originalFileName={sourceProfile?.original_file_name ?? doc.file_name}
              />
            ) : null}
            <IngestionJobsPanel
              jobs={documentJobsQuery.data ?? []}
              segments={segmentsQuery.data ?? []}
              loading={documentJobsQuery.isPending}
              error={documentJobsQuery.isError}
              nowMs={elapsedNowMs}
              suppressMessage={documentFailure.primaryMessage}
            />
            <IngestionSegmentsPanel
              segments={segmentsQuery.data ?? []}
              loading={segmentsQuery.isPending}
              error={segmentsQuery.isError}
              retrying={retryFailedSegments.isPending}
              visibleErrorSegmentIds={ingestionErrorDisplays.segmentIds}
              onRetryFailedSegments={() =>
                retryFailedSegments.mutate(documentId, {
                  onSuccess: (job) => {
                    setLocalWatchProcessing(job.status === "QUEUED" || job.status === "RUNNING");
                  },
                })
              }
            />
          </div>
        </details>

        {doc.status === "INDEXED" ? (
          <Banner severity="success">{t("flow.indexed")}</Banner>
        ) : null}

        {doc.status === "INDEXED" ? <ChunkSetExperimentPanel documentId={doc.id} /> : null}

        {doc.status === "PREPROCESSED" ? (
          preparedArtifactMissing ? (
            <Banner severity="danger">
              {preparedArtifact?.converted
                ? t("flow.preprocessed.persistFailed")
                : t("flow.preprocessed.preparedMissing")}
            </Banner>
          ) : (
            <Banner severity="info">{t("flow.preprocessed.description")}</Banner>
          )
        ) : null}
        {doc.status === "REVIEW" ? (
          <Banner severity="info">{t("flow.review.description")}</Banner>
        ) : null}
        {doc.status === "CHUNKED" ? (
          <Banner severity="info">{t("flow.chunked.description")}</Banner>
        ) : null}

        {approveDocument.isError ? (
          <Banner severity={approveNeedsReingest ? "warning" : "danger"}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1">{approveErrorText}</span>
              {approveNeedsReingest ? (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    enqueueIngestion.mutate(
                      { id: documentId, force: true },
                      {
                        onSuccess: (job) => {
                          setLocalWatchProcessing(
                            job.status === "QUEUED" || job.status === "RUNNING"
                          );
                          toast.success(t("flow.reingestQueued"));
                        },
                      }
                    )
                  }
                  loading={enqueueIngestion.isPending}
                >
                  {t("flow.reingest")}
                </Button>
              ) : null}
            </div>
          </Banner>
        ) : null}
        {rejectDocument.isError ? (
          <Banner severity="danger">
            {errorMessage(rejectDocument.error, t("flow.rejectFailed"))}
          </Banner>
        ) : null}

        {doc.status === "REVIEW" && hasReviewEdits ? (
          <FormStatus tone="warning" message={t("flow.review.edit.pending")} />
        ) : null}

        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
          {((doc.status === "PREPROCESSED" && !preparedArtifactMissing) ||
            doc.status === "REVIEW" ||
            doc.status === "CHUNKED") && (
            <>
              <Button
                onClick={() =>
                  approveDocument.mutate(
                    { id: documentId },
                    {
                      onSuccess: (job) => {
                        setLocalWatchProcessing(
                          job.status === "QUEUED" || job.status === "RUNNING"
                        );
                        setEditingReview(false);
                        setReviewEdits(emptyReviewEdits());
                        toast.success(t("flow.approved"));
                      },
                    }
                  )
                }
                loading={approveDocument.isPending}
                disabled={
                  rejectDocument.isPending ||
                  saveReviewEdits.isPending ||
                  (doc.status === "REVIEW" && hasReviewEdits)
                }
              >
                {!approveDocument.isPending ? <Check size={15} aria-hidden /> : null}
                {approveDocument.isPending
                  ? t("action.queueing")
                  : doc.status === "PREPROCESSED"
                    ? t("flow.approvePreprocess")
                    : doc.status === "CHUNKED"
                      ? t("flow.approveChunks")
                      : t("flow.approveExtraction")}
              </Button>
              {doc.status === "REVIEW" ? (
                <Button
                  variant="secondary"
                  onClick={async () => {
                    const confirmed = await confirm({
                      title: t("flow.rejectConfirm.title"),
                      description: t("flow.rejectConfirm.description"),
                      confirmLabel: t("flow.reject"),
                      tone: "warning",
                    });
                    if (!confirmed) return;
                    rejectDocument.mutate(
                      { id: documentId },
                      {
                        onSuccess: () => {
                          setEditingReview(false);
                          setReviewEdits(emptyReviewEdits());
                          toast.success(t("flow.rejected"));
                        },
                      }
                    );
                  }}
                  loading={rejectDocument.isPending}
                  disabled={approveDocument.isPending || saveReviewEdits.isPending}
                >
                  {!rejectDocument.isPending ? <X size={15} aria-hidden /> : null}
                  {rejectDocument.isPending ? t("action.processing") : t("flow.reject")}
                </Button>
              ) : null}
            </>
          )}
          {doc.status === "PREPROCESSED" && preparedArtifactMissing && (
            <Button
              variant="primary"
              onClick={() => void handleReprocessPhase("PREPROCESS")}
              disabled={enqueueIngestion.isPending}
            >
              <RotateCcw size={15} aria-hidden />
              {t("flow.reprocess.preprocess")}
            </Button>
          )}
          {(doc.status === "UPLOADED" || doc.status === "ERROR") && (
            <Button
              onClick={() =>
                enqueueIngestion.mutate(
                  { id: documentId, force: Boolean(doc.duplicate_of_document_id) },
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
              {enqueueIngestion.isPending
                ? t("action.queueing")
                : doc.duplicate_of_document_id
                  ? t("action.enqueueDuplicateIngestion")
                  : t("action.enqueueIngestion")}
            </Button>
          )}
          {(doc.status === "REVIEW" ||
            doc.status === "CHUNKED" ||
            doc.status === "INDEXED" ||
            doc.status === "ERROR") && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void handleReprocessPhase("PREPROCESS")}
                disabled={enqueueIngestion.isPending}
              >
                <RotateCcw size={15} aria-hidden />
                {t("flow.reprocess.preprocess")}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void handleReprocessPhase("EXTRACT")}
                disabled={enqueueIngestion.isPending || !hasPreparedArtifact}
                title={!hasPreparedArtifact ? t("flow.preview.preparedUnavailable") : undefined}
              >
                <RotateCcw size={15} aria-hidden />
                {t("flow.reprocess.extract")}
              </Button>
              {(doc.status === "CHUNKED" ||
                doc.status === "INDEXED" ||
                (doc.status === "ERROR" && Boolean(parsedExtraction.rawText))) && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleReprocessPhase("CHUNK")}
                  disabled={enqueueIngestion.isPending}
                >
                  <RotateCcw size={15} aria-hidden />
                  {t("flow.reprocess.chunk")}
                </Button>
              )}
              {(doc.status === "INDEXED" || (doc.status === "ERROR" && latestChunkSet)) && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => void handleReprocessPhase("INDEX")}
                  disabled={enqueueIngestion.isPending}
                >
                  <RotateCcw size={15} aria-hidden />
                  {t("flow.reprocess.index")}
                </Button>
              )}
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function IngestionJobsPanel({
  jobs,
  segments,
  loading,
  error,
  nowMs,
  suppressMessage,
}: {
  jobs: IngestionJob[];
  segments: IngestionSegment[];
  loading: boolean;
  error: boolean;
  nowMs: number;
  /** 上部の原因バナーで表示済みの本文。一致時はここで再掲しない（§9 P2）。 */
  suppressMessage?: string | null;
}) {
  if (loading) return <Skeleton className="h-24 w-full rounded-md" />;
  if (error) {
    return (
      <Banner severity="warning" title={t("flow.jobs.loadError")}>
        {t("flow.jobs.loadErrorHint")}
      </Banner>
    );
  }
  if (!jobs.length) return null;

  const latest = jobs[0];
  const active = ingestionJobIsActive(latest.status);
  const normalizedLatestError = normalizeIngestionErrorMessage(latest.error_message);
  const latestErrorMessage =
    normalizedLatestError && normalizedLatestError !== suppressMessage
      ? normalizedLatestError
      : null;
  const progressSummary =
    latest.phase === "PREPROCESS" || latest.phase === "EXTRACT"
      ? resolveIngestionProgressSummary(segments)
      : null;

  return (
    <section className="rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Clock3 size={16} className="text-primary" aria-hidden />
          {t("flow.jobs.title")}
        </h3>
        <span className="text-xs text-muted">
          {t("flow.jobs.count", { count: jobs.length })}
        </span>
      </div>
      <div className="mt-3 rounded-md border border-border bg-card/40 p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={jobStatusClass(latest.status)}>{t(jobStatusKey(latest.status))}</span>
          <span className="rounded-full bg-background px-2 py-0.5 text-xs font-medium text-foreground">
            {t(jobPhaseKey(latest.phase))}
          </span>
          <span className="tnum break-all text-xs text-muted" title={latest.id}>
            {t("flow.jobs.jobId", { id: shortJobId(latest.id) })}
          </span>
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-5">
          <JobMetric label={t("flow.jobs.queuedAt")} value={formatDateTime(latest.queued_at)} />
          <JobMetric label={t("flow.jobs.startedAt")} value={formatDateTime(latest.started_at)} />
          <JobMetric
            label={t("flow.jobs.finishedAt")}
            value={formatDateTime(latest.finished_at)}
          />
          <JobMetric
            label={t("flow.jobs.attempt")}
            value={t("flow.jobs.attemptValue", {
              count: latest.attempt_count,
              max: latest.max_attempts,
            })}
          />
          <JobMetric
            label={t("flow.jobs.elapsed")}
            value={formatJobElapsed(latest, nowMs)}
            testId="ingestion-job-elapsed"
          />
        </dl>
        {active ? (
          <p className="mt-3 text-xs leading-relaxed text-info">{t("flow.jobs.activeHint")}</p>
        ) : null}
        {progressSummary ? <IngestionProgressSummaryView summary={progressSummary} /> : null}
        {latestErrorMessage ? (
          <div className="mt-3 rounded-md border border-danger/20 bg-danger-bg px-2.5 py-2 text-xs text-danger">
            <p className="font-medium text-danger">{t("flow.jobs.errorReason")}</p>
            <p className="mt-1 break-words text-danger/90">{latestErrorMessage}</p>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function IngestionProgressSummaryView({ summary }: { summary: IngestionProgressSummary }) {
  const label = ingestionProgressLabel(summary);
  return (
    <div className="mt-3 space-y-1.5 rounded-md border border-border bg-background px-3 py-2">
      <p className="text-xs font-medium text-foreground">{label}</p>
      {summary.kind === "determinate" ? (
        <progress
          className="h-2 w-full"
          value={summary.completed + summary.failed}
          max={summary.total}
          aria-label={label}
        />
      ) : (
        <progress className="h-2 w-full" aria-label={label} />
      )}
    </div>
  );
}

function ingestionProgressLabel(summary: IngestionProgressSummary): string {
  if (summary.kind === "indeterminate") {
    return t("flow.progress.indeterminate");
  }
  const unit = t(progressUnitLabelKey(summary.unit));
  if (summary.failed > 0) {
    return t("flow.progress.failed", {
      completed: summary.completed,
      failed: summary.failed,
      total: summary.total,
      unit,
    });
  }
  return t("flow.progress.determinate", {
    completed: summary.completed,
    total: summary.total,
    unit,
  });
}

function progressUnitLabelKey(unit: ProgressUnit): I18nKey {
  if (unit === "slide") return "flow.progress.unit.slide";
  if (unit === "sheet") return "flow.progress.unit.sheet";
  return "flow.progress.unit.page";
}

function segmentProgressLabel(segment: IngestionSegment): string {
  const start = segment.progress_start ?? segment.page_start;
  const end = segment.progress_end ?? segment.page_end ?? start;
  if (start == null || end == null || segment.progress_unit === "source") {
    return t("flow.segments.source");
  }
  if (segment.progress_unit === "slide") {
    return progressRangeLabel("flow.segments.slideSingle", "flow.segments.slideRange", start, end);
  }
  if (segment.progress_unit === "sheet") {
    return progressRangeLabel("flow.segments.sheetSingle", "flow.segments.sheetRange", start, end);
  }
  return progressRangeLabel("flow.segments.pageSingle", "flow.segments.pageRange", start, end);
}

function progressRangeLabel(
  singleKey: I18nKey,
  rangeKey: I18nKey,
  start: number,
  end: number
): string {
  return start === end
    ? t(singleKey, { number: start })
    : t(rangeKey, { start, end });
}

function JobMetric({
  label,
  value,
  testId,
}: {
  label: string;
  value: string;
  testId?: string;
}) {
  return (
    <div data-testid={testId}>
      <dt className="text-muted">{label}</dt>
      <dd className="tnum mt-0.5 font-medium text-foreground">{value}</dd>
    </div>
  );
}

function IngestionSegmentsPanel({
  segments,
  loading,
  error,
  retrying,
  visibleErrorSegmentIds,
  onRetryFailedSegments,
}: {
  segments: IngestionSegment[];
  loading: boolean;
  error: boolean;
  retrying: boolean;
  visibleErrorSegmentIds: ReadonlySet<string>;
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
      <ol
        aria-label={t("flow.segments.title")}
        className="bounded-scroll-area mt-3 grid grid-cols-1 gap-2 rounded-md border border-border bg-card/40 p-2 lg:grid-cols-2"
      >
        {segments.map((segment) => {
          const segmentErrorMessage = visibleErrorSegmentIds.has(segment.segment_id)
            ? normalizeIngestionErrorMessage(segment.error_message)
            : null;
          return (
            <li
              key={segment.segment_id}
              className="rounded-md border border-border bg-background p-3 text-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className={segmentStatusClass(segment.status)}>
                  {segmentStatusLabel(segment.status)}
                </span>
                <span className="tnum text-xs text-muted">
                  {segmentProgressLabel(segment)}
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
              {segmentErrorMessage ? (
                <div className="mt-2 space-y-1 rounded-md border border-danger/20 bg-danger-bg px-2.5 py-2 text-xs text-danger">
                  <p className="font-medium text-danger">{t("flow.segments.errorReason")}</p>
                  <p className="break-words text-danger/90">{segmentErrorMessage}</p>
                </div>
              ) : null}
              {segment.status === "FAILED" ? (
                <p className="mt-2 text-xs leading-relaxed text-muted">
                  {t("flow.segments.errorRecovery")}
                </p>
              ) : null}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function DocumentExtractionExportPanel({
  format,
  onFormatChange,
  content,
  loading,
  error,
  pageCount,
  elementCount,
  chunkCount,
}: {
  format: DocumentExtractionExportFormat;
  onFormatChange: (format: DocumentExtractionExportFormat) => void;
  content: string;
  loading: boolean;
  error: boolean;
  pageCount: number;
  elementCount: number;
  chunkCount: number;
}) {
  const formats: DocumentExtractionExportFormat[] = ["markdown", "html", "json", "chunks"];
  return (
    <section className="mt-4 rounded-lg border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h4 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <Braces size={15} className="text-primary" aria-hidden />
          {t("flow.extractionExport.title")}
        </h4>
        <div
          className="inline-flex flex-wrap rounded-md border border-border bg-card p-0.5"
          role="group"
          aria-label={t("flow.extractionExport.format")}
        >
          {formats.map((item) => (
            <button
              key={item}
              type="button"
              className={cn(
                "h-8 rounded px-2.5 text-xs font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                item === format
                  ? "bg-primary text-primary-foreground"
                  : "text-muted hover:bg-background hover:text-foreground"
              )}
              aria-pressed={item === format}
              onClick={() => onFormatChange(item)}
            >
              {extractionExportFormatLabel(item)}
            </button>
          ))}
        </div>
      </div>
      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <ExportMetric label={t("flow.extraction.stats.pages")} value={pageCount} />
        <ExportMetric label={t("flow.extraction.stats.elements")} value={elementCount} />
        <ExportMetric label={t("flow.extractionExport.chunks")} value={chunkCount} />
      </dl>
      {loading ? (
        <Skeleton className="mt-3 h-36 w-full rounded-md" />
      ) : error ? (
        <Banner severity="warning" title={t("flow.extractionExport.loadError")}>
          {t("flow.extractionExport.loadErrorHint")}
        </Banner>
      ) : (
        <pre className="mt-3 max-h-72 overflow-auto rounded-md border border-border bg-card p-3 text-xs leading-relaxed text-foreground">
          <code>{content || t("flow.extractionExport.empty")}</code>
        </pre>
      )}
    </section>
  );
}

function ExportMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-card px-2.5 py-2">
      <dt className="text-muted">{label}</dt>
      <dd className="tnum mt-1 font-semibold text-foreground">{value}</dd>
    </div>
  );
}

function extractionExportFormatLabel(format: DocumentExtractionExportFormat): string {
  if (format === "json") return t("flow.extractionExport.json");
  if (format === "html") return t("flow.extractionExport.html");
  if (format === "chunks") return t("flow.extractionExport.chunks");
  return t("flow.extractionExport.markdown");
}

function jobStatusKey(status: IngestionJob["status"]): I18nKey {
  switch (status) {
    case "QUEUED":
      return "upload.job.status.QUEUED";
    case "RUNNING":
      return "upload.job.status.RUNNING";
    case "SUCCEEDED":
      return "upload.job.status.SUCCEEDED";
    case "FAILED":
      return "upload.job.status.FAILED";
    case "SKIPPED":
      return "upload.job.status.SKIPPED";
    case "CANCELLED":
      return "upload.job.status.CANCELLED";
    default:
      return "upload.job.status.QUEUED";
  }
}

function jobPhaseKey(phase: IngestionJob["phase"]): I18nKey {
  if (phase === "INDEX") return "flow.jobs.phase.index";
  if (phase === "CHUNK") return "flow.jobs.phase.chunk";
  if (phase === "PREPROCESS") return "flow.jobs.phase.preprocess";
  return "flow.jobs.phase.extract";
}

function jobStatusClass(status: IngestionJob["status"]): string {
  const base = "rounded-full px-2 py-0.5 text-xs font-medium";
  switch (status) {
    case "QUEUED":
    case "RUNNING":
      return `${base} bg-info-bg text-info`;
    case "SUCCEEDED":
      return `${base} bg-success-bg text-success`;
    case "FAILED":
      return `${base} bg-danger-bg text-danger`;
    case "SKIPPED":
      return `${base} bg-warning-bg text-warning`;
    case "CANCELLED":
      return `${base} bg-background text-muted`;
    default:
      return `${base} bg-background text-muted`;
  }
}

function shortJobId(id: string): string {
  return id.length > 14 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

function formatJobElapsed(job: IngestionJob, nowMs = Date.now()): string {
  const startDate = parseApiDateTime(job.started_at ?? job.queued_at);
  const endDate = job.finished_at ? parseApiDateTime(job.finished_at) : null;
  const start = startDate?.getTime();
  const end = job.finished_at ? endDate?.getTime() : nowMs;
  if (start == null || end == null || Number.isNaN(end) || end < start) return "—";
  const seconds = Math.max(0, Math.round((end - start) / 1000));
  if (seconds < 60) return t("flow.jobs.elapsedSeconds", { seconds });
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest
    ? t("flow.jobs.elapsedMinutesSeconds", { minutes, seconds: rest })
    : t("flow.jobs.elapsedMinutes", { minutes });
}

/** インスペクタ右ペインのタブ(本文 / 構造化要素 / Chunk / エクスポート切替)。 */
function InspectorTab({
  id,
  controls,
  active,
  onSelect,
  children,
}: {
  id: string;
  controls: string;
  active: boolean;
  onSelect: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      role="tab"
      id={id}
      aria-selected={active}
      aria-controls={controls}
      tabIndex={active ? 0 : -1}
      onClick={onSelect}
      className={cn(
        "cursor-pointer rounded-full px-3 py-1 text-sm font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring",
        active
          ? "bg-primary text-primary-foreground"
          : "border border-border bg-card text-muted hover:bg-background hover:text-foreground"
      )}
    >
      {children}
    </button>
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
      <div className="rounded-md border border-border bg-background">
        <EmptyState title={t("flow.chunks.empty")} />
      </div>
    );
  }

  return (
    <ol className="space-y-3 rounded-lg border border-border bg-background p-3">
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
                <IndexBadge>#{chunk.chunk_index + 1}</IndexBadge>
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
                <div className="mt-2 flex">
                  <InfoChip icon={Route} label={chunk.section_path} />
                </div>
              ) : null}
              <div className="mt-2">
                <ExtractedText text={chunk.text} clamp />
              </div>
              <div className="mt-2 flex">
                <InfoChip
                  icon={ListTree}
                  label={
                    chunk.element_ids.length
                      ? chunk.element_ids.join(", ")
                      : t("flow.chunks.noElements")
                  }
                />
              </div>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function SourceDerivationPanel({
  derivation,
  originalFileName,
}: {
  derivation: SourceDerivationView;
  originalFileName: string;
}) {
  const pageCount = Object.keys(derivation.pageMap).length;
  return (
    <section className="rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <GitBranch size={16} className="text-primary" aria-hidden />
          {t("provenance.title")}
        </h3>
        <span className="rounded-full border border-border bg-card px-2 py-0.5 text-xs font-medium text-foreground">
          {derivation.converted
            ? t("provenance.converted")
            : t("provenance.passthrough")}
        </span>
      </div>
      {/* 原本 → 正規化原本 → 抽出 の系譜(溯源)。原本は保全され、変換物から追跡できる。 */}
      <ol className="mt-3 space-y-2 text-sm">
        <li className="rounded-md border border-border bg-card px-3 py-2">
          <div className="text-xs text-muted">{t("provenance.original")}</div>
          <div className="mt-0.5 break-all font-medium text-foreground">{originalFileName}</div>
          {derivation.sourceSha256 ? (
            <div className="tnum mt-0.5 break-all text-xs text-muted">
              sha256: {derivation.sourceSha256.slice(0, 16)}…
            </div>
          ) : null}
        </li>
        <li className="rounded-md border border-border bg-card px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-xs text-muted">{t("provenance.canonical")}</div>
            <span className="rounded bg-info-bg px-1.5 py-0.5 text-[11px] font-medium text-info">
              {t(`settings.preprocess.profile.${derivation.preprocessProfile}` as I18nKey)}
            </span>
          </div>
          {derivation.converted ? (
            <>
              <div className="mt-0.5 break-all font-medium text-foreground">
                {derivation.derivedObjectPath ?? derivation.derivedContentType ?? "-"}
              </div>
              <div className="tnum mt-0.5 break-all text-xs text-muted">
                {derivation.converterName} {derivation.converterVersion}
                {derivation.derivedSha256
                  ? ` · sha256: ${derivation.derivedSha256.slice(0, 16)}…`
                  : ""}
                {pageCount ? ` · ${t("provenance.pageMap")}: ${pageCount}` : ""}
              </div>
            </>
          ) : (
            <div className="mt-0.5 text-xs text-muted">{t("provenance.noConversion")}</div>
          )}
        </li>
      </ol>
      {derivation.warnings.length > 0 ? (
        <ul className="mt-3 space-y-1 text-xs text-warning">
          {derivation.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function SourceProfilePanel({
  profile,
  ingestionParser,
}: {
  profile: SourceProfile;
  ingestionParser: IngestionParserDisplay;
}) {
  const warnings = profile.quality_warnings ?? [];
  const ingestionBackend = ingestionParser.backend
    ? parserBackendLabel(ingestionParser.backend)
    : ingestionParser.source === "pending"
      ? t("sourceProfile.ingestionParser.pending")
      : t("sourceProfile.ingestionParser.unavailable");
  const ingestionProfile =
    ingestionParser.profile &&
    !isSameParserBackend(ingestionParser.profile, ingestionParser.backend)
      ? ingestionParser.profile
      : null;
  return (
    <section className="rounded-md border border-border bg-background p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <FileSearch size={16} className="text-primary" aria-hidden />
          {t("sourceProfile.documentWorkspaceTitle")}
        </h3>
        <span className="rounded-full border border-border bg-card px-2 py-0.5 text-xs font-medium text-foreground">
          {t(sourceModalityKey(profile.modality))}
        </span>
      </div>
      <dl className="mt-3 grid grid-cols-1 gap-3 text-sm sm:grid-cols-2 xl:grid-cols-6">
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.parserBackend")}</dt>
          <dd className="mt-0.5 font-medium text-foreground">
            {ingestionBackend}
          </dd>
          {ingestionProfile ? (
            <dd className="mt-0.5 break-all text-xs text-muted">{ingestionProfile}</dd>
          ) : null}
        </div>
        <div>
          <dt className="text-xs text-muted">{t("sourceProfile.parser")}</dt>
          <dd className="mt-0.5 font-medium text-foreground">
            {t(parserProfileKey(profile.parser_profile))}
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
      ) : null}
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
