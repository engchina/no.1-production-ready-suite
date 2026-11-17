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

describe("citationEvidenceItem の章節・内容種別のチップ（issue 5303）", () => {
  const chunk = (metadata: RetrievedChunk["metadata"]): RetrievedChunk => ({
    document_id: "d",
    chunk_id: "c",
    text: "原文",
    score: 1,
    rerank_score: null,
    file_name: "合成の表.xlsx",
    category_name: null,
    metadata,
  });

  it("表の行は「表の行」と章節 1 つだけを出し、文脈ヘッダ・分割方針を出さない", () => {
    const item = citationEvidenceItem(
      chunk({
        content_kind: "record",
        section_title: "シート A",
        section_path: "シート A",
        chunk_profile: "structure_v1",
        context_header: "合成の表.xlsx > シート A",
        context_header_enabled: true,
        recipe_slot_no: 1,
      })
    );
    expect(item.chips).toEqual([
      { id: "content_kind", label: "内容種別", value: "表の行" },
      { id: "section_path", label: "見出しの階層", value: "シート A" },
    ]);
    expect(item.tags?.map((tag) => tag.id)).toEqual(["recipe"]);
  });

  it.each([true, false, undefined])("文脈ヘッダ（状態 %s）は根拠のカードに出さない", (enabled) => {
    const item = citationEvidenceItem(
      chunk({ context_header: "文書 A > 節 A", ...(enabled === undefined ? {} : { context_header_enabled: enabled }) })
    );
    expect(item.chips).toEqual([]);
  });
});
