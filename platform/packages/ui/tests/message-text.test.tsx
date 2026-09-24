import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  MessageText,
  normalizeMessageText,
  segmentMessageText,
} from "../src/components/ui/message-text";

describe("MessageText", () => {
  it("改行・タブ・連続空白を prose 向けに正規化する", () => {
    expect(normalizeMessageText("  abc。ghn\n\txyz  ")).toBe("abc。ghn xyz");
  });

  it("日本語・中国語・英語の強い句読点で分節する", () => {
    expect(segmentMessageText("最新です。変更はありません！続行します？注意；完了。"))
      .toEqual([
        { text: "最新です。", separatorBefore: "" },
        { text: "変更はありません！", separatorBefore: "" },
        { text: "続行します？", separatorBefore: "" },
        { text: "注意；", separatorBefore: "" },
        { text: "完了。", separatorBefore: "" },
      ]);
    expect(segmentMessageText("Saved. No changes! Continue?", "en")).toEqual([
      { text: "Saved.", separatorBefore: "" },
      { text: "No changes!", separatorBefore: " " },
      { text: "Continue?", separatorBefore: " " },
    ]);
  });

  it("小数・バージョン・URL 内の句点を分割しない", () => {
    const text = "Version 1.2 is current. See https://example.com/docs. Done.";
    expect(segmentMessageText(text, "en").map((segment) => segment.text)).toEqual([
      "Version 1.2 is current.",
      "See https://example.com/docs.",
      "Done.",
    ]);
  });

  it("連続句読点と単独の長文を空セグメントなしで保持する", () => {
    expect(segmentMessageText("本当！？次です。").map((segment) => segment.text)).toEqual([
      "本当！？",
      "次です。",
    ]);
    expect(segmentMessageText("a".repeat(300))).toEqual([
      { text: "a".repeat(300), separatorBefore: "" },
    ]);
    expect(segmentMessageText(" \n\t ")).toEqual([]);
  });

  it("原文を欠落させず文ごとの inline box を描画する", () => {
    const html = renderToStaticMarkup(
      <MessageText text={"システムテーブルは最新です。変更はありません。"} />
    );
    expect(html).toContain("data-message-text");
    expect(html.match(/data-message-sentence/g)).toHaveLength(2);
    expect(html).toContain("システムテーブルは最新です。</span><span");
    expect(html).toContain("変更はありません。");
  });
});
