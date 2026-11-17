// @vitest-environment happy-dom
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TOAST_RESERVE_BOTTOM_VAR, useToastBottomReserve } from "../src/components/ui/use-toast-bottom-reserve";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
let container: HTMLDivElement;
let root: Root;
// 通知の領域の上端と高さ（画面の高さは happy-dom の既定の innerHeight）。
let regionTop = 0;
let regionHeight = 0;
let resized: () => void;
const disconnect = vi.fn();
function Harness({ enabled = true }) {
  const ref = useRef<HTMLDivElement>(null);
  useToastBottomReserve(ref, enabled);
  return <div ref={ref} />;
}
const reserve = () => document.documentElement.style.getPropertyValue(TOAST_RESERVE_BOTTOM_VAR);
/** 画面の下端から 8rem（112px）の位置に、高さ `height` の通知の領域を置く。 */
function placeRegion(height: number) {
  regionHeight = height;
  regionTop = window.innerHeight - 112 - height;
}
beforeEach(() => {
  disconnect.mockClear();
  placeRegion(0);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    () => ({ top: regionTop, height: regionHeight }) as DOMRect
  );
  vi.stubGlobal("ResizeObserver", class {
    constructor(callback: () => void) { resized = callback; }
    observe() { /* サイズ変更はテストから通知する。 */ }
    disconnect = disconnect;
  });
});
afterEach(() => {
  act(() => root.unmount());
  document.body.replaceChildren();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("通知が無いときはページの末尾に何も空けない（#5388）", () => {
  act(() => root.render(<Harness />));
  expect(reserve()).toBe("");
});

it("通知の表示中だけ、通知の領域の上端から画面の下端までの高さ + 1rem を空け、積んだ通知の高さに従う", () => {
  act(() => root.render(<Harness />));
  placeRegion(72);
  act(() => resized());
  expect(reserve()).toBe("calc(184px + var(--space-4))");
  // 2 件目を積むと伸びる。
  placeRegion(152);
  act(() => resized());
  expect(reserve()).toBe("calc(264px + var(--space-4))");
  // すべて消えると戻す。
  placeRegion(0);
  act(() => resized());
  expect(reserve()).toBe("");
});

it("md 未満へ狭めたとき・外れたときに空けた高さを解放する", () => {
  placeRegion(72);
  act(() => root.render(<Harness />));
  expect(reserve()).not.toBe("");
  act(() => root.render(<Harness enabled={false} />));
  expect(reserve()).toBe("");
  expect(disconnect).toHaveBeenCalled();
  act(() => root.render(<Harness />));
  expect(reserve()).not.toBe("");
  act(() => root.unmount());
  root = createRoot(container);
  expect(reserve()).toBe("");
});
