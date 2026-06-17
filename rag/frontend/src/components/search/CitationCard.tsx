import { FileText, LocateFixed, ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { ApiError, api, type CitationFeedbackRating, type RetrievedChunk } from "@/lib/api";
import {
  citationMetadataChips,
  firstCitationElementId,
  type CitationMetadataChip,
} from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

/** 引用チャンク1件の表示。retrieval 由来の score/metadata を併記。 */
export function CitationCard({
  chunk,
  index,
  traceId,
}: {
  chunk: RetrievedChunk;
  index: number;
  traceId?: string | null;
}) {
  const score = chunk.rerank_score ?? chunk.score;
  const chips = citationMetadataChips(chunk.metadata);
  const previewUrl = citationPreviewUrl(chunk);
  const [pendingRating, setPendingRating] = useState<CitationFeedbackRating | null>(null);
  const [submittedRating, setSubmittedRating] = useState<CitationFeedbackRating | null>(null);
  const canSubmitFeedback = Boolean(traceId);

  async function submitFeedback(rating: CitationFeedbackRating) {
    if (!traceId || pendingRating) return;
    setPendingRating(rating);
    try {
      await api.submitCitationFeedback({
        trace_id: traceId,
        document_id: chunk.document_id,
        chunk_id: chunk.chunk_id,
        rating,
        reason: rating === "not_helpful" ? "answer_untrusted" : null,
      });
      setSubmittedRating(rating);
      toast.success(t("search.citation.feedback.saved"));
    } catch (error) {
      toast.error(
        error instanceof ApiError ? error.message : t("search.citation.feedback.failed")
      );
    } finally {
      setPendingRating(null);
    }
  }

  return (
    <li className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
            {index + 1}
          </span>
          <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-foreground">
            <FileText size={14} className="shrink-0 text-muted" aria-hidden />
            <span className="truncate" title={chunk.file_name ?? chunk.document_id}>
              {chunk.file_name ?? chunk.document_id}
            </span>
          </span>
        </div>
        <span className="tnum shrink-0 rounded bg-background px-2 py-0.5 text-xs text-muted">
          {score.toFixed(3)}
        </span>
      </div>
      <p className="mt-2 line-clamp-4 whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground/90">
        {chunk.text}
      </p>
      {chips.length > 0 ? (
        <dl className="mt-3 flex flex-wrap gap-2">
          {chips.map((chip) => (
            <MetadataChip key={chip.id} chip={chip} />
          ))}
        </dl>
      ) : null}
      {chunk.category_name ? (
        <span className="mt-2 inline-block rounded-full bg-info-bg px-2 py-0.5 text-xs text-info">
          {chunk.category_name}
        </span>
      ) : null}
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <Link
          to={previewUrl}
          className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          aria-label={t("search.citation.openPreview", {
            file: chunk.file_name ?? chunk.document_id,
          })}
        >
          <LocateFixed size={15} aria-hidden />
          {t("search.citation.openPreviewShort")}
        </Link>
        <div
          className="flex justify-end gap-1"
          role="group"
          aria-label={t("search.citation.feedback.group")}
        >
          <Button
            type="button"
            variant={submittedRating === "helpful" ? "secondary" : "ghost"}
            size="sm"
            className={cn("size-8 px-0", submittedRating === "helpful" && "text-success")}
            aria-label={t("search.citation.feedback.helpful")}
            aria-pressed={submittedRating === "helpful"}
            title={t("search.citation.feedback.helpful")}
            disabled={!canSubmitFeedback || (pendingRating !== null && pendingRating !== "helpful")}
            loading={pendingRating === "helpful"}
            onClick={() => void submitFeedback("helpful")}
          >
            {pendingRating === "helpful" ? null : <ThumbsUp size={15} aria-hidden />}
          </Button>
          <Button
            type="button"
            variant={submittedRating === "not_helpful" ? "secondary" : "ghost"}
            size="sm"
            className={cn("size-8 px-0", submittedRating === "not_helpful" && "text-danger")}
            aria-label={t("search.citation.feedback.notHelpful")}
            aria-pressed={submittedRating === "not_helpful"}
            title={t("search.citation.feedback.notHelpful")}
            disabled={
              !canSubmitFeedback || (pendingRating !== null && pendingRating !== "not_helpful")
            }
            loading={pendingRating === "not_helpful"}
            onClick={() => void submitFeedback("not_helpful")}
          >
            {pendingRating === "not_helpful" ? null : <ThumbsDown size={15} aria-hidden />}
          </Button>
        </div>
      </div>
    </li>
  );
}

function citationPreviewUrl(chunk: RetrievedChunk): string {
  const params = new URLSearchParams({ chunk_id: chunk.chunk_id });
  const elementId = firstCitationElementId(chunk.metadata.element_ids);
  if (elementId) params.set("element_id", elementId);
  return `${APP_ROUTES.documents}/${encodeURIComponent(chunk.document_id)}?${params.toString()}`;
}

function MetadataChip({ chip }: { chip: CitationMetadataChip }) {
  return (
    <div className="min-w-0 max-w-full rounded-full border border-border bg-background px-2.5 py-1 text-xs text-muted sm:max-w-80">
      <dt className="sr-only">{chipLabel(chip)}</dt>
      <dd className="truncate">{chipValue(chip)}</dd>
    </div>
  );
}

function chipValue(chip: CitationMetadataChip): string {
  switch (chip.id) {
    case "page":
      return t("search.citation.page", { page: chip.value });
    case "content_kind":
      return t("search.citation.contentKindValue", {
        kind: contentKindLabel(chip.value),
      });
    case "section_title":
      return t("search.citation.sectionTitleValue", { title: chip.value });
    case "section_path":
      return t("search.citation.sectionPathValue", { path: chip.value });
    case "chunk_profile":
      return t("search.citation.profileValue", { profile: chip.value });
  }
}

function chipLabel(chip: CitationMetadataChip): string {
  switch (chip.id) {
    case "page":
      return t("search.citation.pageLabel");
    case "content_kind":
      return t("search.citation.contentKindLabel");
    case "section_title":
      return t("search.citation.sectionTitleLabel");
    case "section_path":
      return t("search.citation.sectionPathLabel");
    case "chunk_profile":
      return t("search.citation.profileLabel");
  }
}

function contentKindLabel(kind: string): string {
  switch (kind) {
    case "text":
      return t("search.filters.contentKind.text");
    case "list":
      return t("search.filters.contentKind.list");
    case "table":
      return t("search.filters.contentKind.table");
    case "figure":
      return t("search.filters.contentKind.figure");
    case "equation":
      return t("search.filters.contentKind.equation");
    case "code":
      return t("search.filters.contentKind.code");
    case "email":
      return t("search.filters.contentKind.email");
    case "slide":
      return t("search.filters.contentKind.slide");
    case "sheet":
      return t("search.filters.contentKind.sheet");
    default:
      return kind;
  }
}
