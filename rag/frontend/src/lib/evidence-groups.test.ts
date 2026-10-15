import { describe, expect, it } from "vitest";

import type { JsonValue, RetrievedChunk } from "./api";
import { citationCountLabel } from "./chunk-metadata";
import {
  evidenceExcerpt,
  evidenceGroups,
  evidenceLocation,
  evidenceRoleLabel,
} from "./evidence-groups";

function chunk(id: string, metadata: Record<string, JsonValue> = {}, documentId = "doc-a"): RetrievedChunk {
  return {
    document_id: documentId,
    chunk_id: id,
    text: `本文 ${id}`,
    score: 0.5,
    rerank_score: null,
    file_name: `${documentId}.pdf`,
    category_name: null,
    metadata,
  };
}

function diagnostics(tree: { parent: string; children: [string, string][] }[]) {
  return {
    evidence_tree: tree.map((parent) => ({
      parent_id: parent.parent,
      source: "",
      page: null,
      children: parent.children.map(([chunkId, role]) => ({ chunk_id: chunkId, role })),
    })),
  };
}

describe("evidenceGroups", () => {
  it("まとまりの件数の合計は AI に渡した件数と一致し、どの箇所も 1 回だけ数える（Issue 2097）", () => {
    const citations = [
      chunk("a:1", { page_start: 3 }),
      chunk("a:2", { page_start: 4 }),
      chunk("b:1", {}, "doc-b"),
      // 根拠の木に無い箇所（古い記録など）は文書ごとのまとまりにする。
      chunk("c:1", {}, "doc-c"),
    ];
    const groups = evidenceGroups(
      citations,
      diagnostics([
        { parent: "pa", children: [["a:1", "retrieved_anchor"], ["a:2", "neighbor_context"], ["gone", "parent_context"]] },
        // 前のまとまりと同じ箇所は数えない。
        { parent: "pb", children: [["b:1", "operation_context"], ["a:1", "operation_context"]] },
        // 引用に入らない箇所だけのまとまりは出さない。
        { parent: "pc", children: [["gone", "section_context"]] },
      ]),
    );
    expect(groups).not.toBeNull();
    const counts = groups!.map((group) => group.items.length);
    expect(counts).toEqual([2, 1, 1]);
    expect(counts.reduce((sum, value) => sum + value, 0)).toBe(citations.length);
    expect(groups![0]).toMatchObject({ fileName: "doc-a.pdf", pages: "3-4" });
    expect(groups![0].items.map((item) => item.index)).toEqual([0, 1]);
    expect(citationCountLabel(citations, groups!.length)).toBe("AI に渡した 4 件（3 のまとまり）");
  });

  it("根拠の木の無い回答は null（一覧だけ出す）", () => {
    expect(evidenceGroups([chunk("a:1")], { evidence_tree: [] })).toBeNull();
    expect(evidenceGroups([chunk("a:1")], null)).toBeNull();
    expect(citationCountLabel([chunk("a:1")], null)).toBe("AI に渡した 1 件");
  });

  it("引用の metadata の役割（実体の拡張）を根拠の木の役割より優先する", () => {
    const groups = evidenceGroups(
      [chunk("a:1", { evidence_role: "entity_expansion" })],
      diagnostics([{ parent: "pa", children: [["a:1", "retrieved_anchor"]] }]),
    );
    expect(groups![0].items[0].role).toBe("entity_expansion");
  });
});

describe("evidenceRoleLabel", () => {
  it("英語の役割の値を画面に出さない", () => {
    expect(evidenceRoleLabel("retrieved_anchor")).toBe("検索で見つかった箇所");
    expect(evidenceRoleLabel("neighbor_context")).toBe("前後の文脈");
    expect(evidenceRoleLabel("same_page_context")).toBe("同じページの箇所");
    expect(evidenceRoleLabel("parent_context")).toBe("同じ節のほかの箇所");
    expect(evidenceRoleLabel("operation_context")).toBe("関連する手順");
    expect(evidenceRoleLabel("visual_context")).toBe("関連する図・画面");
    expect(evidenceRoleLabel("section_context")).toBe("上位の章節から読んだ箇所");
    expect(evidenceRoleLabel("entity_expansion")).toBe("関係情報でたどった箇所");
    expect(evidenceRoleLabel("unknown_role")).toBe("そのほかの文脈");
    for (const role of [
      "parent_context",
      "operation_context",
      "same_page_context",
      "visual_context",
      "section_context",
      "entity_expansion",
      "",
      "unknown_role",
    ]) {
      expect(evidenceRoleLabel(role)).not.toMatch(/[a-z_]{4,}/);
    }
  });
});

describe("evidenceLocation / evidenceExcerpt", () => {
  it("ページと見出しを読める位置にする", () => {
    expect(evidenceLocation(chunk("a:1", { page_start: 12, section_title: "第 2 章" }))).toBe("p.12 ・ 第 2 章");
    expect(evidenceLocation(chunk("a:1"))).toBe("");
  });

  it("本文の空白をつめ、長ければ省く", () => {
    expect(evidenceExcerpt("一行目\n\n  二行目")).toBe("一行目 二行目");
    expect(evidenceExcerpt("あ".repeat(130))).toBe(`${"あ".repeat(120)}…`);
  });
});
