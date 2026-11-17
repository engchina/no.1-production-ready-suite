import { describe, expect, it } from "vitest";

import { computeFloatingMenuLayout, rangedMenuWidth } from "../src/components/ui/floating-menu";

// #5267: 検索つきの選択（単一・複数）の開いた popover の幅。トリガーと同じ幅を 20〜40rem に収め、
// 40rem を超えるトリガーではトリガーの左端にそろえて 40rem。sm（640px）未満は画面の幅から左右の余白を引いた幅。
// ルートの文字は 14px（20rem = 280px、40rem = 560px、余白 1rem = 14px）。
const range = { min: 280, max: 560, gutter: 14, narrowBelow: 640 };

describe("rangedMenuWidth", () => {
  it("トリガーの幅が範囲の中なら同じ幅", () => {
    expect(rangedMenuWidth(392, 1440, range)).toBe(392);
  });

  it("狭いトリガーは 20rem、広いトリガー（RAG 検索の full の欄）は 40rem に収める", () => {
    expect(rangedMenuWidth(140, 1440, range)).toBe(280);
    expect(rangedMenuWidth(1080, 1440, range)).toBe(560);
  });

  it("sm 未満の画面（375px）は、トリガーの幅によらず画面の幅から左右の余白を引いた幅", () => {
    expect(rangedMenuWidth(140, 375, range)).toBe(347);
    expect(rangedMenuWidth(319, 375, range)).toBe(347);
  });

  it("画面の幅を超えない", () => {
    expect(rangedMenuWidth(560, 660, range)).toBe(560);
    expect(rangedMenuWidth(600, 600, range)).toBe(572);
  });
});

describe("computeFloatingMenuLayout（widthRange）", () => {
  const trigger = (left: number, width: number) => ({
    top: 200,
    bottom: 236,
    left,
    right: left + width,
    width,
  });
  const base = { align: "start" as const, menuWidth: 999, naturalHeight: 300, viewportHeight: 900, widthRange: range };

  it("左端をトリガーにそろえ、40rem を超えるトリガーでは 40rem にする", () => {
    const layout = computeFloatingMenuLayout({ ...base, triggerRect: trigger(300, 1080), viewportWidth: 1440 });
    expect(layout.style).toMatchObject({ left: 300, width: 560, top: 240 });
    expect(layout.style.maxWidth).toBeUndefined();
  });

  it("右に入らなければ右端をトリガーにそろえ、それでも入らなければ画面の余白の内側へずらす", () => {
    const flipped = computeFloatingMenuLayout({ ...base, triggerRect: trigger(1300, 100), viewportWidth: 1440 });
    expect(flipped.style).toMatchObject({ left: 1400 - 280, width: 280 });
    const clamped = computeFloatingMenuLayout({ ...base, triggerRect: trigger(1020, 420), viewportWidth: 1440 });
    expect(clamped.style).toMatchObject({ left: 1440 - 14 - 420, width: 420 });
  });

  it("375px は画面の左右の余白の内側いっぱい", () => {
    const layout = computeFloatingMenuLayout({ ...base, triggerRect: trigger(28, 200), viewportWidth: 375 });
    expect(layout.style).toMatchObject({ left: 14, width: 347 });
  });
});
