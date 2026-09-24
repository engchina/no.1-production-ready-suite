import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { Button } from "../src/components/ui/button";
import { Spinner } from "../src/components/ui/spinner";

const tokensCss = readFileSync(
  new URL("../src/styles/tokens/base.css", import.meta.url),
  "utf8"
);

/** `class="…"` から 1 要素分のクラス集合を取り出す。 */
function classesOf(html: string, marker: string) {
  const tag = html.slice(html.indexOf(marker));
  const start = tag.indexOf('class="');
  return tag.slice(start + 7, tag.indexOf('"', start + 7)).split(/\s+/);
}

describe("Spinner", () => {
  it("全周トラックと 270 度アークで構成し、回転してもシルエットが変わらない", () => {
    const html = renderToStaticMarkup(<Spinner />);
    // 閉じた円のトラック（これが無いと欠けた円弧のインク重心が回転で動き、中心ぶれに見える）
    expect(html).toContain('<circle cx="12" cy="12" r="9"');
    expect(html).toContain('opacity="0.25"');
    // 回転を知覚させる 270 度アーク
    expect(html).toContain('d="M21 12a9 9 0 1 0-9 9"');
    expect(html).toContain('viewBox="0 0 24 24"');
  });

  it("既定 16px・animate-spin・motion-reduce 対応で描画する", () => {
    const html = renderToStaticMarkup(<Spinner />);
    expect(html).toContain('width="16"');
    expect(html).toContain('height="16"');
    const classes = classesOf(html, "<svg");
    expect(classes).toContain("animate-spin");
    expect(classes).toContain("motion-reduce:animate-none");
    // flex 内で長いラベルに押されて縮まない
    expect(classes).toContain("shrink-0");
    expect(html).toContain('aria-hidden="true"');
  });

  it("size と className を上書きできる", () => {
    const html = renderToStaticMarkup(<Spinner size={14} className="text-primary" />);
    expect(html).toContain('width="14"');
    expect(classesOf(html, "<svg")).toContain("text-primary");
  });
});

describe("Button loading spinner", () => {
  it("loading 時は Spinner を 16px で描画する（children 側アイコンと同寸法）", () => {
    const html = renderToStaticMarkup(<Button loading>実行</Button>);
    expect(html).toContain('<circle cx="12" cy="12" r="9"');
    expect(html).toContain('width="16"');
    expect(classesOf(html, "<svg")).toContain("animate-spin");
  });

  it("loading 中は children 側の先頭アイコンを隠して無効化する", () => {
    const html = renderToStaticMarkup(<Button loading>実行</Button>);
    // renderToStaticMarkup は class 内の & > を HTML エスケープする
    expect(classesOf(html, "<button")).toContain(
      "[&amp;&gt;svg:not(.animate-spin)]:hidden"
    );
    expect(html).toContain("disabled");
  });

  it("base.css が回転原点を図形中心へ固定し合成レイヤーで回す", () => {
    expect(tokensCss).toMatch(/svg\.animate-spin\s*\{[^}]*transform-box:\s*view-box/);
    expect(tokensCss).toMatch(/svg\.animate-spin\s*\{[^}]*transform-origin:\s*50%\s*50%/);
    expect(tokensCss).toMatch(/svg\.animate-spin\s*\{[^}]*will-change:\s*transform/);
  });
});
