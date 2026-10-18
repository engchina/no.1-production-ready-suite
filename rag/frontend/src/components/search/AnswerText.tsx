import {
  AnswerSections,
  useEvidenceSource,
  type AnswerCitationRef,
  type AnswerSectionEntry,
  type AnswerSectionsBlock,
} from "@production-ready/ui";
import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";
import type { ReactNode } from "react";

import type { RetrievedChunk } from "@/lib/api";
import {
  matchCitationLine,
  parseAnswerText,
  parseCitationLine,
  type AnswerTextEntry,
} from "@/lib/answer-text";
import type { UnreadSectionLink } from "@/lib/answer-diagnostics";
import { t } from "@/lib/i18n";
import { APP_ROUTES } from "@/lib/routes";

import { CitationSourceSheet } from "./CitationSourceSheet";

// rag_engine の grounded.py の UNVERIFIED_SECTION_TITLE（#1436・#2040）。
const UNREAD_SECTION_TITLE = "回答に未反映の関連章節";
// rag_engine の grounded.py の GAPS_SECTION_TITLE。項目が多ければ共通の「すべて表示」で畳む（Agent の同じ節と
// 同じ規則。#2361）。
const GAPS_SECTION_TITLE = "資料から確認できなかった事項";

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
  unreadSections = [],
}: {
  text: string;
  streaming?: boolean;
  /** 生成中に本文の後ろに出すカーソル。 */
  cursor?: ReactNode;
  /** 本文の更新を読み上げる（チャット）。 */
  live?: boolean;
  /** この回答の引用。根拠の行から原文のプレビューを開くのに使う。 */
  citations?: readonly RetrievedChunk[];
  /**
   * 「回答に未反映の関連章節」の行ごとの章節（#2040）。当たる行に章節の原文を開くリンクを付ける。
   * 文書の詳細を開けない利用者には渡さない（呼び出し側が `canOpenDocumentDetail` で決める）。
   */
  unreadSections?: readonly UnreadSectionLink[];
}) {
  const source = useEvidenceSource<number>();
  const blocks = streaming ? null : parseAnswerText(text);
  if (!blocks) {
    return (
      <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg" aria-live={live ? "polite" : undefined}>
        {text}
        {cursor}
      </p>
    );
  }
  // 節・段落・根拠の行の見た目は 3 製品で共通の AnswerSections（#2328）。
  return (
    <AnswerSections
      live={live}
      blocks={blocks.map((block, index): AnswerSectionsBlock =>
        block.kind === "paragraph"
          ? { kind: "paragraph", key: String(index), content: block.text }
          : {
              kind: "section",
              key: String(index),
              title: block.title,
              ordered: block.ordered,
              collapsible: block.title === GAPS_SECTION_TITLE,
              entries: block.entries.map((entry, entryIndex) => toEntry(entry, entryIndex, block.title)),
            }
      )}
    >
      {/* 根拠の行から、根拠の一覧の「根拠を表示」と同じ右のサイドパネルを開く（#2360）。 */}
      {source.mounted && source.shown != null ? (
        <CitationSourceSheet
          chunk={citations[source.shown] ?? null}
          index={source.shown}
          open={source.selected !== null}
          onClose={source.close}
        />
      ) : null}
    </AnswerSections>
  );

  function toCitation(citation: string, ordinal: number | undefined): AnswerCitationRef {
    const ref = parseCitationLine(citation);
    const index = matchCitationLine(ordinal, ref, citations);
    return index < 0
      ? { key: citation, label: citation }
      : {
          key: citation,
          label: citation,
          ariaLabel: t("search.answerText.openCitation", { citation }),
          onOpen: () => source.open(index),
        };
  }

  function renderSectionLink(text: string, title: string) {
    if (title !== UNREAD_SECTION_TITLE) return null;
    const section = unreadSections.find((item) => item.line === text);
    if (!section) return null;
    const query = section.page != null ? `?page=${section.page}` : "";
    return (
      <span className="mt-0.5 flex">
        <Link
          to={`${APP_ROUTES.documents}/${encodeURIComponent(section.documentId)}${query}`}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={t("search.answerText.openUnreadSectionLabel", { section: section.label })}
          className="inline-flex items-center gap-1 text-xs font-medium text-accent-fg hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
        >
          {t("search.answerText.openUnreadSection")}
          <ExternalLink size={14} aria-hidden />
        </Link>
      </span>
    );
  }

  function toEntry(entry: AnswerTextEntry, index: number, title: string): AnswerSectionEntry {
    if (entry.kind === "text") {
      return { key: String(index), content: entry.text, muted: true };
    }
    return {
      key: String(index),
      content: entry.text,
      citations: entry.citations.map((citation, position) => toCitation(citation, entry.citationLines[position])),
      extra: renderSectionLink(entry.text, title),
    };
  }
}
