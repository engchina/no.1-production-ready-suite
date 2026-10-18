import { FileText } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

/**
 * 回答の本文の節と根拠の行（RAG のチャット・RAG 検索・Agent のチャットで共通。#2328）。
 *
 * 回答を「要約の段落 →『資料により確認できた事項』の箇条書き（各項目の下に『根拠：文書 p.N』）→
 * 『資料から確認できなかった事項』」のように節に分けて出す。本文の文は製品が渡し（書き換えない）、
 * ここは並べ方と根拠の行の見た目だけを持つ。
 */

/** 根拠の行（「根拠：文書 p.3」）。`onOpen` があれば押せる行（原文・根拠を開く）、無ければ文字だけ。 */
export interface AnswerCitationRef {
  key: string;
  label: string;
  /** 押せる行の読み上げの名前（例:「根拠：文書 p.3 を表示」）。 */
  ariaLabel?: string;
  onOpen?: () => void;
}

export interface AnswerSectionEntry {
  key: string;
  content: ReactNode;
  /** 箇条書きの印を付けない補足の行（節の説明など）。 */
  muted?: boolean;
  citations?: readonly AnswerCitationRef[];
  /** 根拠の行の後ろに足すもの（章節を開くリンクなど）。 */
  extra?: ReactNode;
}

export type AnswerSectionsBlock =
  | {
      kind: "paragraph";
      key: string;
      /** 段落の文（文字列は改行を保って出す）。 */
      content: ReactNode;
      citations?: readonly AnswerCitationRef[];
    }
  | {
      kind: "section";
      key: string;
      /** 節の見出し。無ければ見出しなしの箇条書き。 */
      title?: string;
      ordered?: boolean;
      entries: readonly AnswerSectionEntry[];
    };

/** 根拠の行の並び。 */
export function AnswerCitationLinks({ citations }: { citations: readonly AnswerCitationRef[] }) {
  return (
    <>
      {citations.map((citation) =>
        citation.onOpen ? (
          <span key={citation.key} className="mt-0.5 flex">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              icon={FileText}
              // 押せる根拠の行は強調色にし、当たらない根拠の行（文字だけ）と見分ける。
              className="text-accent-fg"
              aria-label={citation.ariaLabel}
              onClick={citation.onOpen}
              data-testid="answer-citation-link"
            >
              {citation.label}
            </Button>
          </span>
        ) : (
          <span
            key={citation.key}
            className="mt-0.5 flex items-center gap-1 text-xs text-fg-muted"
            data-testid="answer-citation-text"
          >
            <FileText size={14} aria-hidden className="shrink-0" />
            {citation.label}
          </span>
        ),
      )}
    </>
  );
}

export interface AnswerSectionsProps {
  blocks: readonly AnswerSectionsBlock[];
  /** 本文の更新を読み上げる（チャット）。 */
  live?: boolean;
  /** 末尾に置くもの（根拠を開くダイアログなど）。 */
  children?: ReactNode;
  className?: string;
  testId?: string;
}

/** 回答の本文の節（段落・見出しつきの箇条書き・各項目の根拠の行）。 */
export function AnswerSections({ blocks, live = false, children, className, testId = "answer-text" }: AnswerSectionsProps) {
  return (
    <div
      className={cn("space-y-3 text-sm leading-relaxed text-fg", className)}
      aria-live={live ? "polite" : undefined}
      data-testid={testId}
    >
      {blocks.map((block) =>
        block.kind === "paragraph" ? (
          block.citations?.length ? (
            <div key={block.key}>
              <p className="whitespace-pre-wrap">{block.content}</p>
              <AnswerCitationLinks citations={block.citations} />
            </div>
          ) : (
            <p key={block.key} className="whitespace-pre-wrap">
              {block.content}
            </p>
          )
        ) : (
          <section key={block.key} className="space-y-1.5" data-testid="answer-section">
            {block.title ? <h4 className="text-sm font-semibold text-fg">{block.title}</h4> : null}
            {block.ordered ? (
              <ol className="list-decimal space-y-1.5 pl-5">{block.entries.map(renderEntry)}</ol>
            ) : (
              <ul className="list-disc space-y-1.5 pl-5">{block.entries.map(renderEntry)}</ul>
            )}
          </section>
        ),
      )}
      {children}
    </div>
  );
}

function renderEntry(entry: AnswerSectionEntry) {
  if (entry.muted) {
    return (
      <li key={entry.key} className="list-none whitespace-pre-wrap text-fg-muted">
        {entry.content}
      </li>
    );
  }
  return (
    <li key={entry.key} className="whitespace-pre-wrap">
      {entry.content}
      {entry.citations?.length ? <AnswerCitationLinks citations={entry.citations} /> : null}
      {entry.extra}
    </li>
  );
}
