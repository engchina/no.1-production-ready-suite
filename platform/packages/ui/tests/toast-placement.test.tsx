// @vitest-environment happy-dom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../src/components/app-shell/AppShell";
import { PageHeader } from "../src/components/app-shell/PageHeader";
import { Toaster } from "../src/components/ui/toast";
import { resolveToastPlacement, type ToastPlacementInput } from "../src/lib/toast-placement";
import { toast, useToastStore } from "../src/store/toast-store";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// #411: 通知は主操作（PageHeader のページの操作・内容の面の右上の操作・ページの末尾の操作）を覆わない位置に出す。
const desktop: ToastPlacementInput = {
  narrow: false,
  topBar: false,
  viewportWidth: 1280,
  remPx: 14,
  header: { top: 0, bottom: 107, left: 252 },
  headerActions: { left: 953, right: 1252 },
};

describe("resolveToastPlacement", () => {
  it("md 以上: PageHeader に重ね、ページの操作のすぐ左に右端をそろえる（下端には置かない）", () => {
    const { mode, style } = resolveToastPlacement(desktop);
    expect(mode).toBe("page-header");
    expect(style.top).toBe("calc(0px + 1rem)");
    expect(style.right).toBe("calc(327px + 1rem)");
    expect(style.minWidth).toBe("min(var(--toast-width-min), 673px)");
    expect(style.maxWidth).toBe("min(var(--toast-width-max), 673px)");
    expect(style.left).toBeUndefined();
    expect(style).not.toHaveProperty("bottom");
  });

  it("md 以上: ページの操作が無い PageHeader では画面の右端から 1rem", () => {
    const { mode, style } = resolveToastPlacement({ ...desktop, headerActions: null });
    expect(mode).toBe("page-header");
    expect(style.right).toBe("max(1rem, env(safe-area-inset-right))");
    expect(style.minWidth).toBe("min(var(--toast-width-min), 1000px)");
    expect(style.maxWidth).toBe("min(var(--toast-width-max), 1000px)");
  });

  it("md 以上: ページの操作がタイトルの下へ折り返して左にあるときは、PageHeader の右端に重ねる", () => {
    const { mode, style } = resolveToastPlacement({ ...desktop, headerActions: { left: 280, right: 513 } });
    expect(mode).toBe("page-header");
    expect(style.top).toBe("calc(0px + 1rem)");
    expect(style.right).toBe("max(1rem, env(safe-area-inset-right))");
    expect(style.minWidth).toBe("min(var(--toast-width-min), 739px)");
    expect(style.maxWidth).toBe("min(var(--toast-width-max), 739px)");
  });

  it("md 以上: ページの操作の左右のどちらにも 14rem が取れなければ、PageHeader の下端の 1rem 下の右に出す", () => {
    // 14rem = 196px。左: 操作の左端 − PageHeader の左端 − 2rem が 195px。右: 画面の右端 − 1rem − 操作の右端 − 1rem が 100px。
    const { mode, style } = resolveToastPlacement({ ...desktop, headerActions: { left: 252 + 28 + 195, right: 1152 } });
    expect(mode).toBe("below-page-header");
    expect(style.top).toBe("calc(107px + 1rem)");
    expect(style.right).toBe("max(1rem, env(safe-area-inset-right))");
    expect(style.maxWidth).toBe("min(var(--toast-width-max), calc(100vw - 2 * 1rem))");
  });

  it("md 以上: 幅は固定にせず、内容に合わせて下限〜上限のトークンの間で広がる（#899）", () => {
    // 固定の 22rem では「Oracle Profile の反映が完了しました。」が「…完了しま / した。」と語の途中で 2 行に折り返した。
    const { style } = resolveToastPlacement(desktop);
    expect(style).not.toHaveProperty("width");
    // トークンの値は tokens-css.test.ts で確かめる。
  });

  it("md 以上: lg 未満で PageHeader が一部だけ見えている間は画面の上端に合わせ、見えなくなったら画面の右上", () => {
    expect(resolveToastPlacement({ ...desktop, header: { top: -60, bottom: 47, left: 252 } }).style.top).toBe(
      "calc(0px + 1rem)"
    );
    for (const header of [{ top: -107, bottom: 0, left: 252 }, null]) {
      const { mode, style } = resolveToastPlacement({ ...desktop, header });
      expect(mode).toBe("top-right");
      expect(style.top).toBe("max(1rem, env(safe-area-inset-top))");
      expect(style.right).toBe("max(1rem, env(safe-area-inset-right))");
    }
  });

  it("md 未満: 上端の全幅。上端のバーがあればメニューのボタン（左の 0.5rem + 44px）を覆わない", () => {
    const withBar = resolveToastPlacement({ ...desktop, narrow: true, topBar: true });
    expect(withBar.mode).toBe("top-bar");
    expect(withBar.style.top).toBe("max(0.5rem, env(safe-area-inset-top))");
    expect(withBar.style.left).toBe("calc(1rem + var(--control-height-touch))");
    expect(withBar.style.right).toBe("max(1rem, env(safe-area-inset-right))");
    expect(withBar.style.minWidth).toBeUndefined();
    expect(withBar.style.maxWidth).toBeUndefined();

    const withoutBar = resolveToastPlacement({ ...desktop, narrow: true, topBar: false, header: null });
    expect(withoutBar.style.left).toBe("max(1rem, env(safe-area-inset-left))");
  });
});

let container: HTMLDivElement;
let root: Root;
let headerRect = { top: 0, bottom: 0, left: 0 };
let actionsLeft = 0;

function mockViewport(narrow: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: narrow,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }));
}

function render(node: ReactNode) {
  act(() => root.render(node));
}

function Page() {
  return (
    <AppShell sidebar={<nav aria-label="サイドナビゲーション" />}>
      <PageHeader title="コメント管理" actions={[{ id: "save", kind: "primary", label: "保存" }]} />
      <Toaster />
    </AppShell>
  );
}

const region = () => document.body.querySelector<HTMLElement>('[role="region"][aria-label="通知"]');

beforeEach(() => {
  vi.useFakeTimers();
  useToastStore.getState().clear();
  useToastStore.getState().resume();
  headerRect = { top: 0, bottom: 0, left: 0 };
  actionsLeft = 0;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    let rect: { top: number; bottom: number; left: number; right?: number } = { top: 0, bottom: 0, left: 0 };
    if (this.hasAttribute("data-page-header")) rect = headerRect;
    if (this.hasAttribute("data-page-header-actions")) rect = { top: 18, bottom: 54, left: actionsLeft, right: 1252 };
    return {
      x: rect.left,
      y: rect.top,
      width: 0,
      height: rect.bottom - rect.top,
      right: 0,
      ...rect,
      toJSON: () => ({}),
    } as DOMRect;
  });
  vi.spyOn(document.documentElement, "clientWidth", "get").mockReturnValue(1280);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  act(() => useToastStore.getState().clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Toaster の置き場所（#411）", () => {
  it("md 以上: PageHeader に重ね、ページの操作のすぐ左に出す", () => {
    mockViewport(false);
    headerRect = { top: 0, bottom: 107, left: 252 };
    actionsLeft = 953;
    render(<Page />);
    act(() => {
      toast.success("保存しました");
    });
    const el = region()!;
    expect(el.dataset.toastPlacement).toBe("page-header");
    expect(el.style.right).toBe("calc(327px + 1rem)");
    expect(el.style.bottom).toBe("");
    expect(el.style.left).toBe("");
  });

  it("md 以上: lg 未満で PageHeader が本文と一緒にスクロールして見えなくなったら、画面の右上へ移る", () => {
    mockViewport(false);
    headerRect = { top: 0, bottom: 107, left: 252 };
    actionsLeft = 953;
    render(<Page />);
    act(() => {
      toast.success("保存しました");
    });
    expect(region()!.dataset.toastPlacement).toBe("page-header");
    headerRect = { top: -120, bottom: -13, left: 252 };
    act(() => {
      document.dispatchEvent(new Event("scroll"));
      vi.advanceTimersByTime(32);
    });
    expect(region()!.dataset.toastPlacement).toBe("top-right");
  });

  it("md 未満: 上端のバーに重ね、メニューのボタンを覆わない", () => {
    mockViewport(true);
    headerRect = { top: 49, bottom: 240, left: 0 };
    render(<Page />);
    act(() => {
      toast.success("保存しました");
    });
    const el = region()!;
    expect(el.dataset.toastPlacement).toBe("top-bar");
    expect(el.style.left).toBe("calc(1rem + var(--control-height-touch))");
    expect(el.style.bottom).toBe("");
  });
});
