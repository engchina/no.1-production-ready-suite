import { describe, expect, it } from "vitest";

import { answerEvidenceData } from "@/lib/answer-evidence";
import { structureAnswer, withoutFrameHeading, type StructuredBlock } from "@/lib/answer-structure";

// #2328: Agent の回答を段落・節と「参考情報」に組み立てる（表示だけ。本文の文は変えない）。

const TITLES = { reference: "参考", referenceNote: "原文の説明" };

function evidence(references: { quote: string; chunk_ids: string[] }[], citationLines: string[] = []) {
  return answerEvidenceData({
    items: [
      { document_id: "d1", chunk_id: "c1", file_name: "文書 A.pdf", excerpt: "x", locator: { page_start: 3 }, cited: true },
      { document_id: "d2", chunk_id: "c2", file_name: "文書 B.pdf", excerpt: "y", locator: { section_path: ["第 1 章"] } },
    ],
    markers: [{ text: "【証拠 ID: c1】", chunk_ids: ["c1"] }],
    references,
    citation_lines: citationLines,
  });
}

function withoutRaw<T extends { raw?: string }>(value: T): Omit<T, "raw"> {
  const copy: T = { ...value };
  delete copy.raw;
  return copy;
}

function shape(blocks: StructuredBlock[] | null) {
  return blocks?.map((block) =>
    block.kind === "section" ? { ...block, entries: block.entries.map(withoutRaw) } : withoutRaw(block)
  );
}

const REFERENCE = [{ quote: "申請は 30 日前までに行う。", chunk_ids: ["c1"] }];

describe("answerEvidenceData", () => {
  it("根拠の行の場所は頁、無ければ節", () => {
    const data = evidence([]);
    expect(data.entries.map((entry) => entry.citationLocation)).toEqual(["p.3", "第 1 章"]);
    expect(data.entries[0].item).toMatchObject({ fileName: "文書 A.pdf", usedInAnswer: true });
  });
});

describe("structureAnswer", () => {
  const answer = [
    "答えは次のとおりです。",
    "",
    "- 申請は 30 日前までに行います【証拠 ID: c1】。",
    "- 承認は部署が行います。",
    "",
    "**根拠**",
    "",
    "- 文書 A.pdf p.3",
  ].join("\n");

  it("本文は段落・節のまま、根拠の原文を「参考情報」の節に出し、結び付いた出典の行を除く", () => {
    expect(shape(structureAnswer(answer, evidence(REFERENCE, ["- 文書 A.pdf p.3"]), TITLES))).toEqual([
      { kind: "paragraph", key: "b0", text: "答えは次のとおりです。", chunkIds: [] },
      {
        kind: "section",
        key: "b1",
        title: null,
        ordered: false,
        entries: [
          { key: "b1-0", text: "申請は 30 日前までに行います。", chunkIds: [] },
          { key: "b1-1", text: "承認は部署が行います。", chunkIds: [] },
        ],
      },
      {
        kind: "section",
        key: "reference",
        title: "参考",
        ordered: false,
        entries: [
          { key: "reference-note", text: "原文の説明", chunkIds: [], muted: true, plain: true, uiText: true },
          { key: "reference-0", text: "「申請は 30 日前までに行う。」", chunkIds: ["c1"], plain: true },
        ],
      },
    ]);
  });

  it("根拠を 1 つも結び付けられなければ null（Markdown のまま）", () => {
    expect(structureAnswer(answer, evidence([]), TITLES)).toBeNull();
    expect(structureAnswer(answer, null, TITLES)).toBeNull();
  });

  it("出典の行の後ろに本文が続く見出しは残す", () => {
    const withBody = "答えです。\n\n**根拠**\n\n- 文書 A.pdf p.3\n\n**補足**\n\n期限があります。";
    const blocks = structureAnswer(withBody, evidence(REFERENCE, ["- 文書 A.pdf p.3"]), TITLES);
    const titles = blocks?.filter((block) => block.kind === "section").map((block) => (block.kind === "section" ? block.title : null));
    expect(titles).toEqual(["補足", "参考"]);
  });
});

// #4061: 回答の末尾の区切り線・箇条書きを段落の文にしない。
describe("structureAnswer の区切り線と箇条書き", () => {
  const TAIL = "---\n**根拠**\n- 文書 A.pdf ／ 第 3 条、p.1";
  const answer = ["期限は 30 日です。", "", "| 区分 | 期限 |", "|---|---|", "| 一般 | 30 日 |", "", TAIL].join("\n");
  const kinds = (text: string) =>
    structureAnswer(text, evidence(REFERENCE), TITLES)
      ?.filter((block) => block.key !== "reference")
      .map((block) => (block.kind === "section" ? `section:${block.title ?? ""}` : block.kind));

  it("区切り線・太字・箇条書きの混ざる回答の末尾は、段落の文ではなく Markdown の塊", () => {
    const blocks = structureAnswer(answer, evidence(REFERENCE), TITLES)?.filter((block) => block.key !== "reference");
    expect(shape(blocks ?? null)).toEqual([
      { kind: "paragraph", key: "b0", text: "期限は 30 日です。", chunkIds: [] },
      { kind: "markdown", key: "b1", text: "| 区分 | 期限 |\n|---|---|\n| 一般 | 30 日 |", chunkIds: [] },
      { kind: "markdown", key: "b2", text: TAIL, chunkIds: [] },
    ]);
  });

  it("区切り線だけの塊と、段落の直後に空行なしで続く箇条書きも Markdown の塊", () => {
    expect(kinds("期限は 30 日です。\n\n***\n\n以上です。")).toEqual(["paragraph", "markdown", "paragraph"]);
    expect(kinds("期限は 30 日です。\n\n対象は次のとおりです。\n- 一般社員\n- 課長以上")).toEqual(["paragraph", "markdown"]);
    expect(kinds("期限は 30 日です。\n\n- 一般社員\n- 課長以上")).toEqual(["paragraph", "section:"]);
    expect(kinds("期限は 30 日です。\n\n**対象**\n- 一般社員")).toEqual(["paragraph", "section:対象"]);
    // `・` は Markdown の箇条書きではない（段落の文のまま）。
    expect(kinds("期限は 30 日です。\n\n対象:\n・一般社員")).toEqual(["paragraph", "paragraph"]);
  });
});

describe("withoutFrameHeading", () => {
  // #4061: LLM が本文を「## 回答」で始めると、枠の見出し「回答」と二重に出る。
  it("本文の先頭の見出しが枠の見出しと同じなら、その行と続く空行を除く", () => {
    expect(withoutFrameHeading("\n\n## 回答\n\n上限は 12,000 円です。\n\n### 根拠\n- 文書 A.pdf", "回答")).toBe(
      "上限は 12,000 円です。\n\n### 根拠\n- 文書 A.pdf"
    );
    expect(withoutFrameHeading("**回答**\n上限は 12,000 円です。", "回答")).toBe("上限は 12,000 円です。");
  });

  it("文言の違う見出し・途中の見出し・見出しでない先頭の文は残す", () => {
    expect(withoutFrameHeading("## 回答の要約\n本文", "回答")).toBe("## 回答の要約\n本文");
    expect(withoutFrameHeading("本文\n\n## 回答\n続き", "回答")).toBe("本文\n\n## 回答\n続き");
    expect(withoutFrameHeading("回答は次のとおりです。", "回答")).toBe("回答は次のとおりです。");
  });
});
