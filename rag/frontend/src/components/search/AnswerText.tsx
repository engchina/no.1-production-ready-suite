import { FileText } from "lucide-react";
import type { ReactNode } from "react";

import { parseAnswerText, type AnswerTextEntry } from "@/lib/answer-text";

/**
 * 回答本文（#651）。回答エンジンの書式（節の見出し・説明・「根拠：」の行）に合う本文は、節・説明・根拠に分けて出す。
 * 生成中と、書式に合わない本文（根拠不足の案内など）はそのままの文字列で出す。
 */
export function AnswerText({
  text,
  streaming = false,
  cursor = null,
  live = false,
}: {
  text: string;
  streaming?: boolean;
  /** 生成中に本文の後ろに出すカーソル。 */
  cursor?: ReactNode;
  /** 本文の更新を読み上げる（チャット）。 */
  live?: boolean;
}) {
  const blocks = streaming ? null : parseAnswerText(text);
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
    </div>
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
      {entry.citations.map((citation) => (
        <span key={citation} className="mt-0.5 flex items-center gap-1 text-xs text-fg-muted">
          <FileText size={14} aria-hidden className="shrink-0" />
          {citation}
        </span>
      ))}
    </li>
  );
}
