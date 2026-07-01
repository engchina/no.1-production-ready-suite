import { Eye, FileText, Layers, LocateFixed, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";

import { DocumentPreview } from "@/components/documents/DocumentPreview";
import { Button } from "@/components/ui/button";
import { ApiError, api, type CitationFeedbackRating, type RetrievedChunk } from "@/lib/api";
import { useDocument, useDocumentRecipes } from "@/lib/queries";
import {
  bboxCoordinateModeFromMetadata,
  bboxFromMetadata,
  bboxPageRotationFromMetadata,
  bboxPageSizeFromMetadata,
  bboxUnitFromMetadata,
  withBboxPageRotation,
} from "@/lib/bbox";
import {
  citationMetadataChips,
  firstCitationElementId,
  type CitationMetadataChip,
} from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";
import { firstMetadataToken, integerMetadataValue } from "@/lib/table-cell-focus";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";

/** 引用チャンク1件の表示。retrieval 由来の score/metadata を併記。 */
export function CitationCard({
  chunk,
  index,
  traceId,
  scoreMaxima,
}: {
  chunk: RetrievedChunk;
  index: number;
  traceId?: string | null;
  scoreMaxima?: CitationScoreMaxima;
}) {
  const maxima = scoreMaxima ?? { score: chunk.score, rerankScore: chunk.rerank_score ?? 0 };
  const chips = citationMetadataChips(chunk.metadata);
  const retrievalBadges = citationRetrievalBadges(chunk);
  const recipeId = firstMetadataToken(chunk.metadata.recipe_id);
  const recipeSlot = integerMetadataValue(chunk.metadata.recipe_slot_no);
  const previewUrl = citationPreviewUrl(chunk);
  const previewFileName = chunk.file_name ?? chunk.document_id;
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  // ドロワーを開いたときだけ文書詳細を取得し、Office 原本でも変換済 PDF を表示する。
  const previewDoc = useDocument(previewOpen ? chunk.document_id : null);
  const previewRecipes = useDocumentRecipes(previewOpen ? chunk.document_id : null);
  const previewRecipe = previewRecipes.data?.find((recipe) => recipe.recipe_id === recipeId);
  const focusPage = firstIntegerMetadata(chunk.metadata, ["page_start", "page"]);
  const focusBbox = bboxFromMetadata(chunk.metadata);
  const focusBboxMode = bboxCoordinateModeFromMetadata(chunk.metadata);
  const focusBboxUnit = bboxUnitFromMetadata(chunk.metadata);
  const focusPageSize = withBboxPageRotation(
    bboxPageSizeFromMetadata(chunk.metadata),
    bboxPageRotationFromMetadata(chunk.metadata)
  );
  const [pendingRating, setPendingRating] = useState<CitationFeedbackRating | null>(null);
  const [submittedRating, setSubmittedRating] = useState<CitationFeedbackRating | null>(null);
  const canSubmitFeedback = Boolean(traceId);

  function openPreview() {
    setPreviewOpen(true);
    dialogRef.current?.showModal();
  }
  function closePreview() {
    dialogRef.current?.close();
  }

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
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
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
          {retrievalBadges.length ? (
            <span className="flex flex-wrap gap-1">
              {retrievalBadges.map((badge) => (
                <span
                  key={badge}
                  className="rounded-full border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium leading-none text-muted"
                >
                  {badge}
                </span>
              ))}
            </span>
          ) : null}
        </div>
        <CitationScoreMeters chunk={chunk} maxima={maxima} />
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
      {recipeSlot ? (
        <span
          className="mt-2 mr-2 inline-flex items-center gap-1 rounded-full bg-muted/10 px-2 py-0.5 text-xs text-muted"
        >
          <Layers size={11} aria-hidden />
          {t("documents.recipes.name", { slot: recipeSlot })}
        </span>
      ) : null}
      {chunk.category_name ? (
        <span className="mt-2 inline-block rounded-full bg-info-bg px-2 py-0.5 text-xs text-info">
          {chunk.category_name}
        </span>
      ) : null}
      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={openPreview}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
          >
            <Eye size={15} aria-hidden />
            {t("search.citation.previewOpen")}
          </button>
          <Link
            to={previewUrl}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-border bg-card px-3 text-sm font-medium text-foreground transition-colors hover:bg-background focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            aria-label={t("search.citation.openPreview", {
              file: previewFileName,
            })}
          >
            <LocateFixed size={15} aria-hidden />
            {t("search.citation.openPreviewShort")}
          </Link>
        </div>
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
      <dialog
        ref={dialogRef}
        onClose={() => setPreviewOpen(false)}
        onClick={(event) => {
          if (event.target === dialogRef.current) closePreview();
        }}
        aria-label={t("search.citation.openPreview", { file: previewFileName })}
        className="m-auto w-[min(92vw,900px)] max-h-[85vh] overflow-auto rounded-lg border border-border bg-card p-0 text-foreground backdrop:bg-black/50"
      >
        {previewOpen ? (
          <div className="flex flex-col">
            <div className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-border bg-card px-4 py-3">
              <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-foreground">
                <FileText size={14} className="shrink-0 text-muted" aria-hidden />
                <span className="truncate" title={previewFileName}>
                  {previewFileName}
                </span>
              </span>
              <div className="flex shrink-0 items-center gap-2">
                <Link
                  to={previewUrl}
                  className="text-xs font-medium text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  {t("search.citation.previewFullpage")}
                </Link>
                <button
                  type="button"
                  onClick={closePreview}
                  aria-label={t("search.citation.previewClose")}
                  className="inline-flex size-8 items-center justify-center rounded-md text-muted transition-colors hover:bg-background hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                >
                  <X size={16} aria-hidden />
                </button>
              </div>
            </div>
            <div className="p-4">
              <DocumentPreview
                documentId={chunk.document_id}
                recipeId={recipeId}
                fileName={previewFileName}
                preparedArtifact={
                  recipeId
                    ? (previewRecipe?.preprocess_artifact ?? null)
                    : (previewDoc.data?.preprocess_artifact ?? null)
                }
                focusPage={focusPage}
                focusBbox={focusBbox}
                focusBboxMode={focusBboxMode}
                focusBboxUnit={focusBboxUnit}
                focusPageSize={focusPageSize}
              />
            </div>
          </div>
        ) : null}
      </dialog>
    </li>
  );
}

export interface CitationScoreMaxima {
  score: number;
  rerankScore: number;
}

/** 引用一覧から retrieval / rerank スコアの最大値を求める(スコアメータの基準)。 */
export function scoreMaximaForCitations(citations: RetrievedChunk[]): CitationScoreMaxima {
  return {
    score: maxScore(citations.map((chunk) => chunk.score)),
    rerankScore: maxScore(
      citations.flatMap((chunk) => (chunk.rerank_score == null ? [] : [chunk.rerank_score]))
    ),
  };
}

function maxScore(values: number[]): number {
  return values.reduce((max, value) => (Number.isFinite(value) && value > max ? value : max), 0);
}

function citationRetrievalBadges(chunk: RetrievedChunk): string[] {
  const vectorRank = integerMetadataValue(chunk.metadata.vector_rank);
  const keywordRank = integerMetadataValue(chunk.metadata.keyword_rank);
  const rerankRank = integerMetadataValue(chunk.metadata.rerank_rank);
  const badges: string[] = [];
  if (vectorRank != null && keywordRank != null) badges.push(t("search.citation.badge.both"));
  if (vectorRank != null) badges.push(t("search.citation.badge.vector", { rank: vectorRank }));
  if (keywordRank != null) badges.push(t("search.citation.badge.keyword", { rank: keywordRank }));
  if (rerankRank != null) badges.push(t("search.citation.badge.rerank", { rank: rerankRank }));
  if (chunk.metadata.context_role === "evidence" || badges.length > 0) {
    badges.push(t("search.citation.badge.evidence"));
  }
  return badges;
}

function CitationScoreMeters({
  chunk,
  maxima,
}: {
  chunk: RetrievedChunk;
  maxima: CitationScoreMaxima;
}) {
  return (
    <div className="w-full space-y-1.5 rounded-md bg-background px-2.5 py-2 sm:w-56">
      <ScoreMeter
        label={t("search.citation.score.retrieval")}
        value={chunk.score}
        max={maxima.score}
        tone="primary"
      />
      <ScoreMeter
        label={t("search.citation.score.rerank")}
        value={chunk.rerank_score}
        max={maxima.rerankScore}
        tone="success"
      />
    </div>
  );
}

function ScoreMeter({
  label,
  value,
  max,
  tone,
}: {
  label: string;
  value: number | null;
  max: number;
  tone: "primary" | "success";
}) {
  const valueText = formatScoreValue(value);
  const ariaMax = Number.isFinite(max) && max > 0 ? max : 1;
  const ariaNow = value == null || !Number.isFinite(value) ? 0 : Math.min(Math.max(value, 0), ariaMax);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-[11px]">
        <span className="font-medium text-muted">{label}</span>
        <span className="tnum shrink-0 text-foreground">{valueText}</span>
      </div>
      <div
        role="meter"
        aria-label={t("search.citation.score.meter", { label, value: valueText })}
        aria-valuemin={0}
        aria-valuemax={ariaMax}
        aria-valuenow={ariaNow}
        aria-valuetext={valueText}
        className="h-1.5 overflow-hidden rounded-full bg-muted/20"
      >
        <div
          className={cn(
            "h-full rounded-full",
            tone === "primary" ? "bg-primary" : "bg-success"
          )}
          style={{ width: `${scoreMeterPercent(value, max)}%` }}
        />
      </div>
    </div>
  );
}

function formatScoreValue(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return t("search.citation.score.rerankMissing");
  return value.toFixed(3);
}

export function scoreMeterPercent(value: number | null, max: number): number {
  if (value == null || !Number.isFinite(value) || !Number.isFinite(max) || value <= 0 || max <= 0) {
    return 0;
  }
  return Math.min(100, (value / max) * 100);
}

export function citationPreviewUrl(chunk: RetrievedChunk): string {
  const params = new URLSearchParams({ chunk_id: chunk.chunk_id });
  const recipeId = firstMetadataToken(chunk.metadata.recipe_id);
  if (recipeId) params.set("recipe", recipeId);
  const page = firstIntegerMetadata(chunk.metadata, ["page_start", "page"]);
  if (page != null) params.set("page", String(page));
  const bbox = bboxFromMetadata(chunk.metadata);
  if (bbox) params.set("bbox", bbox.map(compactNumber).join(","));
  const bboxMode = bboxCoordinateModeFromMetadata(chunk.metadata);
  if (bboxMode) params.set("bbox_mode", bboxMode);
  const bboxUnit = bboxUnitFromMetadata(chunk.metadata);
  if (bboxUnit) params.set("bbox_unit", bboxUnit);
  const pageSize = bboxPageSizeFromMetadata(chunk.metadata);
  if (pageSize?.width && pageSize?.height) {
    params.set("page_width", compactNumber(pageSize.width));
    params.set("page_height", compactNumber(pageSize.height));
  }
  const pageRotation = pageSize?.rotation ?? bboxPageRotationFromMetadata(chunk.metadata);
  if (pageRotation != null) params.set("page_rotation", String(pageRotation));
  const elementId = firstCitationElementId(chunk.metadata.element_ids);
  if (elementId) params.set("element_id", elementId);
  const tableId = firstTableId(chunk.metadata);
  const formulaCellRef = firstFormulaCellRef(chunk.metadata);
  const cellRef = firstCellRef(chunk.metadata);
  const row = firstIntegerMetadata(chunk.metadata, ["table_cell_row", "cell_row", "row"]);
  const col = firstIntegerMetadata(chunk.metadata, ["table_cell_col", "cell_col", "col"]);
  if (cellRef || row != null || col != null) {
    if (tableId) params.set("table_id", tableId);
    if (cellRef) params.set("cell_ref", cellRef);
    if (formulaCellRef) params.set("formula_cell_ref", formulaCellRef);
    if (row != null) params.set("cell_row", String(row));
    if (col != null) params.set("cell_col", String(col));
  }
  return `${APP_ROUTES.documents}/${encodeURIComponent(chunk.document_id)}?${params.toString()}`;
}

/** chunk_id(document:chunk_set:index)から chunk_set(variant)id を取り出す。無ければ null。 */
export function variantIdFromChunkId(chunkId: string): string | null {
  const parts = chunkId.split(":");
  return parts.length === 3 ? parts[1] : null;
}

function compactNumber(value: number): string {
  return String(Number(value.toFixed(6)));
}

function firstTableId(metadata: RetrievedChunk["metadata"]): string | null {
  return firstMetadataToken(metadata.table_id ?? metadata.parent_table_id, {
    preferTableId: true,
  });
}

function firstFormulaCellRef(metadata: RetrievedChunk["metadata"]): string | null {
  return firstMetadataToken(metadata.formula_cell_refs ?? metadata.formula_cell_ref);
}

function firstCellRef(metadata: RetrievedChunk["metadata"]): string | null {
  return firstMetadataToken(
    metadata.formula_cell_refs ??
      metadata.formula_cell_ref ??
      metadata.table_cell_refs ??
      metadata.cell_refs ??
      metadata.table_cell_ref ??
      metadata.cell_ref
  );
}

function firstIntegerMetadata(
  metadata: RetrievedChunk["metadata"],
  keys: string[]
): number | null {
  for (const key of keys) {
    const value = integerMetadataValue(metadata[key]);
    if (value != null) return value;
  }
  return null;
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
