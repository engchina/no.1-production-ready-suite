import { describe, expect, it } from "vitest";

import { parseMarkdownBlocks, parseMarkdownInline } from "./markdown-blocks";

// 組み込みのスキルの指示（agent/backend/app/features/agent/builtin_skills/*.md）と同じ形（#1795）。
const SAMPLE = [
  "業務データが要る質問に使う。",
  "SQL はこの Runtime 内では実行しない。",
  "",
  "## 手順",
  "",
  "### Step 1: 業務プロファイルを決める",
  "",
  "- `nl2sql_query` には `profile_id` が必須。",
  "- 次へ進む:",
  "  - 決まった → Step 2",
  "",
  "## 結果による分岐",
  "",
  "- `needs_clarification`: SQL はまだ作っていない。",
  "  1. 問いを 1 つだけ聞く。",
  "  2. 答えたら呼び直す。`answer` には次を入れる:",
  "     - `item_id`",
  "     - `selected_candidate_ids`",
  "- `error`: 伝える。",
].join("\n");

describe("parseMarkdownBlocks", () => {
  it("見出し・段落・入れ子の箇条書きに分ける", () => {
    const blocks = parseMarkdownBlocks(SAMPLE);
    expect(blocks.map((block) => block.kind)).toEqual([
      "paragraph",
      "heading",
      "heading",
      "list",
      "heading",
      "list",
    ]);
    expect(blocks[0]).toEqual({
      kind: "paragraph",
      lines: ["業務データが要る質問に使う。", "SQL はこの Runtime 内では実行しない。"],
    });
    expect(blocks[1]).toEqual({ kind: "heading", level: 2, text: "手順" });
    expect(blocks[2]).toEqual({ kind: "heading", level: 3, text: "Step 1: 業務プロファイルを決める" });

    const steps = blocks[3];
    expect(steps?.kind).toBe("list");
    if (steps?.kind !== "list") return;
    expect(steps.ordered).toBe(false);
    expect(steps.items).toHaveLength(2);
    expect(steps.items[1]).toEqual([
      { kind: "paragraph", lines: ["次へ進む:"] },
      { kind: "list", ordered: false, start: 1, items: [[{ kind: "paragraph", lines: ["決まった → Step 2"] }]] },
    ]);

    const branches = blocks[5];
    if (branches?.kind !== "list") throw new Error("箇条書きではない");
    expect(branches.items).toHaveLength(2);
    const clarification = branches.items[0];
    expect(clarification?.[1]).toMatchObject({ kind: "list", ordered: true, start: 1 });
    const numbered = clarification?.[1];
    if (numbered?.kind !== "list") throw new Error("番号付きではない");
    expect(numbered.items).toHaveLength(2);
    expect(numbered.items[1]?.[1]).toEqual({
      kind: "list",
      ordered: false,
      start: 1,
      items: [
        [{ kind: "paragraph", lines: ["`item_id`"] }],
        [{ kind: "paragraph", lines: ["`selected_candidate_ids`"] }],
      ],
    });
  });

  it("番号付きの開始の番号・コードの囲み・空行で区切った項目を扱う", () => {
    const blocks = parseMarkdownBlocks("3. 三\n\n4. 四\n\n```\n<b>x</b>\n```\n段落");
    expect(blocks).toEqual([
      {
        kind: "list",
        ordered: true,
        start: 3,
        items: [[{ kind: "paragraph", lines: ["三"] }], [{ kind: "paragraph", lines: ["四"] }]],
      },
      { kind: "code", text: "<b>x</b>" },
      { kind: "paragraph", lines: ["段落"] },
    ]);
  });

  it("1 段落の平文（書き直しの前の指示・利用者のスキル）はそのまま 1 つの段落", () => {
    const text = "ユーザーの目的を rag_search の query として扱う。1) 段に分ける。2) 段ごとに引く。";
    expect(parseMarkdownBlocks(text)).toEqual([{ kind: "paragraph", lines: [text] }]);
  });

  it("C# のような語の # は見出しの閉じとして消さない", () => {
    expect(parseMarkdownBlocks("## C#")).toEqual([{ kind: "heading", level: 2, text: "C#" }]);
    expect(parseMarkdownBlocks("## 手順 ##")).toEqual([{ kind: "heading", level: 2, text: "手順" }]);
  });
});

describe("parseMarkdownInline", () => {
  it("`code` と **強調** を分け、ほかの記号・HTML は文字のまま", () => {
    expect(parseMarkdownInline("`rag_search` の **query** と <b>x</b> と [a](b)")).toEqual([
      { kind: "code", text: "rag_search" },
      { kind: "text", text: " の " },
      { kind: "strong", text: "query" },
      { kind: "text", text: " と <b>x</b> と [a](b)" },
    ]);
  });

  it("閉じていない記号は文字のまま", () => {
    expect(parseMarkdownInline("`閉じない と **これも")).toEqual([{ kind: "text", text: "`閉じない と **これも" }]);
  });
});
