import { ChevronDown, ChevronUp, FileText } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { cn } from "../../lib/utils";
import { Button } from "../ui/button";

/**
 * 回答の本文の節と根拠の行（RAG のチャット・RAG 検索・Agent のチャットで共通。#2328）。
 *
 * 回答を「要約の段落 →『資料により確認できた事項』の箇条書き（各項目の下に『根拠：文書 p.N』）→
 * 『資料から確認できなかった事項』」のように節に分けて出す。本文の文は製品が渡し（書き換えない）、
 * ここは並べ方と根拠の行の見た目だけを持つ。
 *
 * 項目の多い節（`collapsible`。資料から確認できなかった事項など）は、先頭の
 * `ANSWER_SECTION_COLLAPSE_AFTER` 件だけを出し、「すべて表示（全 N 件）」で残りを同じ書式のまま開く（#2361。
 * 「ほか N 件」の行で切らず、同じ種類の項目を 2 か所に分けない）。
 */

/** 畳める節で、畳んだときに出す項目の数。 */
export const ANSWER_SECTION_COLLAPSE_AFTER = 10;

export interface AnswerSectionsLabels {
  /** 畳んだ節を開くボタン（`{count}` は節の項目の数）。 */
  showAll: (count: number) => string;
  /** 開いた節を畳むボタン。 */
  collapse: string;
}

export const DEFAULT_ANSWER_SECTIONS_LABELS: AnswerSectionsLabels = {
  showAll: (count) => `すべて表示（全 ${count} 件）`,
  collapse: "折りたたむ",
};

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
      /** 段落にできない本文（表・コード・見出しなど。製品が描いたもの）と、その下の根拠の行。 */
      kind: "content";
      key: string;
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
      /** 項目が多いとき（`ANSWER_SECTION_COLLAPSE_AFTER` 件を超える）、先頭だけを出して残りを畳む。 */
      collapsible?: boolean;
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
  labels?: AnswerSectionsLabels;
}

/** 回答の本文の節（段落・見出しつきの箇条書き・各項目の根拠の行）。 */
export function AnswerSections({
  blocks,
  live = false,
  children,
  className,
  testId = "answer-text",
  labels = DEFAULT_ANSWER_SECTIONS_LABELS,
}: AnswerSectionsProps) {
  return (
    <div
      className={cn("space-y-3 text-sm leading-relaxed text-fg", className)}
      aria-live={live ? "polite" : undefined}
      data-testid={testId}
    >
      {blocks.map((block) =>
        block.kind === "content" ? (
          <div key={block.key} className="min-w-0">
            {block.content}
            {block.citations?.length ? <AnswerCitationLinks citations={block.citations} /> : null}
          </div>
        ) : block.kind === "paragraph" ? (
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
          // 節の見出しの前は、段落の間（0.75rem）より広い 1.25rem（`--space-5`。0.75rem + pt-2）を空け、節の区切りを
          // 見て分かるようにする（#2325。見出しは後ろの内容に近く、前の内容から離す）。先頭の節には付けない。
          <AnswerSection key={block.key} block={block} labels={labels} />
        ),
      )}
      {children}
    </div>
  );
}

function AnswerSection({
  block,
  labels,
}: {
  block: Extract<AnswerSectionsBlock, { kind: "section" }>;
  labels: AnswerSectionsLabels;
}) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  // 数えるのは箇条書きの項目だけ（節の説明の行 `muted` は畳まずに出す）。
  const items = block.entries.filter((entry) => !entry.muted).length;
  const toggle = Boolean(block.collapsible) && items > ANSWER_SECTION_COLLAPSE_AFTER;
  const entries = toggle && !expanded ? firstItems(block.entries, ANSWER_SECTION_COLLAPSE_AFTER) : block.entries;
  return (
    <section className="space-y-1.5 not-first:pt-2" data-testid="answer-section">
      {block.title ? <h4 className="text-sm font-semibold text-fg">{block.title}</h4> : null}
      {block.ordered ? (
        <ol id={listId} className="list-decimal space-y-1.5 pl-5">
          {entries.map(renderEntry)}
        </ol>
      ) : (
        <ul id={listId} className="list-disc space-y-1.5 pl-5">
          {entries.map(renderEntry)}
        </ul>
      )}
      {toggle ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          icon={expanded ? ChevronUp : ChevronDown}
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={() => setExpanded((value) => !value)}
          data-testid="answer-section-toggle"
        >
          {expanded ? labels.collapse : labels.showAll(items)}
        </Button>
      ) : null}
    </section>
  );
}

/** 先頭から `limit` 件の項目までの行（節の説明の行 `muted` は数えずに残す）。 */
function firstItems(entries: readonly AnswerSectionEntry[], limit: number): AnswerSectionEntry[] {
  const result: AnswerSectionEntry[] = [];
  let items = 0;
  for (const entry of entries) {
    if (!entry.muted) {
      if (items >= limit) break;
      items += 1;
    }
    result.push(entry);
  }
  return result;
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
