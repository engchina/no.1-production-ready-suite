import { passagePageLabel } from "@production-ready/ui";
import {
  request,
  type RetrievedChunk,
  type WikiGraphOverview,
  type WikiGraphRelation,
  type WikiGraphRelationItem,
  type WikiGraphSearchItem,
  type WikiGraphTreePage,
  type WikiGraphUnitRef,
  type WikiGraphLocation,
} from "@/lib/api";
import { t } from "@/lib/i18n";

export type WikiMethod = "hybrid" | "vector" | "keyword";
export type WikiScope = { kind: "profile" | "kb"; id: string; kb?: string };
export type WikiKb = { id: string; name: string };
export type WikiHit = WikiGraphSearchItem & {
  knowledge_base_ids: string[];
  vector_rank: number | null;
  keyword_rank: number | null;
};
export interface SourceRef {
  unit_id: string;
  source_hash: string;
}
export interface WikiPick {
  id: string;
  name: string;
  location: string;
  fullLocation?: string;
  /** 原文の名前と文書（関係図の節点に使う。#5981）。 */
  label?: string;
  documentId?: string;
  source: SourceRef;
}
export interface WikiOriginal extends WikiGraphUnitRef {
  parent_id: string | null;
  source: SourceRef;
  chunks: RetrievedChunk[];
  context: string;
  possible_questions: string[];
  unresolved: string[];
}
export type WikiDirection = WikiGraphRelationItem["direction"];
export interface WikiDirectedGroup {
  relation: WikiGraphRelation;
  direction: WikiDirection;
  count: number;
  neighbor_count: number;
}
export interface WikiRelations {
  groups?: WikiDirectedGroup[];
  connections?: { source_id: string; target_id: string; relation: WikiGraphRelation }[];
  preview: WikiGraphRelationItem[];
  items: WikiGraphRelationItem[];
  counts: Partial<Record<WikiGraphRelation, number>>;
  total: number;
  has_more: boolean;
}
export interface WikiDocumentGraph {
  documents: (WikiGraphOverview["documents"][number] & { reference_count: number })[];
  links: { source_id: string; target_id: string; relation: WikiGraphRelation; count: number }[];
  total: number;
  offset: number;
  has_more: boolean;
}
export interface WikiUnitConnection {
  source_id: string;
  target_id: string;
  relation: WikiGraphRelation;
  scope: WikiGraphRelationItem["scope"];
}
/** 単位の関係は 1 回で取り切る（#5916）。 */
export const WIKI_RELATIONS_LIMIT = 500;
/** `connections` が 1 回で受け付ける単位の数（backend の上限）。 */
export const WIKI_CONNECTIONS_LIMIT = 200;
/** 選んだ原文（とその文書の先頭の chunk）だけを根拠にする（only。既定）か、通常の検索の結果に足す（mix）か（#5981）。 */
export type WikiSourceMode = "only" | "mix";
export interface WikiDraft {
  knowledgeBaseId?: string;
  profileId: string;
  items: WikiPick[];
  /** 一度に選べる原文の上限（backend の `selection_limit`）。 */
  selectionLimit?: number;
  mode?: WikiSourceMode;
}
export function isWikiDraft(value: unknown): value is WikiDraft {
  if (
    !value ||
    typeof value !== "object" ||
    !("profileId" in value) ||
    typeof value.profileId !== "string" ||
    ("knowledgeBaseId" in value && typeof value.knowledgeBaseId !== "string") ||
    ("selectionLimit" in value && value.selectionLimit !== undefined && !validLimit(value.selectionLimit)) ||
    ("mode" in value && value.mode !== undefined && value.mode !== "only" && value.mode !== "mix") ||
    !("items" in value) ||
    !Array.isArray(value.items)
  )
    return false;
  return value.items.every(
    (item) =>
      item &&
      typeof item.id === "string" &&
      typeof item.name === "string" &&
      typeof item.location === "string" &&
      (item.fullLocation === undefined || typeof item.fullLocation === "string") &&
      (item.label === undefined || typeof item.label === "string") &&
      (item.documentId === undefined || typeof item.documentId === "string") &&
      item.source?.unit_id === item.id &&
      typeof item.source.source_hash === "string",
  );
}
/** 一度に選べる原文の上限の予備の値（backend の `selection_limit` が応答に無いとき。#5981）。 */
export const WIKI_SELECTION_LIMIT_FALLBACK = 10;
function validLimit(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 0;
}
/** backend が返した上限（無い・不正なら予備の値）。 */
export function wikiSelectionLimit(value: unknown): number {
  return validLimit(value) ? value : WIKI_SELECTION_LIMIT_FALLBACK;
}
function get<T>(scope: WikiScope, path: string, params: Record<string, string> = {}, signal?: AbortSignal) {
  const id = encodeURIComponent(scope.id);
  const base =
    scope.kind === "profile" ? `/api/wiki-search/profiles/${id}` : `/api/knowledge-bases/${id}/wiki/explore`;
  const query = new URLSearchParams(params);
  if (scope.kb && scope.kind === "profile") query.set("kb", scope.kb);
  return request<T>(`${base}/${path}?${query}`, { signal });
}
export const wikiApi = {
  scope: (s: WikiScope, signal?: AbortSignal) => get<{ knowledge_bases: WikiKb[]; selection_limit?: number }>(s, "scope", {}, signal),
  hits: (s: WikiScope, q: string, method: WikiMethod, offset: number, limit: number, signal?: AbortSignal) =>
    get<{ items: WikiHit[]; total: number }>(
      s,
      "search",
      { q, method, offset: String(offset), limit: String(limit) },
      signal,
    ),
  tree: (
    s: WikiScope,
    doc: string,
    parent: string | null,
    offset: number,
    limit: number,
    signal?: AbortSignal,
  ) =>
    get<WikiGraphTreePage>(
      s,
      `documents/${encodeURIComponent(doc)}/tree`,
      { ...(parent ? { parent } : {}), offset: String(offset), limit: String(limit) },
      signal,
    ),
  original: (s: WikiScope, id: string, signal?: AbortSignal) =>
    get<WikiOriginal>(s, `units/${encodeURIComponent(id)}`, {}, signal),
  relations: (
    s: WikiScope,
    id: string,
    group: WikiGraphRelation | "",
    offset: number,
    limit: number,
    signal?: AbortSignal,
    direction?: WikiDirection,
  ) =>
    get<WikiRelations>(
      s,
      `units/${encodeURIComponent(id)}/relations`,
      { ...(group ? { relation: group } : {}), ...(direction ? { direction } : {}), offset: String(offset), limit: String(limit) },
      signal,
    ),
  allRelations: (s: WikiScope, id: string, signal?: AbortSignal) =>
    get<WikiRelations>(s, `units/${encodeURIComponent(id)}/relations`, { offset: "0", limit: String(WIKI_RELATIONS_LIMIT) }, signal),
  documentGraph: (s: WikiScope, offset: number, limit: number, document?: string, signal?: AbortSignal) =>
    get<WikiDocumentGraph>(s, "document-graph", { offset: String(offset), limit: String(limit), ...(document ? { document } : {}) }, signal),
  connections: (s: WikiScope, ids: readonly string[], signal?: AbortSignal) =>
    get<{ connections: WikiUnitConnection[] }>(s, "connections", { ids: ids.join(",") }, signal),
  locate: (s: WikiScope, document: string, node: string, signal?: AbortSignal) =>
    get<WikiGraphLocation>(s, `documents/${encodeURIComponent(document)}/locate`, { node }, signal),
};

function sourcePosition(chunk: RetrievedChunk | undefined, fallback: string) {
  if (!chunk) return { short: fallback, full: fallback };
  const page = passagePageLabel(chunk);
  if (page) return { short: page, full: page };
  const metadata = chunk.metadata;
  const locator = metadata.source_locator;
  const m =
    locator && typeof locator === "object"
      ? { ...metadata, ...(locator as Record<string, unknown>) }
      : metadata;
  const from = Number(m.source_line_start ?? m.line_start ?? m.row_start);
  const to = Number(m.source_line_end ?? m.line_end ?? m.row_end);
  if (from > 0) {
    const lines = t("wiki.search.lines", { range: to > from ? `${from}–${to}` : String(from) });
    return { short: lines, full: lines };
  }
  const citation = String(m.xml_tree_citation_label || fallback);
  return { short: fallback || citation, full: citation };
}
export function sourceLocation(chunk: RetrievedChunk | undefined, fallback: string): string {
  return sourcePosition(chunk, fallback).full;
}
export function sourcePick(original: WikiOriginal): WikiPick {
  const location = sourcePosition(original.chunks[0], original.label);
  const path = original.path;
  return {
    id: original.id,
    name: original.document_name,
    label: original.label,
    documentId: original.document_id,
    source: original.source,
    location: location.short,
    fullLocation: (path.at(-1) === location.full ? path : [...path, location.full]).join(" › "),
  };
}
