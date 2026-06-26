import type { IngestionSegment } from "@/lib/api";

type IngestionErrorDisplayPlan = {
  jobMessage: string | null;
  segmentIds: Set<string>;
  documentMessage: string | null;
  queuedJobMessage: string | null;
  retriedSegmentJobMessage: string | null;
};

export function resolveIngestionErrorDisplayPlan({
  latestJobErrorMessage,
  segments,
  documentErrorMessage,
  queuedJobErrorMessage,
  retriedSegmentJobErrorMessage,
}: {
  latestJobErrorMessage?: string | null;
  segments: Pick<IngestionSegment, "segment_id" | "status" | "error_message">[];
  documentErrorMessage?: string | null;
  queuedJobErrorMessage?: string | null;
  retriedSegmentJobErrorMessage?: string | null;
}): IngestionErrorDisplayPlan {
  const seen = new Set<string>();
  const take = (message?: string | null): string | null => {
    const normalized = normalizeIngestionErrorMessage(message);
    if (!normalized || seen.has(normalized)) return null;
    seen.add(normalized);
    return normalized;
  };

  const jobMessage = take(latestJobErrorMessage);
  const segmentIds = new Set<string>();
  for (const segment of segments) {
    if (segment.status === "FAILED" && take(segment.error_message)) {
      segmentIds.add(segment.segment_id);
    }
  }

  return {
    jobMessage,
    segmentIds,
    documentMessage: take(documentErrorMessage),
    queuedJobMessage: take(queuedJobErrorMessage),
    retriedSegmentJobMessage: take(retriedSegmentJobErrorMessage),
  };
}

export function normalizeIngestionErrorMessage(message?: string | null): string | null {
  const normalized = message?.trim();
  return normalized ? normalized : null;
}
