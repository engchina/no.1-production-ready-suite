import { FileText, Layers, LocateFixed } from "lucide-react";
import { useState } from "react";
import { Button } from "@engchina/production-ready-ui";

import { FeedbackControls } from "@/components/feedback/FeedbackControls";
import type {
  FeedbackContentSnapshot,
  FeedbackSourceSurface,
  RetrievedChunk,
} from "@/lib/api";
import { citationMetadataChips, type CitationMetadataChip } from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";
import { integerMetadataValue } from "@/lib/table-cell-focus";

import { CitationPreviewDialog } from "./CitationPreviewDialog";

export { citationPreviewUrl } from "./CitationPreviewDialog";

/** 引用チャンク1件の表示。retrieval 由来の score/metadata を併記。 */
export function CitationCard({
  chunk,
  index,
  traceId,
  businessViewId,
  sourceSurface = "search",
  messageId = null,
  contentSnapshot = null,
}: {
  chunk: RetrievedChunk;
  index: number;
  traceId?: string | null;
  businessViewId?: string | null;
  sourceSurface?: FeedbackSourceSurface;
  messageId?: string | null;
  contentSnapshot?: FeedbackContentSnapshot | null;
}) {
  const chips = citationMetadataChips(chunk.metadata);
  const retrievalBadges = citationRetrievalBadges(chunk);
  const recipeSlot = integerMetadataValue(chunk.metadata.recipe_slot_no);
  const previewFileName = chunk.file_name ?? chunk.document_id;
  const [previewOpen, setPreviewOpen] = useState(false);

  const hasMetadata = chips.length > 0 || recipeSlot != null || Boolean(chunk.category_name);

  return (
    <li className="rounded-lg border border-border bg-surface p-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_176px] sm:items-start">
        <div data-testid="citation-main" className="min-w-0">
          <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1.5">
            <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-accent-subtle text-xs font-semibold text-accent-fg">
              {index + 1}
            </span>
            <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-fg">
              <FileText size={14} className="shrink-0 text-fg-muted" aria-hidden />
              <span className="truncate" title={chunk.file_name ?? chunk.document_id}>
                {chunk.file_name ?? chunk.document_id}
              </span>
            </span>
            {retrievalBadges.length ? (
              <span className="flex flex-wrap gap-1">
                {retrievalBadges.map((badge) => (
                  <span
                    key={badge}
                    className="rounded-full border border-border bg-surface-sunken px-1.5 py-0.5 text-xs font-medium leading-none text-fg-muted"
                  >
                    {badge}
                  </span>
                ))}
              </span>
            ) : null}
          </div>
          <p
            data-testid="citation-text"
            className="mt-2.5 line-clamp-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-fg/90"
          >
            {chunk.text}
          </p>
          {hasMetadata ? (
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {chips.length > 0 ? (
                <dl className="flex flex-wrap gap-1.5">
                  {chips.map((chip) => (
                    <MetadataChip key={chip.id} chip={chip} />
                  ))}
                </dl>
              ) : null}
              {recipeSlot ? (
                <span className="inline-flex items-center gap-1 rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs text-fg-muted">
                  <Layers size={14} aria-hidden />
                  {t("documents.recipes.name", { slot: recipeSlot })}
                </span>
              ) : null}
              {chunk.category_name ? (
                <span className="inline-flex rounded-full bg-info-subtle px-2 py-0.5 text-xs text-info-fg">
                  {chunk.category_name}
                </span>
              ) : null}
            </div>
          ) : null}
        </div>
        <CitationScores chunk={chunk} />
      </div>
      <div className="mt-2.5 flex flex-col gap-2 border-t border-border pt-2.5 sm:flex-row sm:items-center sm:justify-between">
        {/* 引用箇所は画面を移動せずダイアログで見せる。文書の詳細はダイアログの中から別タブで開く（#442）。 */}
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            icon={LocateFixed}
            onClick={() => setPreviewOpen(true)}
            aria-label={t("search.citation.previewOpenLabel", { file: previewFileName })}
          >
            {t("search.citation.previewOpen")}
          </Button>
        </div>
        {traceId && businessViewId ? (
          <FeedbackControls
            traceId={traceId}
            businessViewId={businessViewId}
            targetType="citation"
            sourceSurface={sourceSurface}
            documentId={chunk.document_id}
            chunkId={chunk.chunk_id}
            messageId={messageId}
            contentSnapshot={contentSnapshot}
            compact
          />
        ) : null}
      </div>
      <CitationPreviewDialog chunk={chunk} open={previewOpen} onClose={() => setPreviewOpen(false)} />
    </li>
  );
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

function CitationScores({ chunk }: { chunk: RetrievedChunk }) {
  return (
    <div
      data-testid="citation-score-panel"
      className="w-full space-y-2 rounded-md bg-surface-sunken px-2.5 py-2 sm:w-[12.57rem] sm:shrink-0"
    >
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-fg-muted">{t("search.citation.score.retrieval")}</span>
        <span className="tnum shrink-0 text-fg">{formatScoreValue(chunk.score)}</span>
      </div>
      <ScoreMeter label={t("search.citation.score.rerank")} value={chunk.rerank_score} />
    </div>
  );
}

function ScoreMeter({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  if (value == null || !Number.isFinite(value)) {
    return (
      <p className="text-xs font-medium text-fg-muted">
        {t("search.citation.score.rerankMissing")}
      </p>
    );
  }
  const valueText = formatScoreValue(value);
  const ariaNow = Math.min(Math.max(value, 0), 1);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-medium text-fg-muted">{label}</span>
        <span className="tnum shrink-0 text-fg">{valueText}</span>
      </div>
      <div
        role="meter"
        aria-label={t("search.citation.score.meter", { label, value: valueText })}
        aria-valuemin={0}
        aria-valuemax={1}
        aria-valuenow={ariaNow}
        aria-valuetext={valueText}
        className="h-1.5 overflow-hidden rounded-full bg-surface-hover"
      >
        <div
          data-testid="citation-rerank-fill"
          className="h-full rounded-full bg-success-emphasis"
          style={{ width: `${scoreMeterPercent(value)}%` }}
        />
      </div>
    </div>
  );
}

function formatScoreValue(value: number): string {
  return Number.isFinite(value) ? value.toFixed(3) : "—";
}

export function scoreMeterPercent(value: number | null): number {
  if (value == null || !Number.isFinite(value) || value <= 0) {
    return 0;
  }
  return Math.min(100, value * 100);
}

/** chunk_id(document:chunk_set:index)から chunk_set(variant)id を取り出す。無ければ null。 */
export function variantIdFromChunkId(chunkId: string): string | null {
  const parts = chunkId.split(":");
  return parts.length === 3 ? parts[1] : null;
}

function MetadataChip({ chip }: { chip: CitationMetadataChip }) {
  return (
    <div className="min-w-0 max-w-full rounded-full border border-border bg-surface-sunken px-2 py-0.5 text-xs text-fg-muted sm:max-w-80">
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
    case "field":
      return t("search.filters.contentKind.field");
    case "section_summary":
      return t("search.filters.contentKind.section_summary");
    default:
      return kind;
  }
}
