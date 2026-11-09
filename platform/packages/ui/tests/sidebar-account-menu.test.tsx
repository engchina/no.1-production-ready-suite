// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SidebarAccountFooter } from "../src/components/app-shell/Sidebar";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const account = { href: "/app/account", label: "アカウント" };
const labels = { logout: "ログアウト", switchToLight: "", switchToDark: "", accountMenu: "アカウントのメニュー" };

let container: HTMLDivElement;
let root: Root;

function render(props: Partial<Parameters<typeof SidebarAccountFooter>[0]> = {}) {
  act(() =>
    root.render(
      <SidebarAccountFooter name="山田" roles="管理者" collapsed={false} account={account} labels={labels} onLogout={() => undefined} {...props} />
    )
  );
}

const trigger = () => document.querySelector<HTMLElement>('[data-testid="sidebar-account"]')!;
const menu = () => document.querySelector<HTMLElement>('[role="menu"]');
const items = () => Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]'));
const openMenu = () => act(() => trigger().click());
const press = (target: Element, key: string, init: KeyboardEventInit = {}) =>
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key, ...init }));
  });

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

describe("サイドナビ下部の利用者のメニュー（#4724）", () => {
  it("閉じている間はログアウトの行もメニューも出さず、ボタンは menu を開くことを支援技術に伝える", () => {
    render();
    expect(container.textContent).not.toContain("ログアウト");
    expect(menu()).toBeNull();
    expect(trigger().tagName).toBe("BUTTON");
    expect(trigger().getAttribute("aria-haspopup")).toBe("menu");
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
    expect(trigger().hasAttribute("aria-controls")).toBe(false);
    expect(trigger().getAttribute("aria-label")).toBe("アカウントのメニュー 山田 管理者");
  });

  it("押すと「アカウント」「ログアウト」だけのメニューを開き、先頭の項目へフォーカスする", () => {
    render();
    openMenu();
    expect(trigger().getAttribute("aria-expanded")).toBe("true");
    expect(trigger().getAttribute("aria-controls")).toBe(menu()?.id);
    expect(menu()?.getAttribute("aria-label")).toBe("アカウントのメニュー 山田");
    expect(items().map((item) => item.textContent)).toEqual(["アカウント", "ログアウト"]);
    expect(items()[0].getAttribute("href")).toBe("/app/account");
    expect(document.activeElement).toBe(items()[0]);
  });

  it("もう一度押す・外を押すと閉じる", () => {
    render();
    openMenu();
    openMenu();
    expect(menu()).toBeNull();
    openMenu();
    act(() => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    });
    expect(menu()).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
  });

  it("↑ ↓ Home End で移動し、Esc で閉じてボタンへ戻す", () => {
    render();
    openMenu();
    press(items()[0], "ArrowDown");
    expect(document.activeElement).toBe(items()[1]);
    press(items()[1], "ArrowDown");
    expect(document.activeElement).toBe(items()[0]);
    press(items()[0], "End");
    expect(document.activeElement).toBe(items()[1]);
    press(items()[1], "Home");
    expect(document.activeElement).toBe(items()[0]);
    press(items()[0], "Escape");
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it("Tab で閉じてボタンの位置へ戻す", () => {
    render();
    openMenu();
    press(items()[0], "Tab");
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it("ログアウトを押すとメニューを閉じて onLogout を 1 回呼ぶ", () => {
    const onLogout = vi.fn();
    render({ onLogout });
    openMenu();
    act(() => items()[1].click());
    expect(onLogout).toHaveBeenCalledTimes(1);
    expect(menu()).toBeNull();
    expect(document.activeElement).toBe(trigger());
  });

  it("アカウントはクリックでも Space でもアカウントの画面へ移り、メニューを閉じる", () => {
    const onNavigate = vi.fn();
    render({ account: { ...account, onNavigate } });
    openMenu();
    act(() => items()[0].click());
    expect(onNavigate).toHaveBeenCalledTimes(1);
    expect(menu()).toBeNull();
    openMenu();
    press(items()[0], " ");
    expect(onNavigate).toHaveBeenCalledTimes(2);
    expect(menu()).toBeNull();
  });

  it("onLogout が無い（ログイン省略）ならメニューはアカウントだけ", () => {
    render({ onLogout: undefined });
    openMenu();
    expect(items().map((item) => item.textContent)).toEqual(["アカウント"]);
  });

  it("折りたたみ時もアイコンの押下で同じメニューを開き、ログアウトの行は出さない", () => {
    render({ collapsed: true });
    expect(container.querySelector('button[aria-label="ログアウト"]')).toBeNull();
    expect(trigger().getAttribute("aria-label")).toBe("アカウントのメニュー 山田");
    openMenu();
    expect(items().map((item) => item.textContent)).toEqual(["アカウント", "ログアウト"]);
  });

  it("アカウントの画面を開いている間はボタンを現在地にし、メニューの項目にも示す", () => {
    render({ account: { ...account, active: true } });
    expect(trigger().getAttribute("aria-current")).toBe("page");
    expect(trigger().className).toContain("bg-accent-emphasis");
    openMenu();
    expect(items()[0].getAttribute("aria-current")).toBe("page");
    expect(items()[1].hasAttribute("aria-current")).toBe(false);
  });

  it("メニューの中身が無ければボタンにせず表示だけにする", () => {
    render({ account: undefined, onLogout: undefined });
    expect(trigger().tagName).toBe("DIV");
    expect(trigger().hasAttribute("aria-haspopup")).toBe(false);
  });
});
