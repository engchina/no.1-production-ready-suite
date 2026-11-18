import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { AnswerPlainText } from "@production-ready/ui";
import { describe, expect, it } from "vitest";

import { AnswerText } from "./AnswerText";

// #4734: 回答の本文の Markdown の表と、表の行の並びを、文字のまま出さずに共通の結果の表で描く。内容はすべて合成のデータ。

const TABLE = ["| 列 A | 列 B |", "| --- | --- |", "| 値 1 | 値 2 |"].join("\n");

function render(text: string, props: Record<string, unknown> = {}): string {
  return renderToStaticMarkup(createElement(AnswerText as (input: Record<string, unknown>) => ReactElement, { text, ...props }));
}

/** 表が部品で描かれ、記号の行が本文に残っていないこと。 */
function expectRenderedTable(html: string, cell = "値 1") {
  expect(html).toContain("<table");
  expect(html).toContain(cell);
  expect(html).not.toContain("| ---");
  expect(html).not.toContain("| 列 A");
}

describe("AnswerText の表（issue 4734）", () => {
  it("HTML 引用の表示用行は、エスケープしたパイプとバックスラッシュを元の値として読む", () => {
    const html = render("資料により確認できた事項\n\n・合成の表題\n| A\\|B | 10 |\n| C\\\\|D\\ | 20 |\n根拠：文書 A.pdf p.3");
    expectRenderedTable(html, "A|B");
    expect(html).toContain("C\\|D\\");
    expect((html.match(/<th\b/g) ?? [])).toHaveLength(2);
    expect(html).toContain("合成の表題");
  });

  it("節に分けない本文の表を描く", () => {
    expectRenderedTable(render(`前の文です。\n\n${TABLE}\n\n後の文です。`));
  });

  it("生成中の本文でも、表頭と区切りの行がそろった表を描き、カーソルを残す", () => {
    const html = render(`前の文です。\n${TABLE}`, { streaming: true, cursor: createElement("span", { "data-testid": "cursor" }) });
    expectRenderedTable(html);
    expect(html).toContain('data-testid="cursor"');
  });

  it("生成の途中で区切りの行がまだ無い表は文字のまま出す", () => {
    const html = render("| 列 A | 列 B |\n| --", { streaming: true });
    expect(html).not.toContain("<table");
    expect(html).toContain("| 列 A | 列 B |");
  });

  it("節に分ける本文の要約の段落の表を描く", () => {
    expectRenderedTable(render(`要約です。\n${TABLE}\n\n資料により確認できた事項\n\n・項目です。\n根拠：文書 A.pdf p.3`));
  });

  it("箇条書きの項目の中の表を描き、根拠の行を項目に残す", () => {
    const html = render(`資料により確認できた事項\n\n・項目です。\n${TABLE}\n根拠：文書 A.pdf p.3`);
    expectRenderedTable(html);
    expect(html).toContain('data-testid="answer-citation-text"');
    expect(html).toContain("根拠：文書 A.pdf p.3");
  });

  it("空行を挟んだ項目の表も項目の続きにして、根拠の行を項目に残す", () => {
    const html = render(`資料により確認できた事項\n\n・項目です。\n\n${TABLE}\n根拠：文書 A.pdf p.3`);
    expectRenderedTable(html);
    expect(html).toContain('data-testid="answer-citation-text"');
  });

  it("結論の項目の続きの行の表を描く", () => {
    expectRenderedTable(render(`資料をもとに導いた結論\n\n・結論です。\n${TABLE}\n根拠：文書 A.pdf p.3`));
  });

  it("結論の説明の表を描く", () => {
    const html = render(`資料をもとに導いた結論\n\n・結論です。\n当てはめ：説明です。\n${TABLE}\n根拠：文書 A.pdf p.3`);
    expectRenderedTable(html);
    expect(html).toContain('data-testid="answer-application-reasoning"');
  });

  it("節の中の項目でない文の表を描く", () => {
    expectRenderedTable(render(`資料により確認できた事項\n\n前置きです。\n${TABLE}`));
  });

  it("コードが切り出した区切りの行の無い表の行の並びを描く", () => {
    const html = render("資料により確認できた事項\n\n・| 項目 A | 10 |\n| 項目 B | 20 |\n根拠：文書 A.pdf p.3");
    expect(html).toContain("<table");
    expect(html).toContain("項目 A");
    expect(html).not.toContain("| 項目 A");
  });

  it("表の行が 1 行だけの項目は文のまま出す", () => {
    const html = render("資料により確認できた事項\n\n・| 項目 A | 10 |\n根拠：文書 A.pdf p.3");
    expect(html).not.toContain("<table");
    expect(html).toContain("| 項目 A | 10 |");
  });

  it("中国語の回答の表も回答の言語で包む", () => {
    const zh = "这是简体中文的回答内容。";
    const html = render(`${zh}\n${TABLE}\n\n资料\n\n资料により確認できた事項\n\n・${zh}\n根拠：文書 A.pdf p.3`);
    expect(html).toContain('lang="zh-Hans"');
  });

  it("| の無い本文は表にせず、これまでと同じ要素で出す", () => {
    const text = "節のない本文です。\n2 行目です。";
    expect(render(text)).toBe(renderToStaticMarkup(createElement(AnswerPlainText, { text })));
    const sections = render("資料により確認できた事項\n\n・項目です。\n根拠：文書 A.pdf p.3");
    expect(sections).toContain('<li class="whitespace-pre-wrap">');
    expect(sections).not.toContain("<table");
  });
});

// #5489: 根拠の行の番号は回答全体の通し番号。同じ根拠の行はどの段落でも同じ番号、別の行は別の番号にする。
describe("AnswerText の根拠の行の番号（issue 5489）", () => {
  const markers = (html: string) =>
    [...html.matchAll(/data-testid="answer-citation-marker"[^>]*>([^<]*)</g)].map((match) => match[1]);
  const anchors = (html: string) => [...html.matchAll(/id="([^"]*-sources-\d+)"/g)].map((match) => match[1]);

  it("段落をまたいで同じ根拠の行は同じ番号にし、id は最初の行にだけ付ける", () => {
    const html = render(
      [
        "合成の原則の文です。",
        "根拠：文書 A.pdf p.3",
        "合成の例外の文です。",
        "根拠：文書 B.pdf p.5",
        "根拠：文書 C.pdf p.7",
        "",
        "合成の原則の続きです。",
        "根拠：文書 A.pdf p.3",
        "根拠：文書 C.pdf p.7",
      ].join("\n"),
    );
    expect(markers(html)).toEqual(["[1]", "[2]", "[3]", "[1]", "[3]"]);
    const ids = anchors(html);
    expect(ids.map((id) => id.replace(/^.*-sources-/, ""))).toEqual(["1", "2", "3"]);
    expect(new Set(ids).size).toBe(3);
  });

  it("2 つの表の出典の列の印は通し番号の根拠の行（最初に出る行）へ移る", () => {
    const html = render(
      [
        "合成の表の題 1",
        "| 項目 | 出典 |",
        "| --- | --- |",
        "| 行 1 | [1] |",
        "| 行 2 | [2] |",
        "根拠：文書 A.pdf p.3",
        "根拠：文書 B.pdf p.5",
        "",
        "合成の表の題 2",
        "| 項目 | 出典 |",
        "| --- | --- |",
        "| 行 1 | [3] |",
        "| 行 2 | [1] |",
        "根拠：文書 C.pdf p.7",
        "根拠：文書 A.pdf p.3",
      ].join("\n"),
      { diagnostics: { answer_format: { table_source_column: "出典" } } },
    );
    expect(markers(html)).toEqual(["[1]", "[2]", "[3]", "[1]"]);
    const ids = anchors(html);
    expect([...html.matchAll(/data-testid="answer-source-mark"/g)]).toHaveLength(4);
    const targets = [...html.matchAll(/href="#([^"]*)"/g)].map((match) => match[1]);
    // 印の移り先はすべて実在する id（2 つ目の表の [1] は 1 つ目の表の下の行）。
    expect(targets.map((id) => id.replace(/^.*-sources-/, ""))).toEqual(["1", "2", "3", "1"]);
    expect(targets.every((id) => ids.includes(id))).toBe(true);
  });
});
