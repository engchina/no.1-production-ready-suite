import { useCallback, type ReactNode } from "react";
import { EvidenceSourceSheet, EvidenceReader, Skeleton, TimedLoadingState, FormStatus, SourceVersionStaleNotice, isSourceVersionStale, evidencePreviewFocus, type OriginalSourceInput, type EvidenceReadingInput } from "@production-ready/ui";

import { ApiErrorState } from "@/components/StateViews";
import { DocumentPreview } from "@/components/documents/DocumentPreview";
import type { CitationPosition } from "@/lib/answer-text";
import { api, type RetrievedChunk } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useDocumentPreviewSource } from "@/lib/queries";
import { firstMetadataToken } from "@/lib/table-cell-focus";

import { citationEvidenceItem, EVIDENCE_LABELS } from "./citation-evidence";

/**
 * 根拠 1 件を右のサイドパネルで開く（#2360。Agent のチャットと同じ共通の `EvidenceSourceSheet`）。上部に根拠の
 * 要約（番号・文書名・引用の印・経路・スコア・抜粋・頁と章節）、その下に原文のプレビュー（該当箇所を強調する。
 * #349 / #442）を出す。根拠のカードの「根拠を表示」・回答本文の根拠の行（#657）から開く。
 * `chunk` は閉じる間も前の根拠を渡す（`useEvidenceSource` の `shown`）。
 *
 * 本文の根拠の行が指す箇所を確定できないとき（`position` が `unresolved`。#2710）は、要約の上に「位置は確定して
 * いない」ことと候補の数を出し、原文のプレビューは頁へ移るだけで箇所を強調しない（別の箇所を正しい位置に見せない）。
 */
export function CitationSourceSheet({
  chunk,
  index,
  open,
  onClose,
  kind = "answer",
  position = { kind: "exact" },
  empty,
  scores,
  showScores,
  title,
}: {
  chunk: RetrievedChunk | null;
  /** 原文表示を開く元に合わせた見出し。未指定は既存の回答/検索の見出し。 */
  title?: string;
  /** 根拠の位置を取得中、または現在は閲覧できない場合の状態。 */
  empty?: ReactNode;
  /** 位置だけを ID で読んだ場合は、保存時のスコアだけを表示する。 */
  scores?: { score: number | null; rerankScore: number | null };
  /** 検索しない原文入口ではスコア領域を表示しない。既定は共通部品の true。 */
  showScores?: boolean;
  /** 根拠の番号（0 始まり）。 */
  index: number;
  open: boolean;
  onClose: () => void;
  /** 回答の根拠（既定）か、回答を作らない検索の結果の箇所か（見出しが変わる）。 */
  kind?: "answer" | "search";
  /** 本文の根拠の行から開いたときの、箇所の位置の決まり方（#2710）。既定は確定（根拠の一覧から開く）。 */
  position?: CitationPosition;
}) {
  const unresolved = position.kind === "unresolved" ? position : null;
  return (
    <EvidenceSourceSheet
      open={open}
      onClose={onClose}
      item={chunk ? { ...citationEvidenceItem(chunk), ...scores } : null}
      index={index}
      labels={title ? {...EVIDENCE_LABELS, sourceTitle: title, sourceTitleSearch: title} : EVIDENCE_LABELS}
      kind={kind}
      showScores={showScores}
      notice={
        unresolved
          ? {
              title: t("search.citationPosition.unresolvedTitle"),
              body: t(
                unresolved.reason === "legacy"
                  ? "search.citationPosition.legacyBody"
                  : "search.citationPosition.candidatesBody",
                { count: unresolved.candidates.length },
              ),
            }
          : undefined
      }
    >
      {chunk ? <CitationSourcePreview chunk={chunk} open={open} highlight={!unresolved} /> : empty}
    </EvidenceSourceSheet>
  );
}

/**
 * 原文のプレビュー（Office 原本は変換済みの PDF。要素の表示領域を要素ごとに強調する。#349。テキストの原本は抜粋の
 * 箇所を強調する。#2512）。強調の計算は Agent の根拠のパネルと同じ共通の `evidencePreviewFocus`（#2389）。
 */
function CitationSourcePreview({
  chunk,
  open,
  highlight,
}: {
  chunk: RetrievedChunk;
  open: boolean;
  /** 箇所を強調するか。位置の決まらない根拠の行から開いたときは頁へ移るだけにする（#2710）。 */
  highlight: boolean;
}) {
  const loadWindow = useCallback((input: EvidenceReadingInput, signal: AbortSignal) => api.documentReader(chunk.document_id, chunk.chunk_id, input, signal), [chunk.document_id, chunk.chunk_id]);
  const loadOriginal = useCallback((input: OriginalSourceInput, signal: AbortSignal) => api.documentSourceMap(chunk.document_id, chunk.chunk_id, input, signal), [chunk.document_id, chunk.chunk_id]);
  const previewFileName = chunk.file_name ?? chunk.document_id;
  const recipeId = firstMetadataToken(chunk.metadata.recipe_id);
  // 切替の後も、引用した版のファイルの頁を出す（bbox はその版のファイルの座標。#4168）。
  const chunkSetId = firstMetadataToken(chunk.metadata.chunk_set_id);
  const office = /\.(?:docx?|pptx?|xlsx?)$/i.test(previewFileName);
  const source = useDocumentPreviewSource(open && office ? chunk.document_id : null, recipeId, chunkSetId);
  const focus = evidencePreviewFocus(chunk.metadata);
  if (/\.(?:txt|csv|tsv|md|json|jsonl|xml|html?|eml|log)$/i.test(previewFileName)) {
    return <EvidenceReader key={`${chunk.document_id}:${chunk.chunk_id}`} readingKey={`${chunk.document_id}:${chunk.chunk_id}`} loadWindow={loadWindow} loadOriginal={/\.json$/i.test(previewFileName) ? loadOriginal : undefined} enabled={open} highlight={highlight} downloadUrl={recipeId ? api.documentRecipeContentUrl(chunk.document_id, recipeId) : api.documentContentUrl(chunk.document_id)} />;
  }
  if (office) {
    if (!recipeId || !chunkSetId) return <FormStatus tone="warning" message={t("search.previewSource.missing")} />;
    if (source.isPending) return <TimedLoadingState label={t("search.previewSource.loading")} operationKey={`citation-source-${chunk.chunk_id}`} framed={false}><Skeleton className="h-24 w-full" /></TimedLoadingState>;
    if (source.isError) {
      if (isSourceVersionStale(source.error)) return <SourceVersionStaleNotice />;
      return <ApiErrorState error={source.error} fallback={t("search.previewSource.error")} onRetry={() => { void source.refetch(); }} />;
    }
  }
  return (
    <DocumentPreview
      documentId={chunk.document_id}
      recipeId={recipeId}
      chunkSetId={chunkSetId}
      fileName={previewFileName}
      preparedPdf={source.data?.is_pdf}
      focusPage={focus.focusPage}
      focusBbox={highlight ? focus.focusBbox : null}
      focusBboxMode={focus.focusBboxMode}
      focusBboxUnit={focus.focusBboxUnit}
      focusPageSize={focus.focusPageSize}
      highlights={highlight ? focus.highlights : []}
      // テキストの原本は、根拠の抜粋の箇所へ移って強調する（#2512）。
      focusText={highlight ? chunk.text : null}
      className="min-h-0 flex-1"
    />
  );
}
