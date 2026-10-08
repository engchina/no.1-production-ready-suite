import type { RetrievedChunk } from "./api";
import { t } from "./i18n";

export type CitationMetadataChipId =
  | "page"
  | "content_kind"
  | "section_title"
  | "section_path"
  | "chunk_profile";

export interface CitationMetadataChip {
  id: CitationMetadataChipId;
  value: string;
  /**
   * page のときだけ: 資料に印刷されたページ番号（PDF のページラベル。#1244）。物理頁と違うときだけ
   * 入る（backend の `page_label_start` / `page_label_end`。無ければ省く）。
   */
  printed?: string;
}

/** 引用カードで表示する低 cardinality metadata を抽出する。 */
export function citationMetadataChips(
  metadata: RetrievedChunk["metadata"]
): CitationMetadataChip[] {
  const chips: CitationMetadataChip[] = [];
  const page = pageRange(metadata);
  if (page) {
    const printed = printedPageRange(metadata, page);
    chips.push(printed ? { id: "page", value: page, printed } : { id: "page", value: page });
  }
  for (const id of ["content_kind", "section_title", "section_path", "chunk_profile"] as const) {
    const value = stringMetadata(metadata, id);
    if (value) chips.push({ id, value });
  }
  return chips;
}

/** citation preview link に渡す先頭 element id を安全に取り出す。 */
export function firstCitationElementId(value: unknown): string | null {
  if (typeof value === "string" || typeof value === "number") {
    return firstNonEmptyToken(String(value).split(","));
  }
  if (Array.isArray(value)) {
    return firstNonEmptyToken(
      value.flatMap((item) =>
        typeof item === "string" || typeof item === "number" ? [String(item)] : []
      )
    );
  }
  return null;
}

function pageRange(metadata: RetrievedChunk["metadata"]): string {
  const start = integerMetadata(metadata, "page_start");
  const end = integerMetadata(metadata, "page_end") ?? start;
  if (start == null) return "";
  return end != null && end > start ? `${start}-${end}` : String(start);
}

/**
 * 資料に印刷されたページ番号の範囲（#1244）。backend は物理頁と違うラベルがあるときだけ
 * `page_label_start` / `page_label_end` を入れる。印刷の番号は「3-4」のようにハイフンを含むことが
 * あるため、範囲は「〜」でつなぐ。物理頁と同じ表記になるときは出さない（空文字）。
 */
export function printedPageRange(metadata: RetrievedChunk["metadata"], physical: string): string {
  const start = stringMetadata(metadata, "page_label_start");
  if (!start) return "";
  const end = stringMetadata(metadata, "page_label_end") || start;
  const printed = end !== start ? `${start}〜${end}` : start;
  return printed === physical ? "" : printed;
}

function integerMetadata(metadata: RetrievedChunk["metadata"], key: string): number | null {
  const value = metadata[key];
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return Math.trunc(value);
}

function stringMetadata(metadata: RetrievedChunk["metadata"], key: string): string {
  const value = metadata[key];
  return typeof value === "string" ? value.trim() : "";
}

function firstNonEmptyToken(values: string[]): string | null {
  const first = values.map((item) => item.trim()).find(Boolean);
  return first ?? null;
}

/**
 * 回答に使った根拠か（#1208）。回答エンジンは回答の文脈に入れた根拠の chunk を全件 citations にし、
 * 回答に使ったものを `evidence_model_used` で示す。回答を作らない検索の結果は持たないので null。
 */
export function citationModelUsed(chunk: Pick<RetrievedChunk, "metadata">): boolean | null {
  const value = chunk.metadata.evidence_model_used;
  return typeof value === "boolean" ? value : null;
}

/** 根拠の件数の表示（#1208）。回答に使ったかが分かるときは「根拠 N 件（回答に使用 M 件）」、分からないときは「根拠 N 件」。 */
export function citationCountLabel(citations: readonly Pick<RetrievedChunk, "metadata">[]): string {
  const flags = citations.map(citationModelUsed);
  if (flags.every((flag) => flag === null)) {
    return t("citations.count", { count: citations.length });
  }
  return t("citations.countWithUsed", {
    count: citations.length,
    used: flags.filter((flag) => flag === true).length,
  });
}
