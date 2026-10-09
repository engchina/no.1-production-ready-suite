import { describe, expect, it } from "vitest";

import type { RetrievedChunk } from "./api";
import { matchCitation, matchCitationLine, parseAnswerText, parseCitationLine } from "./answer-text";

describe("parseAnswerText", () => {
  it("回答エンジンの本文を要約・節・説明・根拠に分ける", () => {
    const text = [
      "受注は受注入力画面で登録します。",
      "",
      "確認できる内容",
      "",
      "・受注番号は自動で採番されます。",
      "根拠：受注マニュアル.pdf p.3",
      "",
      "操作手順（受注入力）",
      "",
      "1. 受注入力画面を開きます。",
      "2. 登録ボタンを押します。",
      "根拠：受注マニュアル.pdf p.5",
      "",
      "資料からは確認できない点",
      "",
      "・既存の受注の修正方法",
    ].join("\n");
    expect(parseAnswerText(text)).toEqual([
      { kind: "paragraph", text: "受注は受注入力画面で登録します。" },
      {
        kind: "section",
        title: "確認できる内容",
        ordered: false,
        entries: [
          {
            kind: "item",
            text: "受注番号は自動で採番されます。",
            citations: ["根拠：受注マニュアル.pdf p.3"],
            citationLines: [1],
          },
        ],
      },
      {
        kind: "section",
        title: "操作手順（受注入力）",
        ordered: true,
        entries: [
          { kind: "item", text: "受注入力画面を開きます。", citations: [], citationLines: [] },
          {
            kind: "item",
            text: "登録ボタンを押します。",
            citations: ["根拠：受注マニュアル.pdf p.5"],
            citationLines: [2],
          },
        ],
      },
      {
        kind: "section",
        title: "資料からは確認できない点",
        ordered: false,
        entries: [{ kind: "item", text: "既存の受注の修正方法", citations: [], citationLines: [] }],
      },
    ]);
  });

  it("節の後ろに続く注記は節の中の文として残す", () => {
    const blocks = parseAnswerText("確認できる内容\n\n・説明\n根拠：a.pdf p.1\n\n旧形式の文書 2 件は検索対象外です。");
    expect(blocks?.[0]).toMatchObject({
      entries: [
        { kind: "item", text: "説明", citations: ["根拠：a.pdf p.1"] },
        { kind: "text", text: "旧形式の文書 2 件は検索対象外です。" },
      ],
    });
  });

  it("書式の見出しが無い本文は null（そのまま出す）", () => {
    expect(parseAnswerText("検索された資料に回答を裏付ける十分な根拠がないため、回答できません。")).toBeNull();
    // 本文中に同じ語があっても、行全体が見出しでなければ節にしない。
    expect(parseAnswerText("確認できる内容は次のとおりです。")).toBeNull();
  });
});

function chunk(fileName: string, pageStart?: number, pageEnd?: number): RetrievedChunk {
  const metadata: RetrievedChunk["metadata"] = {};
  if (pageStart != null) metadata.page_start = pageStart;
  if (pageEnd != null) metadata.page_end = pageEnd;
  return {
    document_id: fileName,
    chunk_id: `${fileName}:${pageStart}`,
    text: "",
    score: 0,
    rerank_score: null,
    file_name: fileName,
    category_name: null,
    metadata,
  };
}

describe("parseCitationLine", () => {
  it("根拠の行のファイル名と頁を読む", () => {
    expect(parseCitationLine("根拠：受注 マニュアル.pdf p.12")).toEqual({ fileName: "受注 マニュアル.pdf", page: 12 });
    // 頁の無い行（頁が空）も、ファイル名だけで当てる。
    expect(parseCitationLine("根拠：規程.docx p.")).toEqual({ fileName: "規程.docx", page: null });
    expect(parseCitationLine("根拠です")).toBeNull();
  });

  it("表計算の根拠の行のシートとセル範囲の行を読む（Issue 1224）", () => {
    expect(parseCitationLine("根拠：経費 コード.xlsx シート「費目 コード」A3:D6")).toEqual({
      fileName: "経費 コード.xlsx",
      page: null,
      sheet: "費目 コード",
      rowStart: 3,
      rowEnd: 6,
    });
    expect(parseCitationLine("根拠：a.xlsx シート「手順」")).toEqual({
      fileName: "a.xlsx",
      page: null,
      sheet: "手順",
      rowStart: null,
      rowEnd: null,
    });
  });
});

describe("matchCitation", () => {
  const citations = [
    chunk("manual.pdf", 2),
    chunk("Manual.pdf", 5, 7),
    chunk("manual.pdf", 6),
    chunk("other.pdf", 6),
  ];

  it("同じファイル（大小文字・全角半角を区別しない）で頁の範囲に入る引用を順位の順に選ぶ", () => {
    expect(matchCitation({ fileName: "ＭＡＮＵＡＬ.pdf", page: 6 }, citations)).toBe(1);
    expect(matchCitation({ fileName: "manual.pdf", page: 2 }, citations)).toBe(0);
  });

  it("頁の範囲に入る引用が無ければ頁の近い引用、頁が無ければ同じファイルの先頭", () => {
    expect(matchCitation({ fileName: "manual.pdf", page: 4 }, citations)).toBe(1);
    expect(matchCitation({ fileName: "manual.pdf", page: null }, citations)).toBe(0);
    // 頁の無い引用は 1 頁目として扱う（backend の _stored_child と同じ）。
    expect(matchCitation({ fileName: "a.pdf", page: 1 }, [chunk("a.pdf")])).toBe(0);
  });

  it("同じファイルの引用が無ければ -1", () => {
    expect(matchCitation({ fileName: "missing.pdf", page: 1 }, citations)).toBe(-1);
    expect(matchCitation({ fileName: "manual.pdf", page: 1 }, [])).toBe(-1);
  });
});

// 出典行の番号で引用を結ぶ（#1330）。
describe("matchCitationLine", () => {
  function numbered(fileName: string, page: number, lines: number[]): RetrievedChunk {
    const base = chunk(fileName, page);
    return { ...base, metadata: { ...base.metadata, answer_citation_lines: lines } };
  }

  it("本文の n 番目の根拠の行は、回答エンジンが n を付けた引用に結ぶ（同じ頁・同じファイルでも取り違えない）", () => {
    const citations = [numbered("規程.pdf", 2, [2]), numbered("規程.pdf", 2, [1, 3])];
    const ref = { fileName: "規程.pdf", page: 2 };

    expect(matchCitationLine(1, ref, citations)).toBe(1);
    expect(matchCitationLine(2, ref, citations)).toBe(0);
    expect(matchCitationLine(3, ref, citations)).toBe(1);
  });

  it("番号の無い回答（古い記録）はファイル名と頁で選ぶ", () => {
    const citations = [chunk("規程.pdf", 2), chunk("規程.pdf", 5)];

    expect(matchCitationLine(1, { fileName: "規程.pdf", page: 5 }, citations)).toBe(1);
    expect(matchCitationLine(undefined, { fileName: "規程.pdf", page: 2 }, citations)).toBe(0);
    expect(matchCitationLine(undefined, null, citations)).toBe(-1);
  });

  it("出典行の番号は、項目に付かない根拠の行も含めて本文の順に数える", () => {
    const blocks = parseAnswerText(
      ["確認できる内容", "", "・説明 A", "根拠：a.pdf p.1", "続きの文", "根拠：a.pdf p.2", "・説明 B", "根拠：b.pdf p.3"].join(
        "\n",
      ),
    );
    const section = blocks?.[0];
    const items = section?.kind === "section" ? section.entries.filter((entry) => entry.kind === "item") : [];

    expect(items.map((entry) => (entry.kind === "item" ? entry.citationLines : []))).toEqual([[1], [3]]);
  });
});

describe("matchCitation（表計算）", () => {
  function sheetChunk(sheet: string, rowStart: number, rowEnd: number): RetrievedChunk {
    const base = chunk("book.xlsx");
    return {
      ...base,
      chunk_id: `${sheet}:${rowStart}`,
      metadata: { sheet_name: sheet, row_start: rowStart, row_end: rowEnd },
    };
  }
  const citations = [sheetChunk("手順", 3, 5), sheetChunk("費目", 1, 1), sheetChunk("費目", 3, 6)];

  it("同じシートで行の範囲が重なる引用を選ぶ", () => {
    const ref = { fileName: "book.xlsx", page: null, sheet: "費目", rowStart: 4, rowEnd: 4 };
    expect(matchCitation(ref, citations)).toBe(2);
  });

  it("行が重ならなければ同じシートの先頭、シートが無ければ同じファイルの先頭", () => {
    expect(matchCitation({ fileName: "book.xlsx", page: null, sheet: "費目", rowStart: 20, rowEnd: 21 }, citations)).toBe(1);
    expect(matchCitation({ fileName: "book.xlsx", page: null, sheet: "別", rowStart: 1, rowEnd: 1 }, citations)).toBe(0);
  });
});
