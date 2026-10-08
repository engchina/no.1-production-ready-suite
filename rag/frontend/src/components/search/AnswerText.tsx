import { Button } from "@production-ready/ui";
import { FileText } from "lucide-react";
import { useState, type ReactNode } from "react";

import type { RetrievedChunk } from "@/lib/api";
import {
  matchCitationLine,
  parseAnswerText,
  parseCitationLine,
  type AnswerTextEntry,
} from "@/lib/answer-text";
import { t } from "@/lib/i18n";

import { CitationPreviewDialog } from "./CitationPreviewDialog";

/**
 * 回答本文（#651）。回答エンジンの書式（節の見出し・説明・「根拠：」の行）に合う本文は、節・説明・根拠に分けて出す。
 * 生成中と、書式に合わない本文（根拠不足の案内など）はそのままの文字列で出す。
 * 根拠の行は、当たる引用（`citations`）があれば押せるようにし、その引用の原文のプレビューを開く（#657）。
 * 当たる引用は、回答エンジンが付けた出典行の番号で決める（無ければファイル名と頁。#1330）。
 */
export function AnswerText({
  text,
  streaming = false,
  cursor = null,
  live = false,
  citations = [],
}: {
  text: string;
  streaming?: boolean;
  /** 生成中に本文の後ろに出すカーソル。 */
  cursor?: ReactNode;
  /** 本文の更新を読み上げる（チャット）。 */
  live?: boolean;
  /** この回答の引用。根拠の行から原文のプレビューを開くのに使う。 */
  citations?: readonly RetrievedChunk[];
}) {
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const blocks = streaming ? null : parseAnswerText(text);
  const previewChunk = previewIndex == null ? undefined : citations[previewIndex];
  if (!blocks) {
    return (
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg" aria-live={live ? "polite" : undefined}>
        {text}
        {cursor}
      </p>
    );
  }
  return (
    <div
      className="space-y-3 text-sm leading-relaxed text-fg"
      aria-live={live ? "polite" : undefined}
      data-testid="answer-text"
    >
      {blocks.map((block, index) =>
        block.kind === "paragraph" ? (
          <p key={index} className="whitespace-pre-wrap">
            {block.text}
          </p>
        ) : (
          <section key={index} className="space-y-1.5">
            <h4 className="text-sm font-semibold text-fg">{block.title}</h4>
            {block.ordered ? (
              <ol className="list-decimal space-y-1.5 pl-5">{block.entries.map(renderEntry)}</ol>
            ) : (
              <ul className="list-disc space-y-1.5 pl-5">{block.entries.map(renderEntry)}</ul>
            )}
          </section>
        )
      )}
      {previewChunk ? (
        <CitationPreviewDialog chunk={previewChunk} open onClose={() => setPreviewIndex(null)} />
      ) : null}
    </div>
  );

  function renderCitation(citation: string, ordinal: number | undefined) {
    const ref = parseCitationLine(citation);
    const index = matchCitationLine(ordinal, ref, citations);
    if (index < 0) {
      return (
        <span key={citation} className="mt-0.5 flex items-center gap-1 text-xs text-fg-muted">
          <FileText size={14} aria-hidden className="shrink-0" />
          {citation}
        </span>
      );
    }
    return (
      <span key={citation} className="mt-0.5 flex">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          icon={FileText}
          // 押せる根拠の行は強調色にし、当たらない根拠の行（文字だけ）と見分ける。
          className="text-accent-fg"
          aria-label={t("search.answerText.openCitation", { citation })}
          onClick={() => setPreviewIndex(index)}
        >
          {citation}
        </Button>
      </span>
    );
  }

  function renderEntry(entry: AnswerTextEntry, index: number) {
    if (entry.kind === "text") {
      return (
        <li key={index} className="list-none whitespace-pre-wrap text-fg-muted">
          {entry.text}
        </li>
      );
    }
    return (
      <li key={index} className="whitespace-pre-wrap">
        {entry.text}
        {entry.citations.map((citation, position) => renderCitation(citation, entry.citationLines[position]))}
      </li>
    );
  }
}
