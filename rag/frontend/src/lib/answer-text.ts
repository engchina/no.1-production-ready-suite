import type { RetrievedChunk } from "./api";
import { integerMetadataValue } from "./table-cell-focus";

/**
 * 回答本文の構成（#651）。回答エンジン（rag_engine の `grounded.render()`）は本文を決まった書式で組み立てる:
 * 要約の段落、節の見出し（「確認できる内容」「操作手順（機能名）」など）、「・」か「N. 」で始まる説明、
 * 説明の直後の「根拠：ファイル名 p.N」の行。画面はこの書式で節・説明・根拠に分けて出す。
 * 書式の見出しが 1 つも無い本文（根拠不足の案内など）は null（そのままの文字列で出す）。
 */

export type AnswerTextEntry =
  | { kind: "item"; text: string; citations: string[] }
  | { kind: "text"; text: string };

export type AnswerTextBlock =
  | { kind: "paragraph"; text: string }
  | { kind: "section"; title: string; ordered: boolean; entries: AnswerTextEntry[] };

// rag_engine の grounded.py の RULES_SECTION_TITLE / QUOTE_ONLY_LABEL / GAPS_SECTION_TITLE と手順の見出し。
const SECTION_TITLE =
  /^(?:確認できる内容|資料の記載（今回への適用は未確認）|資料からは確認できない点|(?:操作|確認)手順(?:（.+）)?)$/;
const CITATION = /^根拠：/;
const BULLET = /^・/;
const NUMBERED = /^\d+\.\s/;

export function parseAnswerText(text: string): AnswerTextBlock[] | null {
  const lines = text.split("\n");
  if (!lines.some((line) => SECTION_TITLE.test(line.trim()))) return null;
  const blocks: AnswerTextBlock[] = [];
  let section: Extract<AnswerTextBlock, { kind: "section" }> | null = null;
  let paragraph: string[] = [];
  const flushParagraph = () => {
    const joined = paragraph.join("\n").trim();
    paragraph = [];
    if (!joined) return;
    if (section) section.entries.push({ kind: "text", text: joined });
    else blocks.push({ kind: "paragraph", text: joined });
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      flushParagraph();
      continue;
    }
    if (SECTION_TITLE.test(line)) {
      flushParagraph();
      section = { kind: "section", title: line, ordered: true, entries: [] };
      blocks.push(section);
      continue;
    }
    if (section && (BULLET.test(line) || NUMBERED.test(line))) {
      flushParagraph();
      if (!NUMBERED.test(line)) section.ordered = false;
      section.entries.push({ kind: "item", text: line.replace(BULLET, "").replace(NUMBERED, ""), citations: [] });
      continue;
    }
    const last = section?.entries[section.entries.length - 1];
    if (CITATION.test(line) && last?.kind === "item" && paragraph.length === 0) {
      last.citations.push(line);
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  for (const block of blocks) {
    if (block.kind === "section" && !block.entries.some((entry) => entry.kind === "item")) {
      block.ordered = false;
    }
  }
  return blocks;
}

/**
 * 根拠の行（「根拠：ファイル名 p.N」、表計算は「根拠：ファイル名 シート「名前」A3:D6」。rag_engine の
 * grounded.py の `_citation`）のファイル名と頁・シート・行（#657 / #1224）。
 */
export interface AnswerCitationRef {
  fileName: string;
  page: number | null;
  /** 表計算のシート名（#1224）。頁の根拠では null。 */
  sheet?: string | null;
  /** シートの行の範囲（セル範囲から読む）。 */
  rowStart?: number | null;
  rowEnd?: number | null;
}

const CITATION_LINE = /^根拠：(.+?)(?:\s+p\.(\d*))?$/;
const SHEET_CITATION_LINE = /^根拠：(.+?)\s+シート「(.+)」(?:[A-Z]+(\d+):[A-Z]+(\d+))?$/;

export function parseCitationLine(line: string): AnswerCitationRef | null {
  const trimmed = line.trim();
  const sheet = SHEET_CITATION_LINE.exec(trimmed);
  if (sheet) {
    const fileName = sheet[1].trim();
    if (!fileName) return null;
    return {
      fileName,
      page: null,
      sheet: sheet[2],
      rowStart: sheet[3] ? Number(sheet[3]) : null,
      rowEnd: sheet[4] ? Number(sheet[4]) : null,
    };
  }
  const match = CITATION_LINE.exec(trimmed);
  if (!match) return null;
  const fileName = match[1].trim();
  if (!fileName) return null;
  return { fileName, page: match[2] ? Number(match[2]) : null };
}

function normalizedFileName(value: string): string {
  return value.normalize("NFKC").trim().toLowerCase();
}

function chunkPages(chunk: RetrievedChunk): [number, number] {
  // backend の _stored_child と同じ頁（page_start、無ければ page_number、どちらも無ければ 1）。
  const start =
    integerMetadataValue(chunk.metadata.page_start) ??
    integerMetadataValue(chunk.metadata.page_number) ??
    1;
  const end = integerMetadataValue(chunk.metadata.page_end) ?? start;
  return [start, Math.max(start, end)];
}

/**
 * 根拠の行に当たる引用の位置（#657）。同じファイルのうち、頁の範囲に入る引用を順位の順に選び、無ければ
 * 頁の近い引用にする。同じファイルの引用が無ければ -1（押せる見た目にしない）。
 */
export function matchCitation(ref: AnswerCitationRef, citations: readonly RetrievedChunk[]): number {
  const target = normalizedFileName(ref.fileName);
  const candidates = citations
    .map((chunk, index) => ({ chunk, index }))
    .filter(({ chunk }) => normalizedFileName(chunk.file_name ?? "") === target);
  if (!candidates.length) return -1;
  if (ref.sheet) return matchSheetCitation(ref, candidates);
  const page = ref.page;
  if (page == null) return candidates[0].index;
  let best = candidates[0];
  let bestDistance = Number.POSITIVE_INFINITY;
  for (const candidate of candidates) {
    const [start, end] = chunkPages(candidate.chunk);
    const distance = page < start ? start - page : page > end ? page - end : 0;
    if (distance < bestDistance) {
      best = candidate;
      bestDistance = distance;
    }
  }
  return best.index;
}

/** 表計算の根拠の行に当たる引用（同じシートで、行の範囲が重なる引用を順位の順に。#1224）。 */
function matchSheetCitation(
  ref: AnswerCitationRef,
  candidates: { chunk: RetrievedChunk; index: number }[]
): number {
  const sameSheet = candidates.filter(({ chunk }) => chunk.metadata.sheet_name === ref.sheet);
  if (!sameSheet.length) return candidates[0].index;
  const start = ref.rowStart ?? null;
  const end = ref.rowEnd ?? start;
  if (start == null || end == null) return sameSheet[0].index;
  const overlapping = sameSheet.find(({ chunk }) => {
    const chunkStart = integerMetadataValue(chunk.metadata.row_start);
    const chunkEnd = integerMetadataValue(chunk.metadata.row_end) ?? chunkStart;
    return chunkStart != null && chunkEnd != null && chunkStart <= end && start <= chunkEnd;
  });
  return (overlapping ?? sameSheet[0]).index;
}
