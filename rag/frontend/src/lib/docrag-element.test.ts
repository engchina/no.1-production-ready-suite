import { describe, expect, it } from "vitest";

import type { DocumentElement } from "@/lib/api";
import { docragVisionDetails, elementVision, tableHtmlToText } from "./docrag-element";

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

describe("docragVisionDetails", () => {
  const extraction = {
    parser_artifacts: {
      docrag_layout: {
        records: [
          {
            id: "docling-p1-2",
            raw: {
              vision_description: {
                visual_kind: "screenshot",
                visible_buttons: ["登録", "取消"],
                table_rows: [
                  ["受注番号", "必須"],
                  ["顧客名", "任意"],
                ],
                condition_result_pairs: [{ condition: "未入力", result: "エラー", visible_text: "" }],
                retrieval_text: "検索用",
              },
            },
          },
          { id: "docling-p1-3", raw: { visual_role: "decorative", visual_role_reason: "ロゴ" } },
        ],
      },
    },
  };

  it("値のある Vision 項目だけを整形して返す", () => {
    const details = docragVisionDetails(extraction, "docling-p1-2");
    expect(details?.lines).toEqual([
      { field: "visual_kind", value: "screenshot" },
      { field: "visible_buttons", value: "登録\n取消" },
      { field: "table_rows", value: "受注番号 | 必須\n顧客名 | 任意" },
      { field: "condition_result_pairs", value: "未入力 → エラー" },
    ]);
  });

  it("装飾画像は除外理由を返し、該当しない要素は null", () => {
    expect(docragVisionDetails(extraction, "docling-p1-3")?.excludedReason).toBe("ロゴ");
    expect(docragVisionDetails(extraction, "missing")).toBeNull();
    expect(docragVisionDetails({}, "docling-p1-2")).toBeNull();
  });
});
