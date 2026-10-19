import { EvidenceSourceSheet, evidencePreviewFocus } from "@production-ready/ui";

import { DocumentPreview } from "@/components/documents/DocumentPreview";
import type { RetrievedChunk } from "@/lib/api";
import { useDocument, useDocumentRecipes } from "@/lib/queries";
import { firstMetadataToken } from "@/lib/table-cell-focus";

import { citationEvidenceItem, EVIDENCE_LABELS } from "./citation-evidence";

/**
 * 根拠 1 件を右のサイドパネルで開く（#2360。Agent のチャットと同じ共通の `EvidenceSourceSheet`）。上部に根拠の
 * 要約（番号・文書名・引用の印・経路・スコア・抜粋・頁と章節）、その下に原文のプレビュー（該当箇所を強調する。
 * #349 / #442）を出す。根拠のカードの「根拠を表示」・回答本文の根拠の行（#657）から開く。
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
  return (
    <EvidenceSourceSheet
      open={open}
      onClose={onClose}
      item={chunk ? citationEvidenceItem(chunk) : null}
      index={index}
      labels={EVIDENCE_LABELS}
      kind={kind}
    >
      {chunk ? <CitationSourcePreview chunk={chunk} open={open} /> : null}
    </EvidenceSourceSheet>
  );
}

/**
 * 原文のプレビュー（Office 原本は変換済みの PDF。要素の表示領域を要素ごとに強調する。#349）。強調の計算は Agent の
 * 根拠のパネルと同じ共通の `evidencePreviewFocus`（#2389）。
 */
function CitationSourcePreview({ chunk, open }: { chunk: RetrievedChunk; open: boolean }) {
  const previewFileName = chunk.file_name ?? chunk.document_id;
  const recipeId = firstMetadataToken(chunk.metadata.recipe_id);
  // パネルを開いたときだけ文書詳細を取得し、Office 原本でも変換済 PDF を表示する。
  const previewDoc = useDocument(open ? chunk.document_id : null);
  const previewRecipes = useDocumentRecipes(open ? chunk.document_id : null);
  const previewRecipe = previewRecipes.data?.find((recipe) => recipe.recipe_id === recipeId);
  const focus = evidencePreviewFocus(chunk.metadata);
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
      focusPage={focus.focusPage}
      focusBbox={focus.focusBbox}
      focusBboxMode={focus.focusBboxMode}
      focusBboxUnit={focus.focusBboxUnit}
      focusPageSize={focus.focusPageSize}
      highlights={focus.highlights}
      className="min-h-0 flex-1"
    />
  );
}
