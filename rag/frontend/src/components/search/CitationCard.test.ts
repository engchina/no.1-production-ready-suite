import { describe, expect, it } from "vitest";

import type { RetrievedChunk } from "@/lib/api";
import {
  citationEvidenceItem,
  variantIdFromChunkId,
} from "./CitationCard";

describe("variantIdFromChunkId", () => {
  it("document:chunk_set:index 形式から chunk_set(variant)id を取り出す", () => {
    expect(variantIdFromChunkId("doc-1:cs_abc123:0")).toBe("cs_abc123");
  });

  it("chunk_set タグの無い document:index は variant 無し(null)", () => {
    expect(variantIdFromChunkId("doc-1:7")).toBeNull();
  });
});

describe("citationEvidenceItem", () => {
  // 共通の根拠のカード（#2328）に渡すデータの契約。見た目は platform の EvidenceCard のテストで確かめる。
  it("引用の印・経路・頁のチップ・レシピ・分類・原典を写す", () => {
    const item = citationEvidenceItem({
      document_id: "doc-1",
      chunk_id: "doc-1:cs_a:3",
      text: "根拠",
      score: 0.91,
      rerank_score: 0.42,
      file_name: null,
      category_name: "分類 A",
      metadata: {
        evidence_model_used: true,
        evidence_answer_use: "reference",
        vector_rank: 1,
        keyword_rank: 2,
        page_start: 3,
        recipe_slot_no: 2,
        document_superseded: true,
        xml_tree_source_url: "https://example.com/a",
      },
    } satisfies RetrievedChunk);
    expect(item).toMatchObject({
      key: "doc-1:cs_a:3",
      fileName: "doc-1",
      usedInAnswer: true,
      answerUse: "reference",
      superseded: true,
      score: 0.91,
      rerankScore: 0.42,
      sourceUrl: "https://example.com/a",
    });
    expect(item.routeBadges).toEqual(["Vector #1", "Keyword #2"]);
    expect(item.chips).toEqual([{ id: "page", label: "ページ", value: "p.3" }]);
    expect(item.tags?.map((tag) => [tag.id, tag.tone ?? "neutral"])).toEqual([
      ["recipe", "neutral"],
      ["category", "info"],
    ]);
  });
});

it.each(["not_run", "error", "timeout", "timed_out", "completed", "partial", "unknown"])("内容確認の%s状態は根拠のタグに表示せず、元の記録を保持する", (execution) => {
  const metadata = { evidence_model_used: true, evidence_answer_use: "confirmed", evidence_claim_support: { scope: "answer", execution, status: "unknown", policy: "diagnostic_only" } };
  const before = structuredClone(metadata);
  const item = citationEvidenceItem({ document_id: "d", chunk_id: "c", text: "原文", score: 1, rerank_score: null, file_name: null, category_name: null, metadata });
  expect(item.usedInAnswer).toBe(true);
  expect(item.answerUse).toBeNull();
  expect(item.tags).toEqual([]);
  expect(metadata).toEqual(before);
});

it.each([true, false, undefined])("文脈ヘッダの状態%sは表示せず、ヘッダ本文を残す", (enabled) => {
  const item = citationEvidenceItem({ document_id: "d", chunk_id: "c", text: "原文", score: 1, rerank_score: null, file_name: null, category_name: null, metadata: { context_header: "文書 A > 節 A", ...(enabled === undefined ? {} : { context_header_enabled: enabled }) } });
  expect(item.chips).toContainEqual({ id: "context_header", label: "検索用の文脈ヘッダ", value: "文書 A > 節 A" });
  expect(item.chips?.some((chip) => chip.id === "context_header_enabled")).toBe(false);
});
