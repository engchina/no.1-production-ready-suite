import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FlowStepper } from "./FlowStepper";

describe("FlowStepper", () => {
  it("ファイル準備を step 単位で skip 表示できる", () => {
    const html = renderToStaticMarkup(
      createElement(FlowStepper, {
        status: "REVIEW",
        skippedSteps: ["PREPROCESSING"],
      })
    );

    expect(html).toContain("ファイル準備");
    expect(html).toContain("skip");
  });

  it("skip 指定が無い場合は skip 表示しない", () => {
    const html = renderToStaticMarkup(createElement(FlowStepper, { status: "REVIEW" }));

    expect(html).toContain("ファイル準備");
    expect(html).not.toContain("skip");
  });

  it("ERROR でも工程列を維持し、汎用エラーバー1個に潰さない（§9 P5）", () => {
    const html = renderToStaticMarkup(
      createElement(FlowStepper, { status: "ERROR", failedStep: "INGESTING" })
    );

    // 全工程が残る。
    expect(html).toContain("ファイル準備");
    expect(html).toContain("抽出");
    expect(html).toContain("Embedding / 索引");
    // 失敗工程は danger 強調 + 「エラー」チップ（色のみに頼らない）。
    expect(html).toContain("text-danger");
    expect(html).toContain("エラー");
  });

  it("ERROR で失敗工程が不明でも工程列は表示する", () => {
    const html = renderToStaticMarkup(createElement(FlowStepper, { status: "ERROR" }));

    expect(html).toContain("ファイル準備");
    expect(html).toContain("索引済み");
  });
});
