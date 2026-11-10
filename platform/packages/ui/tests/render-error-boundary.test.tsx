// @vitest-environment happy-dom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RenderErrorBoundary } from "../src";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let shouldThrow = true;

function Boom() {
  if (shouldThrow) throw new TypeError("描画の失敗");
  return <p>本文</p>;
}

function Harness({ label, routeKey, placement }: { label?: string; routeKey: string; placement?: "inline" | "page" }) {
  const [, setTick] = useState(0);
  return (
    <div>
      <nav>ナビ</nav>
      <button type="button" onClick={() => setTick((tick) => tick + 1)}>更新</button>
      <RenderErrorBoundary key={routeKey} serviceName="production-ready-nl2sql" label={label} placement={placement}>
        <Boom />
      </RenderErrorBoundary>
    </div>
  );
}

beforeEach(() => {
  shouldThrow = true;
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("RenderErrorBoundary（#4800）", () => {
  it("描画の例外を範囲の中に閉じ込め、外（ナビ）を残して日本語のエラーと再試行を出し、診断のログを書く", () => {
    act(() => root.render(<Harness routeKey="/a" />));
    expect(container.textContent).toContain("ナビ");
    const alert = container.querySelector("[data-render-error-boundary] [role='alert']");
    expect(alert?.textContent).toContain("この項目を表示できません");
    expect(alert?.textContent).toContain("ページを再読み込みしてください");
    const button = Array.from(container.querySelectorAll("button")).find((el) => el.textContent === "再試行");
    expect(button).toBeDefined();
    const logged = (console.error as unknown as { mock: { calls: unknown[][] } }).mock.calls
      .map((call) => String(call[0]))
      .find((line) => line.includes('"event":"card_render_failed"'));
    expect(logged).toContain('"service_name":"production-ready-nl2sql"');
    // 業務の本文（Error.message）はログに出さない。
    expect(logged).not.toContain("描画の失敗");
  });

  it("名前があれば見出しに出し、再試行で描画し直す", () => {
    act(() => root.render(<Harness routeKey="/a" label="カード A" />));
    expect(container.textContent).toContain("「カード A」を表示できません");
    shouldThrow = false;
    const button = Array.from(container.querySelectorAll("button")).find((el) => el.textContent === "再試行")!;
    act(() => button.click());
    expect(container.textContent).toContain("本文");
    expect(container.querySelector("[data-render-error-boundary]")).toBeNull();
  });

  it("key が変われば（別の画面へ移れば）エラーの状態を解く", () => {
    act(() => root.render(<Harness routeKey="/a" />));
    expect(container.querySelector("[data-render-error-boundary]")).not.toBeNull();
    shouldThrow = false;
    act(() => root.render(<Harness routeKey="/b" />));
    expect(container.textContent).toContain("本文");
  });

  it("placement=page は本文の余白（PageBody）の中に出し、既定（inline）は置かれた場所にそのまま出す", () => {
    act(() => root.render(<Harness routeKey="/a" placement="page" />));
    expect(container.querySelector("[data-render-error-boundary]")?.className).toContain("px-4");
    act(() => root.render(<Harness routeKey="/b" />));
    expect(container.querySelector("[data-render-error-boundary]")?.className ?? "").toBe("");
  });
});
