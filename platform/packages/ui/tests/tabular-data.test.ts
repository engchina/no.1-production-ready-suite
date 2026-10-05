import { describe, expect, it } from "vitest";

import { splitMarkdownTables, toTabularData } from "../src";

// #1158: ツールの結果の JSON・回答の Markdown の表を、結果の表（ChatResultTable）の列と行にする判定（製品に依存しない）。

describe("toTabularData", () => {
  it("NL2SQL の MCP の出力（列名の文字列・オブジェクトの行・打ち切り・全体の行数）を表にする", () => {
    const data = toTabularData({
      job_id: "job-1",
      columns: ["DEPARTMENT", "AMOUNT"],
      rows: [
        { DEPARTMENT: "営業", AMOUNT: 1200 },
        { DEPARTMENT: "開発", AMOUNT: null },
      ],
      returned_count: 2,
      total: 120,
      has_more: true,
      truncated: false,
    });
    expect(data).toEqual({
      columns: [{ name: "DEPARTMENT" }, { name: "AMOUNT" }],
      rows: [
        ["営業", 1200],
        ["開発", null],
      ],
      truncated: true,
      totalRowCount: 120,
      elapsedMs: null,
    });
  });

  it("列の指定があれば 0 行も表（列の見出しと 0 行を出す）", () => {
    const data = toTabularData({ columns: ["ID"], rows: [], truncated: false, total: 0 });
    expect(data).toEqual({
      columns: [{ name: "ID" }],
      rows: [],
      truncated: false,
      totalRowCount: 0,
      elapsedMs: null,
    });
  });

  it("`{ name, label, type }` の列と配列の行を扱い、行に足りないセルは null", () => {
    const data = toTabularData({
      columns: [
        { name: "id", type: "number" },
        { name: "name", label: "名前" },
      ],
      rows: [[1, "A"], [2]],
      row_count: 2,
      elapsed_ms: 812,
    });
    expect(data?.columns).toEqual([{ name: "id", type: "number" }, { name: "名前" }]);
    expect(data?.rows).toEqual([
      [1, "A"],
      [2, null],
    ]);
    expect(data?.totalRowCount).toBe(2);
    expect(data?.elapsedMs).toBe(812);
    expect(data?.truncated).toBe(false);
  });

  it("`{ name, label }` の列でもオブジェクトの行は `name` の key で読む", () => {
    const data = toTabularData({ columns: [{ name: "amount", label: "金額" }], rows: [{ amount: 10 }] });
    expect(data?.columns).toEqual([{ name: "金額" }]);
    expect(data?.rows).toEqual([[10]]);
  });

  it("オブジェクトの配列は key の和集合を列にし、無い値は null", () => {
    const data = toTabularData([{ a: 1 }, { b: "x" }]);
    expect(data?.columns).toEqual([{ name: "a" }, { name: "b" }]);
    expect(data?.rows).toEqual([
      [1, null],
      [null, "x"],
    ]);
  });

  it("同じ名前の列は位置で読む", () => {
    const data = toTabularData({ columns: ["ID", "ID"], rows: [[1, 2]] });
    expect(data?.columns).toEqual([{ name: "ID" }, { name: "ID" }]);
    expect(data?.rows).toEqual([[1, 2]]);
  });

  it.each([
    ["文字列", "text"],
    ["null", null],
    ["空の配列（列が分からない）", []],
    ["列の無い 0 行", { rows: [] }],
    ["列の指定が空の 0 行（実行中のジョブ）", { columns: [], rows: [] }],
    ["rows を持たないオブジェクト", { answer: "根拠付き回答", citations: [{ id: 1 }] }],
    ["入れ子のセル", [{ a: { b: 1 } }]],
    ["配列のセル", { rows: [{ tags: ["x"] }] }],
    ["読めない列の指定", { columns: [1, 2], rows: [[1, 2]] }],
    ["数値の配列", [1, 2, 3]],
    ["key の無いオブジェクトの配列", [{}, {}]],
    ["数値でない行", { columns: ["a"], rows: ["x"] }],
  ])("表の形でない値（%s）は null", (_, value) => {
    expect(toTabularData(value)).toBeNull();
  });
});

describe("splitMarkdownTables", () => {
  it("表の前後の文と表に分ける", () => {
    const segments = splitMarkdownTables(
      [
        "部門別の売上です。",
        "",
        "| 部門 | 売上 |",
        "|:---|---:|",
        "| 営業 | 1,200 |",
        "| **開発** | `800` |",
        "",
        "以上です。",
      ].join("\n")
    );
    expect(segments).toEqual([
      { kind: "text", text: "部門別の売上です。\n" },
      {
        kind: "table",
        data: {
          // 値がすべて数の列は数値の列（右寄せ）。
          columns: [{ name: "部門" }, { name: "売上", type: "number" }],
          rows: [
            ["営業", "1,200"],
            ["開発", "800"],
          ],
          truncated: false,
          totalRowCount: null,
          elapsedMs: null,
        },
      },
      { kind: "text", text: "\n以上です。" },
    ]);
  });

  it("先頭・末尾の | が無い表と、セルの足りない行・多い行・エスケープした | を扱う", () => {
    const [segment, rest] = splitMarkdownTables("a | b\n--- | ---\n1 \\| 2 | x\n3 |\n4 | 5 | 6\n終わり");
    expect(segment).toMatchObject({
      kind: "table",
      data: {
        rows: [
          ["1 | 2", "x"],
          ["3", ""],
          ["4", "5"],
        ],
      },
    });
    expect(rest).toEqual({ kind: "text", text: "終わり" });
  });

  it("数でない値・空のセルだけの列は数値の列にしない", () => {
    const [segment] = splitMarkdownTables("| a | b | c |\n|---|---|---|\n| 1.5 | 12% | |\n| -3 | 1,2 | |");
    expect(segment).toMatchObject({
      kind: "table",
      data: { columns: [{ name: "a", type: "number" }, { name: "b" }, { name: "c" }] },
    });
  });

  it("空の表頭は列の番号にする", () => {
    const [segment] = splitMarkdownTables("|  | 値 |\n|---|---|\n| a | 1 |");
    expect(segment).toMatchObject({ kind: "table", data: { columns: [{ name: "1" }, { name: "値" }] } });
  });

  it("区切りの行の列数が合わない・区切りの行が無い・コードブロックの中は表にしない", () => {
    expect(splitMarkdownTables("| a | b |\n| --- |\n| 1 | 2 |")).toEqual([
      { kind: "text", text: "| a | b |\n| --- |\n| 1 | 2 |" },
    ]);
    expect(splitMarkdownTables("A|B の比較")).toEqual([{ kind: "text", text: "A|B の比較" }]);
    const fenced = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```";
    expect(splitMarkdownTables(fenced)).toEqual([{ kind: "text", text: fenced }]);
  });

  it("表頭と区切りの行だけの表は 0 行の表", () => {
    const [segment] = splitMarkdownTables("| a |\n|---|");
    expect(segment).toMatchObject({ kind: "table", data: { columns: [{ name: "a" }], rows: [] } });
  });

  it("続けて置いた表は別の表にする", () => {
    const segments = splitMarkdownTables("| a |\n|---|\n| 1 |\n\n| b |\n|---|\n| 2 |");
    expect(segments.map((segment) => segment.kind)).toEqual(["table", "table"]);
  });
});
