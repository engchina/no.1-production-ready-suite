import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";
import { EvidenceSourceSheet } from "@production-ready/ui";

import { DocumentPreview } from "@/components/documents/DocumentPreview";
import { useAuth } from "@/components/security/AuthProvider";
import type { RetrievedChunk } from "@/lib/api";
import {
  bboxCoordinateModeFromMetadata,
  bboxFromMetadata,
  bboxPageRotationFromMetadata,
  bboxPageSizeFromMetadata,
  bboxUnitFromMetadata,
  buildPreviewHighlights,
  displayRegionsFromMetadata,
  withBboxPageRotation,
} from "@/lib/bbox";
import { firstCitationElementId } from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";
import { useDocument, useDocumentRecipes } from "@/lib/queries";
import { canOpenDocumentDetail } from "@/lib/route-permissions";
import { APP_ROUTES } from "@/lib/routes";
import { firstMetadataToken, integerMetadataValue } from "@/lib/table-cell-focus";

import { citationEvidenceItem, EVIDENCE_LABELS } from "./citation-evidence";

/**
 * 根拠 1 件を右のサイドパネルで開く（#2360。Agent のチャットと同じ共通の `EvidenceSourceSheet`）。上部に根拠の
 * 要約（番号・文書名・引用の印・経路・スコア・抜粋・頁と章節）、その下に原文のプレビュー（該当箇所を強調する。
 * #349 / #442）を出す。引用カードの「引用箇所を表示」・まとまりの箇所・回答本文の根拠の行（#657）から開く。
 * `chunk` は閉じる間も前の根拠を渡す（`useEvidenceSource` の `shown`）。
 */
export function CitationSourceSheet({
  chunk,
  index,
  open,
  onClose,
  kind = "answer",
}: {
  chunk: RetrievedChunk | null;
  /** 根拠の番号（0 始まり）。 */
  index: number;
  open: boolean;
  onClose: () => void;
  /** 回答の根拠（既定）か、回答を作らない検索の結果の箇所か（見出しが変わる）。 */
  kind?: "answer" | "search";
}) {
  // 文書の詳細を開けない利用者（検索・チャット・KB だけ）には詳細へのリンクを出さない（#303）。
  const canOpenDetail = canOpenDocumentDetail(useAuth().hasPermission);
  return (
    <EvidenceSourceSheet
      open={open}
      onClose={onClose}
      item={chunk ? citationEvidenceItem(chunk) : null}
      index={index}
      labels={EVIDENCE_LABELS}
      kind={kind}
      actions={
        chunk && canOpenDetail ? (
          <Link
            to={citationPreviewUrl(chunk)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-xs font-medium text-accent-fg hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
          >
            {t("search.citation.openDetail")}
            <ExternalLink size={14} aria-hidden />
          </Link>
        ) : null
      }
    >
      {chunk ? <CitationSourcePreview chunk={chunk} open={open} /> : null}
    </EvidenceSourceSheet>
  );
}

/** 原文のプレビュー（Office 原本は変換済みの PDF。要素の表示領域を要素ごとに強調する。#349）。 */
function CitationSourcePreview({ chunk, open }: { chunk: RetrievedChunk; open: boolean }) {
  const previewFileName = chunk.file_name ?? chunk.document_id;
  const recipeId = firstMetadataToken(chunk.metadata.recipe_id);
  // パネルを開いたときだけ文書詳細を取得し、Office 原本でも変換済 PDF を表示する。
  const previewDoc = useDocument(open ? chunk.document_id : null);
  const previewRecipes = useDocumentRecipes(open ? chunk.document_id : null);
  const previewRecipe = previewRecipes.data?.find((recipe) => recipe.recipe_id === recipeId);
  const focusPage = firstIntegerMetadata(chunk.metadata, ["page_start", "page"]);
  const focusBbox = bboxFromMetadata(chunk.metadata);
  const focusBboxMode = bboxCoordinateModeFromMetadata(chunk.metadata);
  const focusBboxUnit = bboxUnitFromMetadata(chunk.metadata);
  const focusPageSize = withBboxPageRotation(
    bboxPageSizeFromMetadata(chunk.metadata),
    bboxPageRotationFromMetadata(chunk.metadata)
  );
  // 回答の根拠でも、要素の表示領域（要素ごとの bbox）を要素ごとに強調する（#349）。
  const previewHighlights = buildPreviewHighlights({
    focusPage,
    focusBbox,
    focusBboxMode,
    focusBboxUnit,
    focusPageSize,
    regions: displayRegionsFromMetadata(chunk.metadata),
  });
  return (
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
      highlights={previewHighlights}
      className="min-h-0 flex-1"
    />
  );
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

export function firstIntegerMetadata(
  metadata: RetrievedChunk["metadata"],
  keys: string[]
): number | null {
  for (const key of keys) {
    const value = integerMetadataValue(metadata[key]);
    if (value != null) return value;
  }
  return null;
}
