import { LocateFixed } from "lucide-react";
import {
  AnswerEvidence,
  AnswerSections,
  Button,
  EvidenceSourceSheet,
  MarkdownInlineText,
  useEvidenceSource,
  type AnswerCitationRef,
  type AnswerSectionsBlock,
  type EvidenceSourceState,
} from "@production-ready/ui";

import { AnswerBody } from "@/components/chat/ResultTables";
import { RagFigureButton } from "@/components/evidence/RagFigure";
import {
  EVIDENCE_LABELS,
  citationLabel,
  type AnswerEvidenceData,
  type AnswerEvidenceEntry,
} from "@/lib/answer-evidence";
import type { StructuredBlock } from "@/lib/answer-structure";
import { t } from "@/lib/i18n";

/**
 * 回答の根拠の塊（#2328）。RAG のチャットと同じ共有の `AnswerEvidence`（「AI に渡した N 件 ／ 回答で引用 M 件」の
 * 折りたたみ・カード・一定の高さの中のスクロール）。回答の本文の下・回答の評価の前に置く。
 * カードの「根拠を表示」は、本文の根拠の行と同じ右のサイドパネル（共通の `EvidenceSourceSheet`。#2360）を開く。
 */
export function AnswerEvidenceBlock({
  runId,
  evidence,
  idPrefix,
  className,
}: {
  runId: string;
  evidence: AnswerEvidenceData;
  idPrefix: string;
  className?: string;
}) {
  const source = useEvidenceSource<number>();
  return (
    <>
      <AnswerEvidence
        items={evidence.entries.map((entry) => entry.item)}
        labels={EVIDENCE_LABELS}
        idPrefix={idPrefix}
        className={className}
        renderActions={(_item, index) => (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                type="button"
                variant="secondary"
                icon={LocateFixed}
                aria-haspopup="dialog"
                aria-label={t("chat.answer.citationOpenLabel", { file: evidence.entries[index].item.fileName })}
                onClick={() => source.open(index)}
                data-testid={`${idPrefix}-source-${index}`}
              >
                {t("chat.answer.citationOpen")}
              </Button>
            </div>
            <FigureAction runId={runId} entry={evidence.entries[index]} testId={`${idPrefix}-figure-${index}`} />
          </>
        )}
      />
      <EvidenceSource
        runId={runId}
        evidence={evidence}
        source={source}
        testId={`${idPrefix}-source-sheet`}
      />
    </>
  );
}

/**
 * 根拠 1 件の右のサイドパネル（RAG のチャットと同じ共通の `EvidenceSourceSheet`。#2360）。上部に根拠の要約と
 * 抜粋の全文、図の根拠は「図を開く」。Agent には原文のプレビューを出さない（RAG の原文の API は RAG の画面用の
 * Cookie の API で、Agent の画面から呼ぶと製品の境界を破る。プレビューの部品も RAG の frontend にある）。
 */
function EvidenceSource({
  runId,
  evidence,
  source,
  testId,
}: {
  runId: string;
  evidence: AnswerEvidenceData;
  source: EvidenceSourceState<number>;
  testId: string;
}) {
  if (!source.mounted || source.shown == null) return null;
  const entry = evidence.entries[source.shown];
  return (
    <EvidenceSourceSheet
      open={source.selected !== null}
      onClose={source.close}
      item={entry?.item ?? null}
      index={source.shown}
      labels={EVIDENCE_LABELS}
      actions={entry ? <FigureAction runId={runId} entry={entry} testId={`${testId}-figure`} /> : null}
      data-testid={testId}
    />
  );
}

/** 図の根拠の操作（AI が読んだ説明ならその旨と、元の図を開くボタン）。図でなければ何も出さない。 */
function FigureAction({ runId, entry, testId }: { runId: string; entry: AnswerEvidenceEntry; testId: string }) {
  if (!entry.figure) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {entry.aiFigureDescription ? (
        <p className="text-xs text-fg-muted" data-testid="evidence-figure-description">
          {t("evidence.figureDescription")}
        </p>
      ) : null}
      <RagFigureButton
        runId={runId}
        figure={entry.figure}
        title={entry.item.fileName}
        location={entry.location}
        testId={testId}
      />
    </div>
  );
}

/**
 * 回答の本文の節（RAG のチャットの回答と同じ共有の `AnswerSections`。#2328）。段落・項目の下の「根拠：文書 p.N」を
 * 押すと、その根拠を右のサイドパネルで開く（RAG の根拠の行と同じ共通の `EvidenceSourceSheet`。#2360）。
 */
export function StructuredAnswer({
  runId,
  blocks,
  evidence,
  testId,
}: {
  runId: string;
  blocks: StructuredBlock[];
  evidence: AnswerEvidenceData;
  testId: string;
}) {
  const source = useEvidenceSource<number>();
  const byChunk = new Map(evidence.entries.map((entry, index) => [entry.chunkId, { entry, index }]));
  const citations = (chunkIds: readonly string[]): AnswerCitationRef[] => {
    const refs: AnswerCitationRef[] = [];
    for (const chunkId of chunkIds) {
      const found = byChunk.get(chunkId);
      if (!found) continue;
      const label = citationLabel(found.entry);
      // 同じ文書・頁の根拠は 1 行にする。
      if (refs.some((ref) => ref.label === label)) continue;
      refs.push({
        key: chunkId,
        label,
        ariaLabel: EVIDENCE_LABELS.citationOpen(label),
        onOpen: () => source.open(found.index),
      });
    }
    return refs;
  };
  const sections: AnswerSectionsBlock[] = blocks.map((block) => {
    if (block.kind === "paragraph") {
      return {
        kind: "paragraph",
        key: block.key,
        content: <MarkdownInlineText text={block.text} />,
        citations: citations(block.chunkIds),
      };
    }
    if (block.kind === "markdown") {
      return {
        kind: "content",
        key: block.key,
        content: <AnswerBody text={block.text} headingLevel={2} />,
        citations: citations(block.chunkIds),
      };
    }
    return {
      kind: "section",
      key: block.key,
      title: block.title ?? undefined,
      ordered: block.ordered,
      entries: block.entries.map((entry) => ({
        key: entry.key,
        content: <MarkdownInlineText text={entry.text} />,
        muted: entry.muted,
        citations: citations(entry.chunkIds),
      })),
    };
  });
  return (
    <AnswerSections blocks={sections} testId={testId}>
      <EvidenceSource runId={runId} evidence={evidence} source={source} testId={`${testId}-citation-sheet`} />
    </AnswerSections>
  );
}
