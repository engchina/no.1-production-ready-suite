import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { NAV_ITEMS, NAV_SECTIONS, visibleNavSections } from "@/components/layout/nav-config";
import { CAPABILITY_PERMISSIONS, MENU_PERMISSIONS } from "./permissions";
import {
  canOpenDocumentDetail,
  canOpenRoute,
  defaultEntryRoute,
  firstAllowedRoute,
  routeRequiredPermissions,
  settingsEntryRoute,
} from "./route-permissions";
import { APP_ROUTES } from "./routes";

const allow =
  (...codes: string[]) =>
  (permission: string) =>
    codes.includes(permission);

describe("権限コードの対応表", () => {
  it("frontend の権限コードは backend の権限カタログと一致する", () => {
    const catalog = readFileSync(
      resolve(__dirname, "../../../backend/app/security/permissions.py"),
      "utf8"
    );
    const backendCodes = new Set(
      [...catalog.matchAll(/^[A-Z_]+ = "((?:menu|rag)\.[a-z_.]+)"$/gm)].map((match) => match[1])
    );
    const frontendCodes = new Set([
      ...Object.values(MENU_PERMISSIONS),
      ...Object.values(CAPABILITY_PERMISSIONS),
    ]);
    expect(backendCodes.size).toBeGreaterThan(30);
    expect([...frontendCodes].sort()).toEqual([...backendCodes].sort());
  });

  it("ナビのすべての項目にメニュー権限があり、メニュー権限はすべてナビの項目になる", () => {
    const navPermissions = NAV_ITEMS.map((item) => item.permission);
    expect(navPermissions.every((code) => code.startsWith("menu."))).toBe(true);
    expect(new Set(navPermissions).size).toBe(navPermissions.length);
    expect([...navPermissions].sort()).toEqual(Object.values(MENU_PERMISSIONS).sort());
  });

  // Issue 399 / 402 / 409（テスト名に「#」付きの番号を書くと、lint が生の hex 色として検出するため、ここに書く）。
  it("利用の画面はチャット → RAG 検索、業務ビューはナレッジベースの直下、品質評価とフィードバックは「改善・運用」", () => {
    const hrefs = (key: string) => NAV_SECTIONS.find((section) => section.titleKey === key)?.items.map((item) => item.href);
    expect(hrefs("nav.section.rag")).toEqual([APP_ROUTES.chat, APP_ROUTES.search]);
    expect(hrefs("nav.section.ingestion")).toEqual([
      APP_ROUTES.upload,
      APP_ROUTES.fileList,
      APP_ROUTES.knowledgeBases,
      APP_ROUTES.businessViews,
    ]);
    expect(hrefs("nav.section.improve")).toEqual([APP_ROUTES.evaluation, APP_ROUTES.feedback]);
    // NL2SQL と同じ位置: 検索・回答設定（準備）の後、RAG セキュリティの前。
    const keys = NAV_SECTIONS.map((section) => section.titleKey);
    expect(keys.indexOf("nav.section.improve")).toBe(keys.indexOf("nav.section.pipeline") + 1);
    expect(keys.indexOf("nav.section.security")).toBe(keys.indexOf("nav.section.improve") + 1);
  });

  it("ナビの並びは NL2SQL と同じ（RAG セキュリティ → 運用設定 → ユーザーとロール → システム設定）", () => {
    expect(NAV_SECTIONS.map((section) => section.titleKey).slice(-4)).toEqual([
      "nav.section.security",
      "nav.section.operations",
      "nav.section.userRoles",
      "nav.section.settings",
    ]);
    const security = NAV_SECTIONS.find((section) => section.titleKey === "nav.section.security");
    expect(security?.items.map((item) => item.href)).toEqual([APP_ROUTES.securityPermissions]);
    const userRoles = NAV_SECTIONS.find((section) => section.titleKey === "nav.section.userRoles");
    expect(userRoles?.items.map((item) => [item.href, item.permission])).toEqual([
      [APP_ROUTES.securityUsers, MENU_PERMISSIONS.securityUsers],
      [APP_ROUTES.securityRoles, MENU_PERMISSIONS.securityRoles],
    ]);
  });
});

describe("visibleNavSections", () => {
  it("権限のない項目と、項目が 0 件になったセクションを隠す", () => {
    const sections = visibleNavSections(
      allow(MENU_PERMISSIONS.search, MENU_PERMISSIONS.upload, MENU_PERMISSIONS.securityUsers)
    );
    expect(sections.map((section) => section.titleKey)).toEqual([
      "nav.section.rag",
      "nav.section.ingestion",
      "nav.section.userRoles",
    ]);
    expect(sections.flatMap((section) => section.items.map((item) => item.href))).toEqual([
      APP_ROUTES.search,
      APP_ROUTES.upload,
      APP_ROUTES.securityUsers,
    ]);
  });

  it("権限が無ければ空、すべてあれば全項目", () => {
    expect(visibleNavSections(() => false)).toEqual([]);
    expect(visibleNavSections(() => true).flatMap((section) => section.items)).toHaveLength(
      NAV_ITEMS.length
    );
  });
});

describe("ルートの権限", () => {
  it("ナビの URL は項目の権限、詳細画面は開く導線の権限のどれかで開ける", () => {
    expect(routeRequiredPermissions(APP_ROUTES.feedback)).toEqual([MENU_PERMISSIONS.feedback]);
    expect(routeRequiredPermissions(APP_ROUTES.securityPermissions)).toEqual([
      MENU_PERMISSIONS.securityPermissions,
    ]);
    expect(routeRequiredPermissions("/knowledge-bases/kb-1")).toEqual([
      MENU_PERMISSIONS.knowledgeBases,
    ]);
    expect(routeRequiredPermissions("/documents/doc-1")).toEqual([
      MENU_PERMISSIONS.upload,
      MENU_PERMISSIONS.fileList,
    ]);
    expect(routeRequiredPermissions("/")).toBeUndefined();
    expect(routeRequiredPermissions("/unknown")).toBeUndefined();
    expect(routeRequiredPermissions("/documents/")).toBeUndefined();
  });

  it("canOpenRoute は権限のない URL を拒否し、振り分けだけの URL は通す", () => {
    const hasPermission = allow(MENU_PERMISSIONS.chat);
    expect(canOpenRoute(APP_ROUTES.chat, hasPermission)).toBe(true);
    expect(canOpenRoute(APP_ROUTES.search, hasPermission)).toBe(false);
    expect(canOpenRoute(APP_ROUTES.securityUsers, hasPermission)).toBe(false);
    // 文書の詳細はワークスペース専用の API を使うため、チャットの権限だけでは開けない（#303）。
    expect(canOpenRoute("/documents/doc-1", hasPermission)).toBe(false);
    expect(canOpenRoute("/documents/doc-1", allow(MENU_PERMISSIONS.fileList))).toBe(true);
    expect(canOpenRoute("/knowledge-bases/kb-1", hasPermission)).toBe(false);
    expect(canOpenRoute("/", hasPermission)).toBe(true);
  });

  it("文書の詳細へのリンクは、アップロードか文書インデックスの権限があるときだけ出す", () => {
    expect(canOpenDocumentDetail(allow(MENU_PERMISSIONS.upload))).toBe(true);
    expect(canOpenDocumentDetail(allow(MENU_PERMISSIONS.fileList))).toBe(true);
    for (const permission of [
      MENU_PERMISSIONS.search,
      MENU_PERMISSIONS.chat,
      MENU_PERMISSIONS.knowledgeBases,
      MENU_PERMISSIONS.feedback,
    ]) {
      expect(canOpenDocumentDetail(allow(permission))).toBe(false);
    }
  });

  it("既定の入口はチャット（ナビの先頭。`/` と同じ）、開けなければ `/`", () => {
    expect(defaultEntryRoute(() => true)).toBe(APP_ROUTES.chat);
    expect(defaultEntryRoute(allow(MENU_PERMISSIONS.upload, MENU_PERMISSIONS.chat))).toBe(APP_ROUTES.chat);
    // チャットを開けない利用者は `/` へ戻し、ナビの並び順で最初に開ける画面（ここでは RAG 検索）へ。
    expect(defaultEntryRoute(allow(MENU_PERMISSIONS.search))).toBe(APP_ROUTES.home);
    expect(firstAllowedRoute(allow(MENU_PERMISSIONS.search))).toBe(APP_ROUTES.search);
    expect(defaultEntryRoute(() => false)).toBe(APP_ROUTES.home);
  });

  it("`/` の移動先はナビの並びで最初に開ける画面、どれも無ければ権限なし", () => {
    // チャットをナビの先頭に置いたので、すべて開ける利用者の `/` はチャット（#399）。
    expect(firstAllowedRoute(() => true)).toBe(APP_ROUTES.chat);
    expect(firstAllowedRoute(allow(MENU_PERMISSIONS.search, MENU_PERMISSIONS.evaluation))).toBe(APP_ROUTES.search);
    expect(firstAllowedRoute(allow(MENU_PERMISSIONS.upload, MENU_PERMISSIONS.chat))).toBe(
      APP_ROUTES.chat
    );
    expect(firstAllowedRoute(allow(MENU_PERMISSIONS.settingsAppearance))).toBe(
      APP_ROUTES.settingsAppearance
    );
    expect(firstAllowedRoute(() => false)).toBe(APP_ROUTES.forbidden);
  });

  it("/settings はシステム設定のうち最初に開ける画面、無ければ既定の入口", () => {
    expect(settingsEntryRoute(() => true)).toBe(APP_ROUTES.settingsOci);
    expect(settingsEntryRoute(allow(MENU_PERMISSIONS.settingsModel, MENU_PERMISSIONS.search))).toBe(
      APP_ROUTES.settingsModel
    );
    expect(settingsEntryRoute(allow(MENU_PERMISSIONS.search))).toBe(APP_ROUTES.home);
  });
});
