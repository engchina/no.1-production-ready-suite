import { describe, expect, it } from "vitest";

import { isDatabaseGateExempt } from "@/components/system/DatabaseGate";
import { APP_ROUTES } from "@/lib/routes";
import { loginEntryRoute } from "./AuthPages";

const allow =
  (...codes: string[]) =>
  (permission: string) =>
    codes.includes(permission);

describe("loginEntryRoute", () => {
  it("ログイン前に開こうとした URL へ、権限があれば戻す", () => {
    const hasPermission = allow("menu.file_list", "menu.dashboard");
    expect(loginEntryRoute("/file-list")(hasPermission)).toBe("/file-list");
    // 権限の無い URL・認証画面・外部 URL・不正な値は既定の入口へ。
    expect(loginEntryRoute("/settings/oci")(hasPermission)).toBe(APP_ROUTES.dashboard);
    expect(loginEntryRoute(APP_ROUTES.login)(hasPermission)).toBe(APP_ROUTES.dashboard);
    expect(loginEntryRoute(APP_ROUTES.forbidden)(hasPermission)).toBe(APP_ROUTES.dashboard);
    expect(loginEntryRoute("//evil.example.com")(hasPermission)).toBe(APP_ROUTES.dashboard);
    expect(loginEntryRoute("https://evil.example.com")(hasPermission)).toBe(APP_ROUTES.dashboard);
    expect(loginEntryRoute(undefined)(hasPermission)).toBe(APP_ROUTES.dashboard);
    expect(loginEntryRoute(42)(allow("menu.chat"))).toBe(APP_ROUTES.chat);
  });
});

describe("isDatabaseGateExempt", () => {
  it("設定ページは DB ゲートを通さないが、ユーザー・ロール・権限管理は通す", () => {
    expect(isDatabaseGateExempt("/settings")).toBe(true);
    expect(isDatabaseGateExempt(APP_ROUTES.settingsDatabase)).toBe(true);
    expect(isDatabaseGateExempt(APP_ROUTES.settingsPipeline)).toBe(true);
    expect(isDatabaseGateExempt(APP_ROUTES.securityUsers)).toBe(false);
    expect(isDatabaseGateExempt(APP_ROUTES.securityRoles)).toBe(false);
    expect(isDatabaseGateExempt(APP_ROUTES.securityPermissions)).toBe(false);
    expect(isDatabaseGateExempt(APP_ROUTES.dashboard)).toBe(false);
    expect(isDatabaseGateExempt("/settingsx")).toBe(false);
  });
});
