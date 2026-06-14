import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const css = readFileSync(new URL("./globals.css", import.meta.url), "utf8");

describe("global focus styles", () => {
  it("フォーム入力のフォーカスリングは外側へはみ出さない", () => {
    expect(css).toContain(
      ":is(input, textarea, select):not([type=\"checkbox\"]):not([type=\"radio\"]):focus-visible"
    );
    expect(css).toMatch(/outline:\s*none;/);
    expect(css).toMatch(/box-shadow:\s*inset 0 0 0 1px var\(--ring\);/);
  });
});
