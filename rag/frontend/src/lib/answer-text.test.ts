import { describe, expect, it } from "vitest";

import { parseAnswerText } from "./answer-text";

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
        entries: [{ kind: "item", text: "受注番号は自動で採番されます。", citations: ["根拠：受注マニュアル.pdf p.3"] }],
      },
      {
        kind: "section",
        title: "操作手順（受注入力）",
        ordered: true,
        entries: [
          { kind: "item", text: "受注入力画面を開きます。", citations: [] },
          { kind: "item", text: "登録ボタンを押します。", citations: ["根拠：受注マニュアル.pdf p.5"] },
        ],
      },
      {
        kind: "section",
        title: "資料からは確認できない点",
        ordered: false,
        entries: [{ kind: "item", text: "既存の受注の修正方法", citations: [] }],
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
