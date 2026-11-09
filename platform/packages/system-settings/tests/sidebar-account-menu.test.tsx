// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SidebarAccount } from "../src";

const account = vi.hoisted(() => ({ current: null as SidebarAccount | null }));
vi.mock("../src/auth/RequireAuth", () => ({ useSidebarAccount: () => account.current }));

const { SidebarAccountSection } = await import("../src/auth/SidebarAccountSection");

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const routes = { login: "/login" };
let container: HTMLDivElement;
let root: Root;

function setAccount(overrides: Partial<SidebarAccount> = {}) {
  account.current = {
    name: "利用者 A",
    roles: "管理者",
    rolesLabel: "ロール: 管理者",
    account: { href: "/app/account", label: "アカウント", active: false },
    onLogout: vi.fn(),
    labels: { logout: "ログアウト", switchToLight: "", switchToDark: "", accountMenu: "アカウントのメニュー" },
    debugMode: false,
    ...overrides,
  };
}

async function clickLogout() {
  act(() => document.querySelector<HTMLElement>('[data-testid="sidebar-account"]')!.click());
  const logout = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((item) => item.textContent === "ログアウト")!;
  await act(async () => logout.click());
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("サイドナビの利用者のメニューのログアウト（#4724）", () => {
  it("未保存の確認で中止するとログアウトしない", async () => {
    setAccount();
    const confirmLeave = vi.fn().mockResolvedValue(false);
    act(() => root.render(<SidebarAccountSection routes={routes} collapsed={false} confirmLeave={confirmLeave} />));
    await clickLogout();
    expect(confirmLeave).toHaveBeenCalledTimes(1);
    expect(account.current?.onLogout).not.toHaveBeenCalled();
  });

  it("確認を通ればログアウトする", async () => {
    setAccount();
    const confirmLeave = vi.fn().mockResolvedValue(true);
    act(() => root.render(<SidebarAccountSection routes={routes} collapsed={false} confirmLeave={confirmLeave} />));
    await clickLogout();
    expect(account.current?.onLogout).toHaveBeenCalledTimes(1);
  });

  it("ログイン省略ではメニューはアカウントだけで、ログイン省略の表示は欄に残る", () => {
    setAccount({ onLogout: undefined, debugMode: true });
    act(() => root.render(<SidebarAccountSection routes={routes} collapsed={false} />));
    expect(container.querySelector('[role="status"]')?.getAttribute("aria-label")).toBe("ログイン省略");
    act(() => document.querySelector<HTMLElement>('[data-testid="sidebar-account"]')!.click());
    expect(Array.from(document.querySelectorAll('[role="menuitem"]')).map((item) => item.textContent)).toEqual(["アカウント"]);
  });
});
