import type { Page } from "@playwright/test";

import { expect, test, type MockApi } from "./fixtures/mock-api";

/**
 * DB ゲート（3製品共通の部品。#325）。システム設定の 5 画面以外は、DB が使えるまで本文だけを
 * 案内に替える（サイドナビは残す）。データベース設定への導線・再試行・診断コードを出す。
 */

const VIEWPORTS = [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
] as const;

const SETTINGS_HINT =
  "OCI 認証・アップロード保存先・モデル・データベース・外観の各設定ページは引き続き利用できます。";

type DbStatus = "ok" | "not_configured" | "unreachable";

function setDatabaseStatus(mockApi: MockApi, status: DbStatus, check?: string) {
  Object.assign(mockApi.state.databaseStatus, {
    status,
    check: check ?? (status === "not_configured" ? "missing" : "ok"),
    detail: status === "unreachable" ? "Oracle connection probe failed (ORA-12514)." : null,
  });
}

function gateCard(page: Page) {
  return page.locator('section[aria-labelledby="database-unavailable-title"]');
}

async function expectNoHorizontalOverflow(page: Page) {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(0);
}

async function expectGate(page: Page, title: string) {
  const card = gateCard(page);
  await expect(card.getByRole("heading", { level: 1, name: title })).toBeVisible();
  // ADB の起動・接続情報の確認はデータベース設定の ADB 管理へ（Agent はシステムテーブルの案内を持たない）。
  const link = card.getByRole("link", { name: "データベース設定を開く" });
  await expect(link).toHaveAttribute("href", "/settings/database#adb-management");
  await expect(card.getByRole("button", { name: "再試行" })).toBeVisible();
  await expect(card.getByText(SETTINGS_HINT, { exact: true })).toBeVisible();
  // 本文だけを塞ぎ、サイドナビは残す（設定の画面へ移れる）。
  await expect(page.locator('a[href="/settings/database"]')).toBeAttached();
  await expectNoHorizontalOverflow(page);
  return link;
}

function businessRequests(mockApi: MockApi) {
  return mockApi.requests.filter(
    (request) => request.path.startsWith("/api/runs") || request.path.startsWith("/api/agents")
  );
}

for (const viewport of VIEWPORTS) {
  test.describe(`DB ゲート（${viewport.name}）`, () => {
    test.beforeEach(async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
    });

    test("DB 未設定なら Agent の案内・診断コード・再試行を出し、業務 API を呼ばない", async ({ page, mockApi }) => {
      setDatabaseStatus(mockApi, "not_configured", "wallet_not_found");

      await page.goto("/runs");

      await expectGate(page, "データベースの接続情報が未設定です");
      await expect(
        gateCard(page).getByText(/^Agent の機能（業務 Agent・Run・承認・監査など）を使うには/)
      ).toBeVisible();
      await expect(page.getByText("診断コード: wallet_not_found", { exact: true })).toBeVisible();
      await expect(
        page.getByText(
          "Wallet の保存先に接続用のファイルがそろっていません。Wallet をアップロードし直してください。",
          { exact: true }
        )
      ).toBeVisible();
      expect(businessRequests(mockApi)).toEqual([]);
    });

    test("DB に接続できなければ起動の案内を出し、接続先の詳細は出さない", async ({ page, mockApi }) => {
      setDatabaseStatus(mockApi, "unreachable");

      await page.goto("/agents");

      const link = await expectGate(page, "データベースを起動してください");
      await expect(page.getByText(/ORA-12514/)).toHaveCount(0);
      // 設定を開くリンク → 再試行の順に Tab で移る。
      await link.focus();
      await page.keyboard.press("Tab");
      await expect(gateCard(page).getByRole("button", { name: "再試行" })).toBeFocused();
      expect(businessRequests(mockApi)).toEqual([]);
    });

    test("状態の確認中は経過時間付きの読み込み表示を出す", async ({ page, mockApi }) => {
      setDatabaseStatus(mockApi, "unreachable");
      let release: () => void = () => undefined;
      const released = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route("**/api/ready/database", async (route) => {
        await released;
        await route.fallback();
      });

      await page.goto("/runs");

      const loading = page.getByTestId("database-gate-loading");
      await expect(loading).toBeVisible();
      await expect(loading).toContainText("データベースの状態を確認しています");
      await expectNoHorizontalOverflow(page);
      release();
      await expect(loading).toHaveCount(0);
      await expect(page.getByRole("heading", { level: 1, name: "データベースを起動してください" })).toBeVisible();
    });

    test("DB が使えれば本文を出し、再試行で使えるようになったら本来の画面へ戻る", async ({ page, mockApi }) => {
      setDatabaseStatus(mockApi, "unreachable");

      await page.goto("/runs");
      await expectGate(page, "データベースを起動してください");

      setDatabaseStatus(mockApi, "ok");
      await gateCard(page).getByRole("button", { name: "再試行" }).click();

      await expect(page.getByRole("heading", { level: 1, name: "Run" })).toBeVisible();
      await expect(gateCard(page)).toHaveCount(0);
      await expectNoHorizontalOverflow(page);
    });

    test("システム設定の 5 画面は DB が使えなくても開け、ユーザー管理はゲートを通す", async ({ page, mockApi }) => {
      setDatabaseStatus(mockApi, "not_configured");

      for (const { path, title } of [
        { path: "/settings/oci", title: "OCI 認証設定" },
        { path: "/settings/upload-storage", title: "アップロード保存先" },
        { path: "/settings/model", title: "モデル設定" },
        { path: "/settings/database", title: "データベース設定" },
        { path: "/settings/appearance", title: "外観" },
      ]) {
        await page.goto(path);
        await expect(page.getByRole("heading", { level: 1, name: title }), path).toBeVisible();
        await expect(gateCard(page), path).toHaveCount(0);
      }
      // ゲートを通さない画面では状態 API を呼ばない。
      expect(mockApi.requests.filter((request) => request.path === "/api/ready/database")).toEqual([]);

      // ユーザー・ロール管理は共通 DB にデータを持つため、ゲートを通す（3製品共通）。
      await page.goto("/settings/security/users");
      await expectGate(page, "データベースの接続情報が未設定です");

      // 案内のリンクからデータベース設定（ADB 管理）を開ける。
      await gateCard(page).getByRole("link", { name: "データベース設定を開く" }).click();
      await expect(page).toHaveURL(/\/settings\/database#adb-management$/);
      await expect(page.getByRole("heading", { level: 1, name: "データベース設定" })).toBeVisible();
    });
  });
}
