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
    const hasPermission = allow("menu.file_list", "menu.search");
    expect(loginEntryRoute("/file-list")(hasPermission)).toBe("/file-list");
    // 権限の無い URL・認証画面・外部 URL・不正な値は既定の入口（RAG 検索）へ。
    expect(loginEntryRoute("/settings/oci")(hasPermission)).toBe(APP_ROUTES.search);
    expect(loginEntryRoute(APP_ROUTES.login)(hasPermission)).toBe(APP_ROUTES.search);
    expect(loginEntryRoute(APP_ROUTES.forbidden)(hasPermission)).toBe(APP_ROUTES.search);
    expect(loginEntryRoute("//evil.example.com")(hasPermission)).toBe(APP_ROUTES.search);
    expect(loginEntryRoute("https://evil.example.com")(hasPermission)).toBe(APP_ROUTES.search);
    expect(loginEntryRoute(undefined)(hasPermission)).toBe(APP_ROUTES.search);
    // RAG 検索を開けなければ `/`（`/` が最初に開ける画面へ振り分ける）。
    expect(loginEntryRoute(42)(allow("menu.chat"))).toBe(APP_ROUTES.home);
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
    expect(isDatabaseGateExempt(APP_ROUTES.search)).toBe(false);
    expect(isDatabaseGateExempt("/settingsx")).toBe(false);
  });
});
