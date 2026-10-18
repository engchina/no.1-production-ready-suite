import { EvidenceFigureAction } from "@production-ready/ui";

import { EVIDENCE_FIGURE_LABELS, type AnswerEvidenceEntry } from "@/lib/answer-evidence";
import { agentApi } from "@/lib/api";

/**
 * RAG の図の根拠の「図を開く」（#1311）。部品は RAG と同じ共通の `EvidenceFigureAction`（#2428）で、Agent が持つのは
 * 画像の取り方だけ: Agent の backend から短命の URL（RAG が閲覧者・根拠・版に縛って署名し、読むたびに権限・版を
 * 確かめ直す）を取る。URL は保存せず、開くたびに取り直す。図の根拠（`image_ref` がある根拠）でなければ何も出さない。
 */
export function RagFigureAction({
  runId,
  entry,
  testId,
}: {
  runId: string;
  entry: AnswerEvidenceEntry;
  testId?: string;
}) {
  const figure = entry.figure;
  if (!figure) return null;
  return (
    <EvidenceFigureAction
      title={entry.item.fileName}
      location={entry.location}
      aiDescription={entry.aiFigureDescription}
      load={(signal) =>
        agentApi
          .getRunFigureUrl(runId, figure.documentId, figure.chunkId, { signal })
          .then((data) => ({ url: data.url }))
      }
      labels={EVIDENCE_FIGURE_LABELS}
      testId={testId}
    />
  );
}
