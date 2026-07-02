import type {
  FileStatus,
  IngestionJobPhase,
  IngestionJobStatus,
  IngestionSegment,
} from "@/lib/api";
import type { I18nKey } from "@/lib/i18n";

export type DocumentPrimaryAction =
  | { kind: "enqueue"; phase: "PREPROCESS" }
  | { kind: "approve" }
  | { kind: "retry"; phase: IngestionJobPhase };

export type DocumentActionPlan = {
  primary: DocumentPrimaryAction | null;
  reprocessPhases: IngestionJobPhase[];
};

const NO_DOCUMENT_ACTIONS: DocumentActionPlan = {
  primary: null,
  reprocessPhases: [],
};

const PROCESSING_STATUSES: ReadonlySet<FileStatus> = new Set([
  "PREPROCESSING",
  "INGESTING",
  "CHUNKING",
  "INDEXING",
]);

const PHASE_LABEL_KEYS: Record<IngestionJobPhase, I18nKey> = {
  PREPROCESS: "flow.jobs.phase.preprocess",
  EXTRACT: "flow.jobs.phase.extract",
  CHUNK: "flow.jobs.phase.chunk",
  INDEX: "flow.jobs.phase.index",
};

const PHASE_STARTED_MESSAGE_KEYS: Record<IngestionJobPhase, I18nKey> = {
  PREPROCESS: "flow.phase.started.preprocess",
  EXTRACT: "flow.phase.started.extract",
  CHUNK: "flow.phase.started.chunk",
  INDEX: "flow.phase.started.index",
};

const PHASE_RUNNING_MESSAGE_KEYS: Record<IngestionJobPhase, I18nKey> = {
  PREPROCESS: "flow.phase.running.preprocess",
  EXTRACT: "flow.phase.running.extract",
  CHUNK: "flow.phase.running.chunk",
  INDEX: "flow.phase.running.index",
};

const PHASE_RETRY_LABEL_KEYS: Record<IngestionJobPhase, I18nKey> = {
  PREPROCESS: "flow.retry.preprocess",
  EXTRACT: "flow.retry.extract",
  CHUNK: "flow.retry.chunk",
  INDEX: "flow.retry.index",
};

export function phaseLabelKey(phase: IngestionJobPhase): I18nKey {
  return PHASE_LABEL_KEYS[phase];
}

export function phaseStartedMessageKey(phase: IngestionJobPhase): I18nKey {
  return PHASE_STARTED_MESSAGE_KEYS[phase];
}

export function phaseRunningMessageKey(phase: IngestionJobPhase): I18nKey {
  return PHASE_RUNNING_MESSAGE_KEYS[phase];
}

export function phaseRetryLabelKey(phase: IngestionJobPhase): I18nKey {
  return PHASE_RETRY_LABEL_KEYS[phase];
}

export function phaseForDocumentStatus(status: FileStatus): IngestionJobPhase | null {
  if (status === "PREPROCESSING") return "PREPROCESS";
  if (status === "INGESTING") return "EXTRACT";
  if (status === "CHUNKING") return "CHUNK";
  if (status === "INDEXING") return "INDEX";
  return null;
}

/** 文書状態と保存済み成果物から、画面に出してよい操作だけを解決する。 */
export function resolveDocumentActionPlan({
  status,
  activeJob,
  latestFailedPhase,
  hasPreparedArtifact,
  hasExtraction,
  hasChunkSet,
  hasSelectedRecipe = true,
}: {
  status: FileStatus;
  activeJob: boolean;
  latestFailedPhase?: IngestionJobPhase | null;
  hasPreparedArtifact: boolean;
  hasExtraction: boolean;
  hasChunkSet: boolean;
  hasSelectedRecipe?: boolean;
}): DocumentActionPlan {
  if (!hasSelectedRecipe || activeJob || PROCESSING_STATUSES.has(status)) {
    return NO_DOCUMENT_ACTIONS;
  }

  if (status === "UPLOADED") {
    return {
      primary: { kind: "enqueue", phase: "PREPROCESS" },
      reprocessPhases: [],
    };
  }

  if (status === "PREPROCESSED") {
    return hasPreparedArtifact
      ? {
          primary: { kind: "approve" },
          reprocessPhases: ["PREPROCESS"],
        }
      : {
          primary: { kind: "retry", phase: "PREPROCESS" },
          reprocessPhases: [],
        };
  }

  if (status === "REVIEW") {
    return {
      primary: { kind: "approve" },
      reprocessPhases: ["PREPROCESS", ...(hasPreparedArtifact ? (["EXTRACT"] as const) : [])],
    };
  }

  if (status === "CHUNKED" || status === "INDEXED") {
    return {
      primary: status === "CHUNKED" ? { kind: "approve" } : null,
      reprocessPhases: [
        "PREPROCESS",
        ...(hasPreparedArtifact ? (["EXTRACT"] as const) : []),
        ...(hasExtraction ? (["CHUNK"] as const) : []),
        ...(status === "INDEXED" && hasChunkSet ? (["INDEX"] as const) : []),
      ],
    };
  }

  if (status === "ERROR") {
    const retryPhase = resolveRetryPhase({
      failedPhase: latestFailedPhase,
      hasPreparedArtifact,
      hasExtraction,
      hasChunkSet,
    });
    return {
      primary: {
        kind: "retry",
        phase: retryPhase,
      },
      reprocessPhases: [
        ...(retryPhase !== "PREPROCESS" ? (["PREPROCESS"] as const) : []),
        ...(hasPreparedArtifact && (retryPhase === "CHUNK" || retryPhase === "INDEX")
          ? (["EXTRACT"] as const)
          : []),
        ...(hasExtraction && retryPhase === "INDEX" ? (["CHUNK"] as const) : []),
      ],
    };
  }

  return NO_DOCUMENT_ACTIONS;
}

function resolveRetryPhase({
  failedPhase,
  hasPreparedArtifact,
  hasExtraction,
  hasChunkSet,
}: {
  failedPhase?: IngestionJobPhase | null;
  hasPreparedArtifact: boolean;
  hasExtraction: boolean;
  hasChunkSet: boolean;
}): IngestionJobPhase {
  if (failedPhase === "INDEX" && hasChunkSet) return "INDEX";
  if ((failedPhase === "INDEX" || failedPhase === "CHUNK") && hasExtraction) return "CHUNK";
  if (
    (failedPhase === "INDEX" || failedPhase === "CHUNK" || failedPhase === "EXTRACT") &&
    hasPreparedArtifact
  ) {
    return "EXTRACT";
  }
  return "PREPROCESS";
}

export const INGESTION_PHASE_ORDER: readonly IngestionJobPhase[] = [
  "PREPROCESS",
  "EXTRACT",
  "CHUNK",
  "INDEX",
];

/**
 * 工程順(ファイル準備→抽出→Chunk 作成→Embedding/索引)に、各工程の最新ジョブを解決する。
 * jobs は既存 API と同じく新しい順で渡す前提(各工程の先頭一致 = 最新)。未実行工程は null。
 */
export function resolveLatestJobsByPhase<T extends { phase: IngestionJobPhase }>(
  jobs: T[]
): Array<{ phase: IngestionJobPhase; job: T | null }> {
  return INGESTION_PHASE_ORDER.map((phase) => ({
    phase,
    job: jobs.find((job) => job.phase === phase) ?? null,
  }));
}

export type IngestionParserDisplay = {
  backend: string | null;
  profile: string | null;
  source: "segment" | "extraction" | "pending" | "unavailable";
};

export type ProgressUnit = "page" | "slide" | "sheet";

export type IngestionProgressSummary =
  | {
      kind: "determinate";
      unit: ProgressUnit;
      completed: number;
      failed: number;
      total: number;
    }
  | { kind: "indeterminate" };

export function resolveIngestionParserDisplay({
  segments,
  extractionBackend,
  extractionProfile,
  loading,
}: {
  segments: Pick<IngestionSegment, "status" | "parser_backend" | "parser_profile">[];
  extractionBackend?: string | null;
  extractionProfile?: string | null;
  loading?: boolean;
}): IngestionParserDisplay {
  const segment = selectDisplaySegment(segments);
  if (segment) {
    return {
      backend: segment.parser_backend,
      profile: segment.parser_profile,
      source: "segment",
    };
  }
  if (extractionBackend || extractionProfile) {
    return {
      backend: extractionBackend ?? null,
      profile: extractionProfile ?? null,
      source: "extraction",
    };
  }
  return {
    backend: null,
    profile: null,
    source: loading ? "pending" : "unavailable",
  };
}

function selectDisplaySegment(
  segments: Pick<IngestionSegment, "status" | "parser_backend" | "parser_profile">[]
): Pick<IngestionSegment, "status" | "parser_backend" | "parser_profile"> | null {
  return (
    segments.find((segment) => segment.status === "RUNNING") ??
    segments.find((segment) => segment.status === "QUEUED") ??
    segments.find((segment) => segment.status === "FAILED") ??
    segments.find((segment) => segment.status === "SUCCEEDED") ??
    segments[0] ??
    null
  );
}

export function resolveIngestionProgressSummary(
  segments: Pick<
    IngestionSegment,
    "status" | "progress_unit" | "progress_start" | "progress_end"
  >[]
): IngestionProgressSummary | null {
  if (!segments.length) return null;
  const unit = segments.find(isDeterminateSegment)?.progress_unit as ProgressUnit | undefined;
  if (!unit) return { kind: "indeterminate" };
  const scoped = segments.filter(
    (segment) => segment.progress_unit === unit && isDeterminateSegment(segment)
  );
  const total = scoped.reduce((sum, segment) => sum + segmentSpan(segment), 0);
  if (total <= 0) return { kind: "indeterminate" };
  return {
    kind: "determinate",
    unit,
    completed: scoped
      .filter((segment) => segment.status === "SUCCEEDED")
      .reduce((sum, segment) => sum + segmentSpan(segment), 0),
    failed: scoped
      .filter((segment) => segment.status === "FAILED")
      .reduce((sum, segment) => sum + segmentSpan(segment), 0),
    total,
  };
}

function isDeterminateSegment(
  segment: Pick<IngestionSegment, "progress_unit" | "progress_start" | "progress_end">
): boolean {
  return (
    (segment.progress_unit === "page" ||
      segment.progress_unit === "slide" ||
      segment.progress_unit === "sheet") &&
    segment.progress_start != null &&
    segment.progress_end != null
  );
}

function segmentSpan(
  segment: Pick<IngestionSegment, "progress_start" | "progress_end">
): number {
  if (segment.progress_start == null || segment.progress_end == null) return 0;
  return Math.max(0, segment.progress_end - segment.progress_start + 1);
}

/**
 * 「このドキュメントは現在取込中です。」(409 競合)は取込が進行中の間だけ意味を持つ。
 * 取込完了後も react-query の mutation エラーが残り banner が消えないため、
 * 進行中ジョブが無くなったら stale とみなして消す。
 */
export function ingestConflictBannerIsStale({
  errorStatus,
  hasActiveJob,
}: {
  errorStatus: number | null | undefined;
  hasActiveJob: boolean;
}): boolean {
  return errorStatus === 409 && !hasActiveJob;
}

export function shouldShowProcessingWatchBanner({
  watchProcessing,
  documentStatus,
  latestJobStatus,
}: {
  watchProcessing: boolean;
  documentStatus: string | null | undefined;
  latestJobStatus: IngestionJobStatus | null | undefined;
}): boolean {
  return (
    watchProcessing &&
    latestJobStatus !== "FAILED" &&
    !["REVIEW", "CHUNKED", "INDEXED", "ERROR"].includes(documentStatus ?? "")
  );
}
