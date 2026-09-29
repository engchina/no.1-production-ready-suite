import { readFileSync } from "node:fs";

import { Upload } from "lucide-react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { BUTTON_ICON_SIZE, Button } from "../src/components/ui/button";
import { SPINNER_STROKE_PX, Spinner, spinnerGeometry } from "../src/components/ui/spinner";

const readCss = (path: string) =>
  readFileSync(new URL(`../src/styles/tokens/${path}`, import.meta.url), "utf8");
const baseCss = readCss("base.css");
const colorsCss = readCss("colors.css");
const a11yCss = readCss("a11y.css");

/** `class="…"` から 1 要素分のクラス集合を取り出す。 */
function classesOf(html: string, marker: string) {
  const tag = html.slice(html.indexOf(marker));
  const start = tag.indexOf('class="');
  return tag.slice(start + 7, tag.indexOf('"', start + 7)).split(/\s+/);
}

function attr(html: string, marker: string, name: string) {
  const tag = html.slice(html.indexOf(marker));
  return tag.match(new RegExp(`${name}="([^"]*)"`))?.[1];
}

describe("Spinner", () => {
  it("全周トラックと 270 度アークで構成し、回転してもシルエットが変わらない", () => {
    const html = renderToStaticMarkup(<Spinner />);
    // 閉じた円のトラック（これが無いと欠けた円弧のインク重心が回転で動き、中心ぶれに見える）
    expect(html).toContain('<circle class="pr-spinner-track" cx="12" cy="12" r="8.5"');
    // 固定の opacity ではなく、トークン（--color-spinner-track）で色を付ける
    expect(html).not.toContain("opacity=");
    // 回転を知覚させる 270 度アーク（3 時から反時計回りに 6 時まで）
    expect(html).toContain('class="pr-spinner-arc" d="M20.5 12a8.5 8.5 0 1 0-8.5 8.5"');
    expect(html).toContain('viewBox="0 0 24 24"');
  });

  it.each([
    [14, 3.429, 8.286],
    [16, 3, 8.5],
    [20, 2.4, 8.8],
    [24, 2, 9],
  ])("%ipx でも線の実寸は 2px、外縁の半径は 10（viewBox 単位）", (size, strokeWidth, radius) => {
    expect(spinnerGeometry(size)).toEqual({ strokeWidth, radius });
    // 実寸 = viewBox 上の線幅 × size / 24
    expect((strokeWidth * size) / 24).toBeCloseTo(SPINNER_STROKE_PX, 2);
    expect(radius + strokeWidth / 2).toBeCloseTo(10, 2);

    const html = renderToStaticMarkup(<Spinner size={size} />);
    expect(attr(html, "<svg", "stroke-width")).toBe(String(strokeWidth));
    expect(attr(html, "<circle", "r")).toBe(String(radius));
    expect(html).toContain(`a${radius} ${radius} 0 1 0-${radius} ${radius}`);
  });

  it("既定 16px・animate-spin・motion-reduce でも回転を止めない（#440）", () => {
    const html = renderToStaticMarkup(<Spinner />);
    expect(html).toContain('width="16"');
    expect(html).toContain('height="16"');
    const classes = classesOf(html, "<svg");
    expect(classes).toContain("animate-spin");
    expect(classes).not.toContain("motion-reduce:animate-none");
    // flex 内で長いラベルに押されて縮まない
    expect(classes).toContain("shrink-0");
    expect(html).toContain('aria-hidden="true"');
  });

  it("size と className を上書きできる", () => {
    const html = renderToStaticMarkup(<Spinner size={14} className="text-accent-fg" />);
    expect(html).toContain('width="14"');
    expect(classesOf(html, "<svg")).toContain("text-accent-fg");
  });
});

describe("Spinner の CSS（トークン・reduced-motion）", () => {
  it("トラックはアークと同じ色を透かすトークン（ライト 30% / ダーク 35%）", () => {
    expect(colorsCss).toMatch(
      /--color-spinner-track: light-dark\(\s*color-mix\(in srgb, currentColor 30%, transparent\),\s*color-mix\(in srgb, currentColor 35%, transparent\)\s*\);/
    );
    // color-scheme を切り替える要素すべてで宣言し直すブロックの中に置く（Lightning CSS 変換後もダーク値になる）
    const scoped = colorsCss.slice(colorsCss.indexOf(":root, [data-theme], [data-surface] {"));
    expect(scoped.slice(0, scoped.indexOf("\n}\n"))).toContain("--color-spinner-track:");
    expect(baseCss).toMatch(/\.pr-spinner-track\s*\{\s*stroke:\s*var\(--color-spinner-track\);/);
    // 強制カラーモードでは透過色を使わずシステム色にする
    expect(a11yCss).toMatch(/@media \(forced-colors: active\)[\s\S]*--color-spinner-track: GrayText;/);
  });

  it("reduced-motion でも回転を続ける（一括無効化より強い詳細度で 1s に戻す。#440）", () => {
    const reduced = baseCss.slice(baseCss.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reduced).toMatch(/svg\.animate-spin\s*\{\s*animation-duration: 1s !important;/);
    expect(baseCss).not.toContain("pr-spinner-breathe");
  });

  it("base.css が回転原点を図形中心へ固定し合成レイヤーで回す", () => {
    expect(baseCss).toMatch(/svg\.animate-spin\s*\{[^}]*transform-box:\s*view-box/);
    expect(baseCss).toMatch(/svg\.animate-spin\s*\{[^}]*transform-origin:\s*50%\s*50%/);
    expect(baseCss).toMatch(/svg\.animate-spin\s*\{[^}]*will-change:\s*transform/);
  });
});

describe("Button loading spinner", () => {
  it.each(["sm", "md", "lg"] as const)(
    "size=%s でもスピナーはアイコンと同じ 16px（loading で先頭スロットの寸法が変わらない）",
    (size) => {
      expect(BUTTON_ICON_SIZE).toBe(16);
      const idle = renderToStaticMarkup(
        <Button size={size} icon={Upload}>
          実行
        </Button>
      );
      const busy = renderToStaticMarkup(
        <Button size={size} icon={Upload} loading>
          実行
        </Button>
      );
      expect(attr(idle, "<svg", "width")).toBe("16");
      expect(attr(busy, "<svg", "width")).toBe("16");
      expect(attr(busy, "<svg", "height")).toBe("16");
      expect(classesOf(busy, "<svg")).toContain("animate-spin");
      // loading 中は 1 つだけ（アイコンとスピナーを重ねない）
      expect(busy.match(/<svg/g)).toHaveLength(1);
    }
  );

  it("loading 中のスピナーは fg-muted（disabled の文字色 fg-disabled では地に対して 3:1 に届かない）", () => {
    const html = renderToStaticMarkup(
      <Button icon={Upload} loading>
        実行
      </Button>
    );
    expect(classesOf(html, "<svg")).toContain("text-fg-muted");
  });

  it("loading 中は children 側の先頭アイコンを隠して無効化する", () => {
    const html = renderToStaticMarkup(<Button loading>実行</Button>);
    // renderToStaticMarkup は class 内の & > を HTML エスケープする
    expect(classesOf(html, "<button")).toContain(
      "[&amp;&gt;svg:not(.animate-spin)]:hidden"
    );
    // loading 中はネイティブの disabled ではなく aria-disabled（フォーカスを保つ。#355）
    expect(html).toContain('aria-disabled="true"');
  });
});
