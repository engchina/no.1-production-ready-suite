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
export interface WikiRelations {
  preview: WikiGraphRelationItem[];
  items: WikiGraphRelationItem[];
  counts: Partial<Record<WikiGraphRelation, number>>;
  total: number;
  has_more: boolean;
}
export interface WikiDraft {
  knowledgeBaseId?: string;
  profileId: string;
  items: WikiPick[];
}
export function isWikiDraft(value: unknown): value is WikiDraft {
  if (
    !value ||
    typeof value !== "object" ||
    !("profileId" in value) ||
    typeof value.profileId !== "string" ||
    ("knowledgeBaseId" in value && typeof value.knowledgeBaseId !== "string") ||
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
      item.source?.unit_id === item.id &&
      typeof item.source.source_hash === "string",
  );
}
export const MAX_WIKI_SELECTION = 200;
function get<T>(scope: WikiScope, path: string, params: Record<string, string> = {}, signal?: AbortSignal) {
  const id = encodeURIComponent(scope.id);
  const base =
    scope.kind === "profile" ? `/api/wiki-search/profiles/${id}` : `/api/knowledge-bases/${id}/wiki/explore`;
  const query = new URLSearchParams(params);
  if (scope.kb && scope.kind === "profile") query.set("kb", scope.kb);
  return request<T>(`${base}/${path}?${query}`, { signal });
}
export const wikiApi = {
  scope: (s: WikiScope, signal?: AbortSignal) => get<{ knowledge_bases: WikiKb[] }>(s, "scope", {}, signal),
  hits: (s: WikiScope, q: string, method: WikiMethod, offset: number, limit: number, signal?: AbortSignal) =>
    get<{ items: WikiHit[]; total: number }>(
      s,
      "search",
      { q, method, offset: String(offset), limit: String(limit) },
      signal,
    ),
  documents: (s: WikiScope, offset: number, limit: number, signal?: AbortSignal) =>
    get<WikiGraphOverview>(s, "documents", { offset: String(offset), limit: String(limit) }, signal),
  docCount: (s: WikiScope, signal?: AbortSignal) => get<{ total: number }>(s, "document-count", {}, signal),
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
  ) =>
    get<WikiRelations>(
      s,
      `units/${encodeURIComponent(id)}/relations`,
      { ...(group ? { relation: group } : {}), offset: String(offset), limit: String(limit) },
      signal,
    ),
};
export function sourceLocation(chunk: RetrievedChunk | undefined, fallback: string): string {
  if (!chunk) return fallback;
  const page = passagePageLabel(chunk);
  if (page) return page;
  const metadata = chunk.metadata;
  const locator = metadata.source_locator;
  const m =
    locator && typeof locator === "object"
      ? { ...metadata, ...(locator as Record<string, unknown>) }
      : metadata;
  const from = Number(m.source_line_start ?? m.line_start ?? m.row_start);
  const to = Number(m.source_line_end ?? m.line_end ?? m.row_end);
  if (from > 0) return t("wiki.search.lines", { range: to > from ? `${from}–${to}` : String(from) });
  return String(m.xml_tree_citation_label || fallback);
}
export function sourcePick(original: WikiOriginal): WikiPick {
  return {
    id: original.id,
    name: original.document_name,
    source: original.source,
    location: sourceLocation(original.chunks[0], original.label),
  };
}
