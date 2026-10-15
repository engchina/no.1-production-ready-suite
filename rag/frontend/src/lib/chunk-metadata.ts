import type { RetrievedChunk } from "./api";
import { t } from "./i18n";

export type CitationMetadataChipId =
  | "citation_label"
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
  // XML ツリー JSON の段落の引用ラベル（#2024）。ページの無い原本なので頁より先に出す。
  const citationLabel = stringMetadata(metadata, "xml_tree_citation_label_arabic");
  if (citationLabel) chips.push({ id: "citation_label", value: citationLabel });
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

/** 原典の URL（XML ツリー JSON の文書。#2024）。https の URL のときだけ返す。 */
export function citationSourceUrl(metadata: RetrievedChunk["metadata"]): string | null {
  const url = stringMetadata(metadata, "xml_tree_source_url");
  return url.startsWith("https://") ? url : null;
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
 * 回答で引用した箇所か（#1208）。回答エンジンは AI に渡した箇所（chunk）を全件 citations にし、
 * 回答で引用したものを `evidence_model_used` で示す。回答を作らない検索の結果は持たないので null。
 */
export function citationModelUsed(chunk: Pick<RetrievedChunk, "metadata">): boolean | null {
  const value = chunk.metadata.evidence_model_used;
  return typeof value === "boolean" ? value : null;
}

/** 回答での使われ方（#2040）。confirmed は質問の条件への該当確認済みの記載、reference は参考の記載。 */
export type CitationAnswerUse = "confirmed" | "reference";

/**
 * 回答で引用した箇所の使われ方（`evidence_answer_use`。#2040）。回答で引用していない箇所と、
 * 使われ方を記録していない回答は null。
 */
export function citationAnswerUse(chunk: Pick<RetrievedChunk, "metadata">): CitationAnswerUse | null {
  if (citationModelUsed(chunk) !== true) return null;
  const value = chunk.metadata.evidence_answer_use;
  return value === "confirmed" || value === "reference" ? value : null;
}

/**
 * AI に渡した件数と内訳（#1208・#2040）。「AI に渡した 45 件（12 のまとまり）／ 回答で引用 13 件 ／ うち該当確認済み
 * 10 件 ／ 参考 3 件」。まとまり（同じ節ごとにまとめて渡した単位。#2097）は数が分かるときだけ、回答で引用したかが
 * 分からない（検索だけ）なら件数だけ、0 件の項目は出さない。
 */
export function citationCountLabel(
  citations: readonly Pick<RetrievedChunk, "metadata">[],
  groupCount: number | null = null,
): string {
  const parts = [
    groupCount
      ? t("citations.countWithGroups", { count: citations.length, groups: groupCount })
      : t("citations.count", { count: citations.length }),
  ];
  const flags = citations.map(citationModelUsed);
  if (flags.some((flag) => flag !== null)) {
    const used = flags.filter((flag) => flag === true).length;
    const uses = citations.map(citationAnswerUse);
    const confirmed = uses.filter((use) => use === "confirmed").length;
    const reference = uses.filter((use) => use === "reference").length;
    if (used) parts.push(t("citations.used", { count: used }));
    if (confirmed) parts.push(t("citations.confirmed", { count: confirmed }));
    if (reference) parts.push(t("citations.reference", { count: reference }));
  }
  return parts.join(t("citations.separator"));
}
