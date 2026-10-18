import { useState } from "react";
import {
  AnswerEvidence,
  AnswerSections,
  EvidenceCard,
  MarkdownInlineText,
  SideSheet,
  type AnswerCitationRef,
  type AnswerSectionsBlock,
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
 * Agent には本文の箇所を開く画面が無い（RAG の画面用の API は呼ばない）ので、カードは抜粋と場所を出し、図の根拠は
 * 「図を開く」（RAG の MCP 経由の短命の URL。#1311）で確かめる。
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
  return (
    <AnswerEvidence
      items={evidence.entries.map((entry) => entry.item)}
      labels={EVIDENCE_LABELS}
      idPrefix={idPrefix}
      className={className}
      renderActions={(_item, index) => <FigureAction runId={runId} entry={evidence.entries[index]} testId={`${idPrefix}-figure-${index}`} />}
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
 * 押すと、その根拠のカードを右のシートで開く（RAG の「引用箇所を表示」に当たる）。
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
  const [openChunk, setOpenChunk] = useState<string | null>(null);
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
        onOpen: () => setOpenChunk(chunkId),
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
  const opened = openChunk ? byChunk.get(openChunk) : undefined;
  return (
    <AnswerSections blocks={sections} testId={testId}>
      <SideSheet
        open={opened !== undefined}
        onClose={() => setOpenChunk(null)}
        title={t("chat.answer.citationTitle")}
        closeLabel={t("chat.answer.citationClose")}
        side="right"
        size="wide"
        data-testid={`${testId}-citation-sheet`}
      >
        {opened ? (
          <ul>
            <EvidenceCard
              item={opened.entry.item}
              index={opened.index}
              labels={EVIDENCE_LABELS}
              actions={<FigureAction runId={runId} entry={opened.entry} testId={`${testId}-citation-figure`} />}
            />
          </ul>
        ) : null}
      </SideSheet>
    </AnswerSections>
  );
}
