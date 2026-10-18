import { EvidenceFigureAction, evidenceFigureLabels } from "@production-ready/ui";

import { api, type RetrievedChunk } from "@/lib/api";
import { citationFigure } from "@/lib/chunk-metadata";
import { t } from "@/lib/i18n";

/** 「図を開く」の文言（Agent と同じ共通の部品の既定）。 */
const FIGURE_LABELS = evidenceFigureLabels();

/**
 * 図の根拠の「図を開く」（#2428）。部品は Agent と同じ共通の `EvidenceFigureAction` で、RAG が持つのは画像の取り方
 * だけ: 画面用の API（Cookie のセッション）で、保存した図の領域を今の権限・版で確かめて切り出した画像を取り、
 * blob の URL で出す（閉じたら解放する）。backend が元の図の画像を切り出せると返した根拠（`figure_image_available`。
 * Agent の `image_ref` と同じ判定。#2433）だけに出し、それ以外は何も出さない。
 */
export function CitationFigureAction({ chunk, testId }: { chunk: RetrievedChunk; testId?: string }) {
  const figure = citationFigure(chunk);
  if (!figure) return null;
  const page = figure.page ? t("search.citation.page", { page: figure.page }) : "";
  const location = [page, figure.section].filter(Boolean).join(" · ") || null;
  return (
    <EvidenceFigureAction
      title={chunk.file_name ?? chunk.document_id}
      location={location}
      aiDescription={figure.aiDescription}
      load={async (signal) => {
        const file = await api.getChunkFigure(chunk.document_id, chunk.chunk_id, { signal });
        const url = URL.createObjectURL(file.blob);
        return { url, release: () => URL.revokeObjectURL(url) };
      }}
      labels={FIGURE_LABELS}
      testId={testId}
    />
  );
}
