import { describe, expect, it } from "vitest";

import type { DocumentElement } from "@/lib/api";
import { elementVision, tableHtmlToText } from "./docrag-element";

function element(metadata: DocumentElement["metadata"]): DocumentElement {
  return { kind: "figure", text: "", order: 0, metadata } as DocumentElement;
}

describe("tableHtmlToText", () => {
  it("表 HTML を行・セル区切りのテキストへ落とす", () => {
    const html =
      "<table><thead><tr><th>項目</th><th>値</th></tr></thead>" +
      "<tbody><tr><td>受付&amp;番号</td><td>A<br>B</td></tr><tr><td></td><td></td></tr></tbody></table>";
    expect(tableHtmlToText(html)).toBe("項目 | 値\n受付&番号 | A B");
  });

  it("表でない text はそのまま返す", () => {
    expect(tableHtmlToText("本文 <b>強調</b>")).toBe("本文 <b>強調</b>");
  });
});

describe("elementVision", () => {
  it("Vision の状態と説明を取り出す", () => {
    expect(
      elementVision(element({ vision_status: "succeeded", vision_retrieval_text: "画面説明" }))
    ).toEqual({ status: "succeeded", retrievalText: "画面説明", excluded: false });
  });

  it("装飾画像は除外として扱い、無関係な要素は null", () => {
    expect(elementVision(element({ visual_role: "decorative" }))?.excluded).toBe(true);
    expect(elementVision(element({ category: "Text" }))).toBeNull();
  });
});
