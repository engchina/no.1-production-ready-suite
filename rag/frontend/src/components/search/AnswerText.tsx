import {
  AnswerBody,
  AnswerCitationLinks,
  AnswerPlainText,
  AnswerSections,
  AnswerSourceMarks,
  cn,
  hasMarkdownTable,
  MarkdownInlineText,
  MarkdownText,
  useEvidenceSource,
  type AnswerBodyLabels,
  type AnswerBodyTableCell,
  type AnswerCitationRef,
  type AnswerSectionEntry,
  type AnswerSectionsBlock,
  type SplitMarkdownTablesOptions,
} from "@production-ready/ui";
import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";
import { useId, type ReactNode } from "react";

import type { RetrievedChunk } from "@/lib/api";
import {
  answerFormat,
  citationLocation,
  displayCitationLine,
  parseAnswerText,
  parseCitationLine,
  REASONING_LABEL,
  resolveCitationLineTargets,
  type AnswerTextEntry,
  type CitationTarget,
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
// 回答の本文の表（#4734）。モデルが書いた Markdown の表に加えて、回答エンジンが文書の表から切り出した区切りの行の無い
// 行の並び（rag_engine の table_html_as_rows。#4717）も表にする。判定は行の形だけ。
const ANSWER_TABLE_OPTIONS: SplitMarkdownTablesOptions = { headerlessRows: true };
// 回答エンジンが冒頭の回答の表の右端の出典の列に入れる番号の印（rag_engine の answer_table.SOURCE_MARK。#4812）。
const SOURCE_MARKS = /^(?:\[\d+\])+$/;

/**
 * 回答本文（#651）。回答エンジンの書式（節の見出し・説明・「根拠：」の行）に合う本文は、節・説明・根拠に分けて出す。
 * 生成中は文字列の表示を保ち、完了した本文は共有の Markdown 部品で描く。
 * 根拠の行は、当たる引用（`citations`）があれば押せるようにし、その引用の原文のプレビューを開く（#657）。
 * 当たる引用は、回答エンジンが付けた出典行の番号で決める（無ければファイル名と頁。#1330）。位置が確定できない行は
 * 候補の引用を開き、位置は未確定と示して原文を強調しない（#2710）。
 */
export function AnswerText({
  text,
  streaming = false,
  cursor = null,
  live = false,
  citations = [],
  unreadSections = [],
  diagnostics,
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
  /** 回答の記録の診断（`diagnostics.answer`）。回答の形式を読む。生成中は渡さない。 */
  diagnostics?: unknown;
}) {
  const source = useEvidenceSource<CitationTarget>();
  // 出典の列の印の移り先（根拠の行）の id。同じ画面に回答が複数あっても重ならないよう回答ごとに分ける。
  const anchorPrefix = useId();
  const format = answerFormat(diagnostics);
  const blocks = streaming ? null : parseAnswerText(text, format);
  // 本文順で初めて出る根拠の行に番号を付け、同じ行は段落と節をまたいでも同じ番号にする。
  const sourceNumbers = new Map<string, number>();
  const firstSources = new Map<number, AnswerCitationRef>();
  const anchor = (number: number) => `${anchorPrefix}-sources-${number}`;
  // 回答エンジンの決まった文言（保存した表示形式の値と同じ段落・説明）は画面の言語で描く（回答の言語の lang を付けない。#3349）。
  const fixedTexts = new Set(Object.values(format ?? {}));
  const fixedLang = (value: string) => (fixedTexts.has(value) ? null : undefined);
  if (!blocks) {
    // 生成中の未完の記法は文字列のまま。表の形がそろった場合と完成本文は共有の描画に渡す。
    if (streaming && !hasTable(text)) {
      return <AnswerPlainText text={text} live={live}>{cursor}</AnswerPlainText>;
    }
    return (
      <AnswerSections
        live={live}
        blocks={[{ kind: "content", key: "body", content: <AnswerMarkdownBody text={text} />, passageText: text }]}
      >
        {cursor}
      </AnswerSections>
    );
  }
  // 節・段落・根拠の行の見た目は 3 製品で共通の AnswerSections（#2328）。
  return (
    <AnswerSections
      live={live}
      blocks={blocks.map((block, index): AnswerSectionsBlock =>
        block.kind === "paragraph" && block.citations?.length
          ? sourcedParagraph(block.text, numberedCitations(block.citations, block.citationLines), index)
          : block.kind === "paragraph"
          ? { kind: "content", key: String(index), content: <AnswerMarkdownBody text={block.text} lang={fixedLang(block.text)} />, passageText: block.text, lang: fixedLang(block.text) }
          : {
              kind: "section",
              key: String(index),
              title: block.title,
              ordered: block.ordered,
              collapsible: block.title === (format?.gaps_section ?? GAPS_SECTION_TITLE),
              entries: block.entries.map((entry, entryIndex) => toEntry(entry, entryIndex, block.title)),
            }
      )}
    >
      {/* 根拠の行から、根拠の一覧の「根拠を表示」と同じ右のサイドパネルを開く（#2360）。 */}
      {source.mounted && source.shown != null ? (
        <CitationSourceSheet
          chunk={citations[source.shown.index] ?? null}
          index={source.shown.index}
          position={source.shown.position}
          open={source.selected !== null}
          onClose={source.close}
        />
      ) : null}
    </AnswerSections>
  );

  /**
   * 節の前の段落（冒頭の回答の表を含む）と、その直後の根拠の行（#4812）。根拠の行に回答全体の通し番号の「[n]」の印を
   * 付け、その出典が最初に出る行に id を付ける（#5489）。表の出典の列の印は、その行の原文を開くリンクにする（原文を
   * 開けない行は、その行へ移る。#4822）。
   */
  function sourcedParagraph(text: string, refs: AnswerCitationRef[], index: number): AnswerSectionsBlock {
    const renderCell = ({ value, columnIndex, table }: AnswerBodyTableCell) => {
      const column = table.columns[columnIndex]?.name;
      if (columnIndex !== table.columns.length - 1 || typeof value !== "string" || !SOURCE_MARKS.test(value)) return undefined;
      if (format?.table_source_column && column !== format.table_source_column) return undefined;
      const numbers = [...value.matchAll(/\[(\d+)\]/g)].map((match) => Number(match[1]));
      return (
        <AnswerSourceMarks
          marks={numbers.map((number) => ({
            key: String(number),
            label: `[${number}]`,
            // 当たる根拠の行が無い番号は移り先を持たない（印は文字だけで出す）。
            targetId: firstSources.has(number) ? anchor(number) : undefined,
            ariaLabel: t(
              firstSources.get(number)?.onOpen ? "search.answerText.openSourceMark" : "search.answerText.sourceMarkLabel",
              { mark: `[${number}]` },
            ),
          }))}
        />
      );
    };
    return {
      kind: "content",
      key: String(index),
      content: <AnswerMarkdownBody text={text} renderTableCell={renderCell} lang={fixedLang(text)} />,
      passageText: text,
      lang: fixedLang(text),
      citations: refs,
    };
  }

  function numberedCitations(lines: readonly string[], ordinals: readonly number[] | undefined): AnswerCitationRef[] {
    return lines.flatMap((line, position) => {
      const [first, ...others] = toCitations(line, ordinals?.[position]);
      const existing = sourceNumbers.get(line);
      const number = existing ?? sourceNumbers.size + 1;
      if (existing === undefined) {
        sourceNumbers.set(line, number);
        firstSources.set(number, first);
      }
      const marker = `[${number}]`;
      // 1 行が複数の原文を開くときも印は一つ。各リンクは出現位置ごとの原文を開く。
      return [{
        ...first,
        marker,
        anchorId: existing === undefined ? anchor(number) : undefined,
        ariaLabel: first.onOpen ? t("search.answerText.openNumberedCitation", { mark: marker, citation: first.label }) : undefined,
      }, ...others];
    });
  }

  function toCitations(citation: string, ordinal: number | undefined): AnswerCitationRef[] {
    const ref = parseCitationLine(citation, format);
    const targets = resolveCitationLineTargets(ordinal, ref, citations);
    if (!targets.length) return [{ key: `${ordinal}:${citation}`, label: citation }];
    return targets.map((target, index) => {
      const display = displayCitationLine(citation, ref, citations[target.index], ordinal, format);
      const label = targets.length === 1 ? display : t(
        target.position.kind === "unresolved"
          ? "search.answerText.citationCandidates"
          : "search.answerText.citationPart",
        { citation: display, number: index + 1, total: targets.filter((entry) => entry.position.kind === "exact").length },
      );
      return {
        key: `${ordinal}:${citations[target.index].document_id}:${citations[target.index].chunk_id ?? target.index}:${target.position.kind}`,
        label,
        location: citationLocation(display, ref?.fileName),
        ariaLabel: t("search.answerText.openCitation", { citation: label }),
        onOpen: () => source.open(target),
      };
    });
  }

  function renderSectionLink(text: string, title: string) {
    if (title !== UNREAD_SECTION_TITLE && title !== "資料の読み込みに関する補足" && title !== format?.unverified_section) return null;
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
      const refs = numberedCitations(entry.citations ?? [], entry.citationLines);
      return {
        key: String(index),
        content: <>{textOrTable(entry.text)}{refs.length ? <AnswerCitationLinks citations={refs} /> : null}</>,
        passageText: entry.text,
        muted: true,
        lang: fixedLang(entry.text),
        block: refs.length > 0 || hasTable(entry.text),
      };
    }
    const hasDetails = Boolean(entry.details?.length || entry.reasoning);
    return {
      key: String(index),
      content: hasDetails ? <>
        {textOrTable(entry.text)}
        {entry.details?.map((detail, position) => hasTable(detail)
          ? <AnswerMarkdownBody key={position} text={detail} className="text-fg-muted" />
          : <span key={position} className="block whitespace-pre-line text-fg-muted"><MarkdownInlineText text={detail} /></span>)}
        {entry.reasoning ? <ReasoningText text={entry.reasoning} /> : null}
      </> : textOrTable(entry.text),
      // 文字列の content と同じ言語の判定を、表を描く要素になっても続ける（#3335）。
      passageText: hasDetails ? undefined : entry.text,
      block: [entry.text, entry.reasoning ?? "", ...(entry.details ?? [])].some(hasTable),
      citations: numberedCitations(entry.citations, entry.citationLines),
      extra: renderSectionLink(entry.text, title),
    };
  }
}

function hasTable(text: string): boolean {
  return hasMarkdownTable(text, ANSWER_TABLE_OPTIONS);
}

function answerTableLabels(): AnswerBodyLabels {
  return {
    tableName: (number) =>
      number === null ? t("search.answerText.tableName") : t("search.answerText.tableNameIndexed", { number }),
    table: (name) => ({
      tableLabel: name,
      scrollLabel: t("search.answerText.tableScrollLabel", { name }),
      sheetTitle: (count) => t("search.answerText.tableSheetTitle", { name, count }),
      close: t("search.answerText.tableClose", { name }),
    }),
  };
}

/**
 * 完成した本文（表は共通の結果の表、それ以外は Markdown。#4734・#5631）。回答の表はセルの全文を折り返し、省略しない
 * （共通の表の回答のモード。#4812）。
 */
function AnswerMarkdownBody({
  text,
  className,
  renderTableCell,
  lang,
}: {
  text: string;
  className?: string;
  renderTableCell?: (cell: AnswerBodyTableCell) => ReactNode | undefined;
  lang?: string | null;
}) {
  return (
    <AnswerBody
      text={text}
      splitOptions={ANSWER_TABLE_OPTIONS}
      renderTableCell={renderTableCell}
      labels={answerTableLabels()}
      className={cn("whitespace-normal", className)}
      testId="answer-table-body"
      renderText={(segment) => <MarkdownText text={segment} headingLevel={6} lang={lang} />}
    />
  );
}

/** 項目の中は行内の Markdown を描き、表だけはブロックとして描く。 */
function textOrTable(text: string): ReactNode {
  return hasTable(text) ? <AnswerMarkdownBody text={text} /> : <MarkdownInlineText text={text} />;
}

/**
 * 結論の説明（#4393）。説明は Markdown（段落・箇条書き・コードの囲み）を含むことがあるので、共通の Markdown の部品で
 * 描く（コードは枠の中で横にスクロールし、375px でもページをはみ出さない）。1 行の説明は今までどおり見出しの語に続けて出す。
 */
function ReasoningText({ text }: { text: string }) {
  if (!text.includes("\n")) {
    return (
      <span className="block whitespace-pre-line text-fg-muted" data-testid="answer-application-reasoning">
        {REASONING_LABEL}
        <MarkdownInlineText text={text} />
      </span>
    );
  }
  return (
    <div className="text-fg-muted" data-testid="answer-application-reasoning">
      <span className="block">{REASONING_LABEL}</span>
      {hasTable(text) ? (
        <AnswerBody
          text={text}
          splitOptions={ANSWER_TABLE_OPTIONS}
          labels={answerTableLabels()}
          className="whitespace-normal"
          testId="answer-table-body"
          renderText={(segment) => <MarkdownText text={segment} headingLevel={6} className="text-fg-muted" />}
        />
      ) : (
        <MarkdownText text={text} headingLevel={6} className="text-fg-muted" />
      )}
    </div>
  );
}
