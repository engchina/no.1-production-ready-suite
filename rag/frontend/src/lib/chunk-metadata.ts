import type { RetrievedChunk } from "./api";

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
