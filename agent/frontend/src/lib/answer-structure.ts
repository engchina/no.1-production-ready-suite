import type { AnswerEvidenceData } from "@/lib/answer-evidence";
import { t } from "@/lib/i18n";

/**
 * 回答の本文の節（#2328）。本文の文は書き換えず（AI で直さない）、根拠の ID の印（「【証拠 ID: …】」など）を
 * 除いて段落・節に並べる。ツールが返した根拠に結び付いた分は、RAG と同じ「参考情報」の節（根拠の原文と
 * 「根拠：文書 p.N」）に出す。結び付いた出典の行（「文書 A.pdf p.3」）は「参考情報」の根拠の行で示すので本文から
 * 除く。根拠を 1 つも結び付けられないときは null（呼び出し側は今までの Markdown の描き方にする）。
 */

export interface StructuredEntry {
  key: string;
  text: string;
  raw?: string;
  chunkIds: string[];
  /** 箇条書きの印を付けない補足の行。 */
  muted?: boolean;
  /** Markdown として読まない文（根拠の原文・決まった説明の文）。 */
  plain?: boolean;
  /** 画面の決まった説明の文（回答の言語ではなく画面の言語で描く。#3349）。 */
  uiText?: boolean;
}

export type StructuredBlock =
  | { kind: "paragraph"; key: string; text: string; raw?: string; chunkIds: string[] }
  /** 表・コード・見出しの続きなど、段落にできない Markdown の塊。 */
  | { kind: "markdown"; key: string; text: string; raw?: string; chunkIds: string[] }
  | {
      kind: "section";
      key: string;
      title: string | null;
      ordered: boolean;
      entries: StructuredEntry[];
      collapsible?: boolean;
    };

const BULLET = /^\s*(?:[-*+・•]|(\d{1,3})[.)．）])\s+/;
const HEADING = /^\s*(?:#{1,6}\s+(.+?)\s*#*|\*\*([^*\n]{1,60})\*\*\s*[:：]?|【([^】\n]{1,30})】\s*[:：]?)\s*$/;
const MARKDOWN_BLOCK = /^\s*(?:\||```|~~~|>|\s{2,}[-*+]\s)/;
// 区切り線（`---` / `***` / `___`）。段落の文としては描けないので、塊を Markdown のまま描く（#4061）。
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
// Markdown の箇条書きの行（`・` は Markdown の箇条書きではないので含めない）。
const MARKDOWN_LIST_ITEM = /^ {0,3}(?:[-*+]|\d{1,9}[.)])[ \t]+\S/;

/**
 * 本文の先頭の見出しが、本文を囲む枠の見出し（チャットの `ChatAnswerBody`・実行の詳細の節の「回答」）と同じ文言なら
 * その行を除く（#4061）。LLM が本文を「## 回答」で始めると、枠の見出しの直下に同じ見出しが二重に出るため。
 * 先頭の 1 行だけを見る。本文の途中の見出しと、文言の違う見出しは残す。
 */
export function withoutFrameHeading(answer: string, frameHeading: string): string {
  const match = /^\s*(.*)(?:\n|$)/.exec(answer);
  if (!match || headingText(match[1]) !== frameHeading) return answer;
  return answer.slice(match[0].length).replace(/^\s*\n/, "");
}

function headingText(line: string): string | null {
  const match = HEADING.exec(line);
  if (!match) return null;
  return (match[1] ?? match[2] ?? match[3] ?? "").trim() || null;
}

/** 本文から根拠の ID の印を除いた表示の文（印の前後の空白をつめる）。 */
function displayText(raw: string, markers: readonly string[]): string {
  let value = raw;
  for (const marker of markers) value = value.split(marker).join("");
  return value
    .replace(/[ \t\u3000]+([。、．，,.）)」』])/g, "$1")
    .replace(/[ \t\u3000]{2,}/g, " ")
    .trim();
}

/** 空行で区切った本文の塊。 */
function chunks(text: string): string[][] {
  const result: string[][] = [];
  let current: string[] = [];
  for (const line of text.split("\n")) {
    if (line.trim()) {
      current.push(line);
    } else if (current.length) {
      result.push(current);
      current = [];
    }
  }
  if (current.length) result.push(current);
  return result;
}

interface Line {
  raw: string;
  text: string;
  chunkIds: string[];
}

/** 回答の本文を節に組み立てる。結び付けられないときは null。 */
export interface AnswerStructureTitles {
  /** 「参考情報」。 */
  reference: string;
  /** 「参考情報」の節の説明（「以下は資料の原文です。本質問の条件への該当は確認できていません。」）。 */
  referenceNote: string;
}

/** 節の見出しと説明の文（チャットと実行の詳細で同じ）。 */
export function answerStructureTitles(): AnswerStructureTitles {
  return {
    reference: t("chat.answer.reference"),
    referenceNote: t("chat.answer.referenceNote"),
  };
}

export function structureAnswer(
  answer: string,
  evidence: AnswerEvidenceData | null,
  titles: AnswerStructureTitles
): StructuredBlock[] | null {
  if (!evidence?.references.length) return null;
  const blocks = bodyBlocks(withoutCitationLines(answer, evidence.citationLines), evidence);
  blocks.push(referenceSection(evidence, titles));
  return blocks;
}

/** 「参考情報」の節（説明の文 → 根拠の原文「…」と「根拠：文書 p.N」）。 */
function referenceSection(evidence: AnswerEvidenceData, titles: AnswerStructureTitles): StructuredBlock {
  return {
    kind: "section",
    key: "reference",
    title: titles.reference,
    ordered: false,
    entries: [
      { key: "reference-note", text: titles.referenceNote, chunkIds: [], muted: true, plain: true, uiText: true },
      ...evidence.references.map((reference, index) => ({
        key: `reference-${index}`,
        text: `「${reference.quote}」`,
        chunkIds: reference.chunkIds,
        plain: true,
      })),
    ],
  };
}

/** 根拠に結び付いた出典の行と、それで中身が無くなった見出しの行を除いた本文。 */
function withoutCitationLines(answer: string, citationLines: readonly string[]): string {
  if (!citationLines.length) return answer;
  const lines = answer.split("\n");
  const removed = lines.map((line) => citationLines.includes(line.trim()));
  const kept = (index: number) => !removed[index] && lines[index].trim() !== "";
  lines.forEach((line, index) => {
    if (removed[index] || headingText(line) === null) return;
    // 見出しの下（空行を除く）の最初の行が除いた出典の行で、残る次の行がその見出しの下に入らない（本文の終わり・
    // 次の見出し・空行の後の箇条書きでない段落）なら、見出しも除く。
    let next = index + 1;
    while (next < lines.length && !lines[next].trim()) next += 1;
    if (next >= lines.length || !removed[next]) return;
    let blank = false;
    while (next < lines.length && !kept(next)) {
      if (!lines[next].trim()) blank = true;
      next += 1;
    }
    if (next >= lines.length || headingText(lines[next]) !== null || (blank && !BULLET.test(lines[next]))) {
      removed[index] = true;
    }
  });
  return lines.filter((_line, index) => !removed[index]).join("\n");
}

/** 本文を段落・節に組み立てる。 */
function bodyBlocks(body: string, evidence: AnswerEvidenceData): StructuredBlock[] {
  const markers = evidence.markers.map((item) => item.text);
  const line = (raw: string): Line => ({
    raw,
    text: displayText(raw.replace(BULLET, ""), markers),
    chunkIds: [],
  });

  const blocks: StructuredBlock[] = [];
  chunks(body).forEach((lines, index) => {
    const key = `b${index}`;
    // 区切り線を含む塊と、見出しの無い段落の後ろに空行なしで箇条書きが続く塊（「---\n**根拠**\n- 文書 A.pdf」）は、
    // 段落の文（行の中の Markdown だけを描く）にすると `---` と `- ` が文字のまま出るので、Markdown の塊にする（#4061）。
    if (lines.some((item) => MARKDOWN_BLOCK.test(item) || RULE.test(item)) || mixedList(lines)) {
      const raw = lines.join("\n");
      blocks.push({ kind: "markdown", key, text: displayText(raw, markers), raw, chunkIds: [] });
      return;
    }
    let rest = lines;
    const title = headingText(lines[0]);
    if (title !== null) {
      rest = lines.slice(1);
      if (!rest.length) {
        blocks.push({ kind: "section", key, title, ordered: false, entries: [] });
        return;
      }
    }
    if (rest.every((item) => BULLET.test(item))) {
      const parsed = rest.map(line);
      blocks.push({
        kind: "section",
        key,
        title,
        ordered: rest.every((item) => /^\s*\d/.test(item)),
        entries: parsed.map((item, position) => ({
          key: `${key}-${position}`,
          text: item.text,
          raw: item.raw,
          chunkIds: item.chunkIds,
        })),
      });
      return;
    }
    if (title !== null) {
      // 見出しの下の段落は、見出しの節の項目にする（RAG の節と同じ形）。
      const parsed = rest.map(line);
      blocks.push({
        kind: "section",
        key,
        title,
        ordered: false,
        entries: parsed.map((item, position) => ({
          key: `${key}-${position}`,
          text: item.text,
          raw: item.raw,
          chunkIds: item.chunkIds,
        })),
      });
      return;
    }
    const raw = rest.join("\n");
    blocks.push({ kind: "paragraph", key, text: displayText(raw, markers), raw, chunkIds: [] });
  });
  merge(blocks);
  return blocks;
}

/**
 * 見出しの無い塊で、段落の文と Markdown の箇条書きの行が混ざる（箇条書きだけではない）とき true。
 * 見出しで始まる塊は、見出しの節の項目にする（今までどおり）。
 */
function mixedList(lines: readonly string[]): boolean {
  if (headingText(lines[0]) !== null) return false;
  return lines.some((item) => MARKDOWN_LIST_ITEM.test(item)) && !lines.every((item) => BULLET.test(item));
}

/** 空の見出しの節の直後の箇条書きは、その見出しの節にまとめる（見出しと箇条書きの間に空行がある書き方）。 */
function merge(blocks: StructuredBlock[]): void {
  for (let index = blocks.length - 2; index >= 0; index -= 1) {
    const current = blocks[index];
    const next = blocks[index + 1];
    if (current.kind === "section" && current.title && !current.entries.length && next.kind === "section" && !next.title) {
      blocks.splice(index, 2, { ...next, key: current.key, title: current.title });
    }
  }
}
