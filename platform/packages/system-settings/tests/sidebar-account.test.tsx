import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { SidebarAccount } from "../src";

const account = vi.hoisted(() => ({ current: null as SidebarAccount | null }));
vi.mock("../src/auth/RequireAuth", () => ({ useSidebarAccount: () => account.current }));

const { SidebarAccountSection } = await import("../src/auth/SidebarAccountSection");

const routes = { login: "/login", passwordChange: "/password/change" };

function base(overrides: Partial<SidebarAccount>): SidebarAccount {
  return {
    name: "営業ユーザー",
    roles: "ロール: SALES",
    actions: [],
    onLogout: () => undefined,
    labels: { logout: "ログアウト", switchToLight: "", switchToDark: "" },
    debugMode: false,
    ...overrides,
  };
}

describe("サイドバーのアカウント欄（#307）", () => {
  it("未ログインなら何も出さない", () => {
    account.current = null;
    expect(renderToStaticMarkup(<SidebarAccountSection routes={routes} collapsed={false} />)).toBe("");
  });

  it("ログイン中は利用者名・ロール・ログアウトを出し、ログイン省略の表示は出さない", () => {
    account.current = base({});
    const html = renderToStaticMarkup(<SidebarAccountSection routes={routes} collapsed={false} />);
    expect(html).toContain("営業ユーザー");
    expect(html).toContain("ロール: SALES");
    expect(html).toContain("ログアウト");
    expect(html).not.toContain("ログイン省略");
  });

  it("ログイン省略では利用者名とログイン省略の表示を出し、ログアウトは出さない", () => {
    account.current = base({ name: "ローカル利用者", roles: "ロール: SYSTEM_ADMIN", onLogout: undefined, debugMode: true });
    const html = renderToStaticMarkup(<SidebarAccountSection routes={routes} collapsed={false} />);
    expect(html).toContain("ローカル利用者");
    expect(html).toContain("ログイン省略");
    expect(html).toContain('role="status"');
    expect(html).not.toContain("ログアウト");
  });
});
