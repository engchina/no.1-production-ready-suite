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
}

/** 引用カードで表示する低 cardinality metadata を抽出する。 */
export function citationMetadataChips(
  metadata: RetrievedChunk["metadata"]
): CitationMetadataChip[] {
  const chips: CitationMetadataChip[] = [];
  const page = pageRange(metadata);
  if (page) chips.push({ id: "page", value: page });
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
