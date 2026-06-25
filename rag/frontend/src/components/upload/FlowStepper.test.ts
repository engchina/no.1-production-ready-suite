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
});
