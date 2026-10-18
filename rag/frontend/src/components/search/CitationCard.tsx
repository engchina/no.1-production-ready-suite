import { Layers, LocateFixed } from "lucide-react";
import { useState } from "react";
import { Button, EvidenceCard, evidenceLabels, type EvidenceListItem, type EvidenceTag } from "@production-ready/ui";

import { FeedbackControls } from "@/components/feedback/FeedbackControls";
import type {
  FeedbackContentSnapshot,
  FeedbackSourceSurface,
  RetrievedChunk,
} from "@/lib/api";
import {
  citationSourceUrl,
  citationMetadataChips,
  citationAnswerUse,
  citationModelUsed,
  type CitationMetadataChip,
} from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";
import { integerMetadataValue } from "@/lib/table-cell-focus";

import { CitationPreviewDialog } from "./CitationPreviewDialog";

export { citationPreviewUrl } from "./CitationPreviewDialog";

/** 根拠の文言（3 製品で共通。#2328）。 */
export const EVIDENCE_LABELS = evidenceLabels();

/** 根拠のカードの操作の設定（チャット・RAG 検索・回答の記録・実行履歴で渡す）。 */
export interface CitationCardOptions {
  traceId?: string | null;
  searchAnswerProfileId?: string | null;
  sourceSurface?: FeedbackSourceSurface;
  messageId?: string | null;
  contentSnapshot?: FeedbackContentSnapshot | null;
  /**
   * 引用箇所のプレビューを出すか。文書を読む権限（backend の `_DOCUMENT_VIEW`）が無い利用者の画面
   * （実行履歴だけの利用者。#1890）では false にする（押すと 403 で権限なしの画面へ移るため）。
   */
  preview?: boolean;
}

/**
 * 引用チャンク1件の表示。retrieval 由来の score/metadata を併記する。見た目は 3 製品で共通の `EvidenceCard`
 * （#2328）で、ここは RAG の根拠（`RetrievedChunk`）をデータの契約に写し、引用箇所のプレビューと根拠の評価を足す。
 */
export function CitationCard({ chunk, index, ...options }: CitationCardOptions & { chunk: RetrievedChunk; index: number }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const preview = options.preview ?? true;
  return (
    <EvidenceCard
      item={citationEvidenceItem(chunk)}
      index={index}
      labels={EVIDENCE_LABELS}
      actions={<CitationActions chunk={chunk} {...options} onPreview={() => setPreviewOpen(true)} />}
    >
      {preview ? (
        <CitationPreviewDialog chunk={chunk} open={previewOpen} onClose={() => setPreviewOpen(false)} />
      ) : null}
    </EvidenceCard>
  );
}

/** 根拠のカードの操作の行（引用箇所の表示・根拠の評価）。 */
export function CitationActions({
  chunk,
  traceId,
  searchAnswerProfileId,
  sourceSurface = "search",
  messageId = null,
  contentSnapshot = null,
  preview = true,
  onPreview,
}: CitationCardOptions & { chunk: RetrievedChunk; onPreview: () => void }) {
  const previewFileName = chunk.file_name ?? chunk.document_id;
  return (
    <>
      {/* 引用箇所は画面を移動せずダイアログで見せる。文書の詳細はダイアログの中から別タブで開く（#442）。 */}
      {preview ? (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="secondary"
            icon={LocateFixed}
            onClick={onPreview}
            aria-label={t("search.citation.previewOpenLabel", { file: previewFileName })}
          >
            {t("search.citation.previewOpen")}
          </Button>
        </div>
      ) : null}
      {traceId && searchAnswerProfileId ? (
        <FeedbackControls
          traceId={traceId}
          searchAnswerProfileId={searchAnswerProfileId}
          targetType="citation"
          sourceSurface={sourceSurface}
          documentId={chunk.document_id}
          chunkId={chunk.chunk_id}
          messageId={messageId}
          contentSnapshot={contentSnapshot}
          compact
        />
      ) : null}
    </>
  );
}

/** RAG の根拠 → 共通の根拠のデータの契約（#2328）。 */
export function citationEvidenceItem(chunk: RetrievedChunk): EvidenceListItem {
  const recipeSlot = integerMetadataValue(chunk.metadata.recipe_slot_no);
  const tags: EvidenceTag[] = [];
  if (recipeSlot) tags.push({ id: "recipe", value: t("documents.recipes.name", { slot: recipeSlot }), icon: Layers });
  if (chunk.category_name) tags.push({ id: "category", value: chunk.category_name, tone: "info" });
  return {
    key: chunk.chunk_id,
    fileName: chunk.file_name ?? chunk.document_id,
    text: chunk.text,
    // AI に渡した箇所のうち、回答で引用したもの（#1208）。該当確認済みの記載と参考の記載を分ける（#2040）。
    usedInAnswer: citationModelUsed(chunk),
    answerUse: citationAnswerUse(chunk),
    // 新しい版に置き換えた文書（旧版）の根拠（#1248。include_superseded で含めたときだけ出る）。
    superseded: chunk.metadata.document_superseded === true,
    routeBadges: citationRetrievalBadges(chunk),
    score: chunk.score,
    rerankScore: chunk.rerank_score,
    chips: citationMetadataChips(chunk.metadata).map((chip) => ({
      id: chip.id,
      label: chipLabel(chip),
      value: chipValue(chip),
    })),
    tags,
    // 原典の URL（XML ツリー JSON の文書。#2024）。根拠を原典で確かめる。
    sourceUrl: citationSourceUrl(chunk.metadata),
  };
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
    badges.push(EVIDENCE_LABELS.evidenceBadge);
  }
  return badges;
}

/** chunk_id(document:chunk_set:index)から chunk_set(variant)id を取り出す。無ければ null。 */
export function variantIdFromChunkId(chunkId: string): string | null {
  const parts = chunkId.split(":");
  return parts.length === 3 ? parts[1] : null;
}

function chipValue(chip: CitationMetadataChip): string {
  switch (chip.id) {
    case "citation_label":
      return chip.value;
    case "page":
      // 印刷のページ番号があれば物理頁と並べる（「p.12（資料の表記: 3-4）」。#1287）。
      return chip.printed
        ? t("search.citation.pageWithLabel", { page: chip.value, label: chip.printed })
        : t("search.citation.page", { page: chip.value });
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
    case "citation_label":
      return t("search.citation.citationLabelLabel");
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
