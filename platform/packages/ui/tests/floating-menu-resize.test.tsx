// @vitest-environment happy-dom
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useFloatingMenuPosition } from "../src/components/ui/floating-menu";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// #5380: 開いた後にメニューの中身が伸びたら（候補の描画）、位置と高さを測り直して画面の下へはみ出させない。
// 高さを制限した後（max-height）は測り直さない（flex の子が縮み scrollHeight が中身の高さを表さず、往復するため）。

/** happy-dom は大きさの変化を知らせないので、ResizeObserver を差し替えて `resize()` で呼ぶ。 */
const resizeCallbacks = new Set<() => void>();
class FakeResizeObserver {
  private readonly notify: () => void;
  constructor(callback: ResizeObserverCallback) {
    this.notify = () => callback([], this as unknown as ResizeObserver);
  }
  observe() {
    resizeCallbacks.add(this.notify);
  }
  unobserve() {}
  disconnect() {
    resizeCallbacks.delete(this.notify);
  }
}

let container: HTMLDivElement;
let root: Root;
/** メニューの中身の高さ（px）。 */
let menuHeight = 100;

function rect(top: number, height: number, left = 300, width = 392) {
  return { top, bottom: top + height, left, right: left + width, width, height, x: left, y: top, toJSON: () => ({}) } as DOMRect;
}

function Harness() {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const position = useFloatingMenuPosition({ align: "start", boundary: "viewport", menuRef, open: true, triggerRef });
  return (
    <>
      <button ref={triggerRef} type="button">
        開く
      </button>
      <div ref={menuRef} data-testid="menu" style={position?.style as React.CSSProperties} />
    </>
  );
}

function resize() {
  act(() => {
    for (const callback of resizeCallbacks) callback();
  });
}

const menu = () => container.querySelector<HTMLDivElement>('[data-testid="menu"]')!;

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", FakeResizeObserver);
  vi.stubGlobal("innerHeight", 760);
  vi.stubGlobal("innerWidth", 1280);
  menuHeight = 100;
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  // トリガーは y=362〜398、下の空きは 760 - 8 - 398 - 間隔 ≒ 350px。
  vi.spyOn(HTMLButtonElement.prototype, "getBoundingClientRect").mockImplementation(() => rect(362, 36));
  vi.spyOn(HTMLDivElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLDivElement) {
    const capped = Number.parseFloat(this.style.maxHeight) || Number.POSITIVE_INFINITY;
    return rect(402, Math.min(menuHeight, capped));
  });
  vi.spyOn(HTMLDivElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLDivElement) {
    // 高さを制限すると flex の子が縮み、scrollHeight は中身の高さではなく見えている高さになる。
    const capped = Number.parseFloat(this.style.maxHeight) || Number.POSITIVE_INFINITY;
    return Math.min(menuHeight, capped);
  });
  vi.spyOn(HTMLDivElement.prototype, "offsetWidth", "get").mockReturnValue(392);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  resizeCallbacks.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("浮かぶメニューの大きさの変化（useFloatingMenuPosition。#5380）", () => {
  it("開いた後に中身が伸びたら測り直し、下の空きに収まる高さに制限する", () => {
    act(() => root.render(<Harness />));
    expect(menu().style.maxHeight).toBe("");

    menuHeight = 432;
    resize();
    const limited = menu().style.maxHeight;
    expect(Number.parseFloat(limited)).toBeGreaterThan(0);
    expect(Number.parseFloat(limited)).toBeLessThan(432);

    // 制限した後の大きさの変化では測り直さない（制限と解除を往復しない）。
    for (let index = 0; index < 3; index += 1) {
      resize();
      expect(menu().style.maxHeight).toBe(limited);
    }
  });
});
