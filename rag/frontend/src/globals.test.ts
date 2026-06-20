import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

// グローバルスタイルのデザイントークン（フォーカスリング等）は共有 UI パッケージへ移管した。
// 回帰テストは正本である @engchina/production-ready-ui の tokens.css を検証する。
const tokensCss = readFileSync(
  new URL("../node_modules/@engchina/production-ready-ui/dist/tokens.css", import.meta.url),
  "utf8"
);

describe("global focus styles (shared tokens)", () => {
  it("フォーム入力のフォーカスリングは外側へはみ出さない", () => {
    expect(tokensCss).toContain(
      ":is(input, textarea, select):not([type=\"checkbox\"]):not([type=\"radio\"]):focus-visible"
    );
    expect(tokensCss).toMatch(/outline:\s*none;/);
    expect(tokensCss).toMatch(/box-shadow:\s*inset 0 0 0 1px var\(--ring\);/);
  });
});
