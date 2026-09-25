import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { FIXED_SPLIT_STORAGE_PREFIX, FixedSplitPane, fixedSplitStorageKey } from "../src";

const tokensCss = readFileSync(new URL("../src/styles/tokens.css", import.meta.url), "utf8");

describe("FixedSplitPane", () => {
  it("保存 key の前置きは既定が共通で、製品が上書きできる", () => {
    expect(fixedSplitStorageKey("list")).toBe(`${FIXED_SPLIT_STORAGE_PREFIX}.list`);
    expect(fixedSplitStorageKey("list", "production-ready-nl2sql.fixedSplitPane")).toBe(
      "production-ready-nl2sql.fixedSplitPane.list"
    );
  });

  it("左右のパネルと、既定の日本語で読み上げる divider を描く", () => {
    const html = renderToStaticMarkup(
      <FixedSplitPane splitId="list" preferredWidePane="right" left={<p>一覧</p>} right={<p>詳細</p>} />
    );
    expect(html).toContain("fixed-split-pane__panel--left");
    expect(html).toContain("fixed-split-pane__panel--right");
    expect(html).toContain('role="separator"');
    expect(html).toContain('aria-label="左右ペインの表示比率"');
    expect(html).toContain("右を広く表示");
  });

  it("labels で文言を差し替えられる", () => {
    const html = renderToStaticMarkup(
      <FixedSplitPane
        splitId="list"
        preferredWidePane="left"
        left={null}
        right={null}
        labels={{ separator: "Split ratio", leftWide: "Left wide" }}
      />
    );
    expect(html).toContain('aria-label="Split ratio"');
    expect(html).toContain("Left wide");
  });

  it("構造 CSS を共有 tokens.css から配布する", () => {
    expect(tokensCss).toContain('@import "./structure/fixed-split-pane.css";');
  });
});
