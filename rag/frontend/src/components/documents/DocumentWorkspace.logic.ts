import type { IngestionJobStatus, IngestionSegment } from "@/lib/api";

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
