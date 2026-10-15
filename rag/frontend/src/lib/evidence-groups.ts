import type { RetrievedChunk } from "./api";
import { parseAnswerDiagnostics } from "./answer-diagnostics";
import { citationMetadataChips } from "./chunk-metadata";
import { t } from "./i18n";

/**
 * 回答の根拠のまとまり（#2097）。回答エンジンは AI に渡した箇所（chunk）を、同じ文書の同じ節（親の chunk）
 * ごとにまとめて渡す（根拠の木 `evidence_tree`）。画面の「まとまりごと」は、この木を AI に渡した箇所
 * （citations）に引き当てて作るので、まとまりの件数の合計は「AI に渡した N 件」の N と一致する。
 */
export interface EvidenceGroup {
  key: string;
  fileName: string;
  /** まとまりの箇所のページの範囲（「3」「3-5」）。ページの無い文書は空。 */
  pages: string;
  /** まとまりの章節（最初に見出しを持つ箇所の見出し）。無ければ空。 */
  section: string;
  items: EvidenceGroupItem[];
}

export interface EvidenceGroupItem {
  chunk: RetrievedChunk;
  /** 「順に表示」の番号（0 始まり）。2 つの見方で同じ番号を出す。 */
  index: number;
  /** 根拠の役割（回答エンジンの値。表示は {@link evidenceRoleLabel}）。 */
  role: string;
}

/**
 * AI に渡した箇所をまとまりごとに並べる。根拠の木の無い回答（古い記録・回答を作らない検索の一部）は null。
 * 木に無い箇所は文書ごとのまとまりにして末尾に足し、どの箇所も 1 回だけ数える。
 */
export function evidenceGroups(
  citations: readonly RetrievedChunk[],
  diagnostics: unknown,
): EvidenceGroup[] | null {
  const tree = parseAnswerDiagnostics(diagnostics)?.tree ?? [];
  if (!tree.length || !citations.length) return null;
  const byId = new Map(citations.map((chunk, index) => [chunk.chunk_id, index]));
  const placed = new Set<number>();
  const groups: EvidenceGroup[] = [];
  tree.forEach((parent, position) => {
    const items: EvidenceGroupItem[] = [];
    for (const child of parent.children) {
      const index = byId.get(child.chunkId);
      if (index === undefined || placed.has(index)) continue;
      placed.add(index);
      const chunk = citations[index];
      items.push({ chunk, index, role: evidenceRole(chunk, child.role) });
    }
    if (items.length) {
      groups.push(group(`${position}:${parent.parentId}`, parent.source, items));
    }
  });
  const rest = new Map<string, EvidenceGroupItem[]>();
  citations.forEach((chunk, index) => {
    if (placed.has(index)) return;
    const items = rest.get(chunk.document_id) ?? [];
    items.push({ chunk, index, role: evidenceRole(chunk, "") });
    rest.set(chunk.document_id, items);
  });
  for (const [documentId, items] of rest) {
    groups.push(group(`rest:${documentId}`, "", items));
  }
  return groups.length ? groups : null;
}

function group(key: string, source: string, items: EvidenceGroupItem[]): EvidenceGroup {
  const first = items[0].chunk;
  return {
    key,
    fileName: first.file_name || source || first.document_id,
    pages: pageSpan(items.map((item) => item.chunk)),
    section: items.map((item) => sectionOf(item.chunk)).find(Boolean) ?? "",
    items,
  };
}

function sectionOf(chunk: RetrievedChunk): string {
  const chips = citationMetadataChips(chunk.metadata);
  return (
    chips.find((chip) => chip.id === "section_title")?.value ??
    chips.find((chip) => chip.id === "section_path")?.value ??
    ""
  );
}

/** 引用の metadata の役割（実体の拡張は backend が付け直す。#1362）を、根拠の木の役割より優先する。 */
function evidenceRole(chunk: RetrievedChunk, treeRole: string): string {
  const value = chunk.metadata.evidence_role;
  return typeof value === "string" && value ? value : treeRole;
}

function pageSpan(chunks: readonly RetrievedChunk[]): string {
  const pages = chunks.flatMap((chunk) => {
    const start = integer(chunk.metadata.page_start);
    const end = integer(chunk.metadata.page_end) ?? start;
    return start === null ? [] : [start, end ?? start];
  });
  if (!pages.length) return "";
  const min = Math.min(...pages);
  const max = Math.max(...pages);
  return max > min ? `${min}-${max}` : String(min);
}

function integer(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? Math.trunc(value) : null;
}

/** 根拠の役割の区分。表示名とアイコンを区分ごとに持つ（色だけで区別しない）。 */
export type EvidenceRoleKind = "retrieved" | "neighbor" | "context";

export function evidenceRoleKind(role: string): EvidenceRoleKind {
  if (role === "retrieved_anchor") return "retrieved";
  if (role === "neighbor_context") return "neighbor";
  return "context";
}

/** 根拠の役割の利用者の言葉。回答エンジンの値（英語）は画面に出さない（#2097）。 */
export function evidenceRoleLabel(role: string): string {
  return t(`evidence.role.${evidenceRoleKind(role)}`);
}

/** 箇所の読める位置（引用ラベル・ページ・見出し）。無ければ空。 */
export function evidenceLocation(chunk: RetrievedChunk): string {
  const chips = citationMetadataChips(chunk.metadata);
  const parts: string[] = [];
  for (const chip of chips) {
    if (chip.id === "citation_label") parts.push(chip.value);
    if (chip.id === "page") parts.push(t("search.citation.page", { page: chip.value }));
  }
  const section = sectionOf(chunk);
  if (section) parts.push(section);
  return parts.join(t("evidence.locationSeparator"));
}

/** 本文の抜粋（空白をつめ、長ければ末尾を省く）。 */
export function evidenceExcerpt(text: string, maxChars = 120): string {
  const compact = text.replace(/\s+/g, " ").trim();
  return compact.length > maxChars ? `${compact.slice(0, maxChars)}…` : compact;
}
