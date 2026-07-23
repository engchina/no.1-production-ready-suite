import { expect, test, type Page } from "@playwright/test";

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const data =
      path === "/api/auth/me"
        ? {
            user_id: "admin",
            login_name: "SYSTEM",
            display_name: "システム管理者",
            status: "ACTIVE",
            force_password_change: false,
            role_codes: ["SYSTEM_ADMIN"],
            permissions: [],
            data_entitlements: [],
          }
        : path === "/api/ready/database"
        ? { status: "ok", check: "ok", detail: null }
        : path === "/api/nl2sql/persistence"
          ? {
              mode: "oracle",
              ready: true,
              durable: true,
              writable: true,
              snapshot_loaded: true,
              reason_code: null,
              checked_at: "2026-07-19T00:00:00Z",
            }
        : path === "/api/schema/catalog/head"
          ? {
              catalog_version: 0,
              schema_fingerprint: "",
              refreshed_at: "2026-07-10T00:00:00Z",
              object_count: 0,
              column_count: 0,
              change_token: 0,
              etag: "",
            }
          : path === "/api/schema/objects"
            ? { items: [], next_cursor: null, total: 0, catalog_version: 0 }
            : path === "/api/schema/catalog"
              ? { refreshed_at: "2026-07-10T00:00:00Z", tables: [] }
        : path === "/api/nl2sql/profiles/search"
          ? { items: [], next_cursor: null, total: 0, change_token: 0 }
        : path === "/api/nl2sql/profiles"
          ? []
          : path === "/api/security/users" || path === "/api/security/roles"
            ? []
          : path === "/api/nl2sql/history"
            ? { items: [] }
            : null;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data, error_messages: [], warning_messages: [] }),
    });
  });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("サイドバーを producer / consumer 思想のユーザー向け 5 セクションで表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });

  for (const section of ["データ準備", "AI 活用", "改善・運用", "セキュリティ管理", "システム設定"]) {
    await expect(sidebar.getByText(section, { exact: true })).toBeVisible();
  }

  const aiUseBox = await sidebar.getByText("AI 活用", { exact: true }).boundingBox();
  const dataPrepareBox = await sidebar.getByText("データ準備", { exact: true }).boundingBox();
  const securityBox = await sidebar.getByText("セキュリティ管理", { exact: true }).boundingBox();
  const settingsBox = await sidebar.getByText("システム設定", { exact: true }).boundingBox();
  if (!aiUseBox || !dataPrepareBox || !securityBox || !settingsBox) {
    throw new Error("セクション見出しの位置を取得できませんでした。");
  }
  expect(aiUseBox.y).toBeLessThan(dataPrepareBox.y);
  expect(securityBox.y).toBeLessThan(settingsBox.y);

  for (const label of [
    "管理 SQL を実行",
    "テーブルの管理",
    "ビューの管理",
    "データの管理",
    "コメント管理",
    "アノテーション管理",
    "検証用サンプルデータ",
    "業務プロファイル",
    "用語・同義語",
    "共通ルール",
  ]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  for (const label of ["SQL 生成", "SELECT SQL を実行", "SQL 確認・修復", "SQL から質問を生成", "実行履歴"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  const adminSqlBox = await sidebar.getByText("管理 SQL を実行", { exact: true }).boundingBox();
  const tableManagementBox = await sidebar.getByText("テーブルの管理", { exact: true }).boundingBox();
  if (!adminSqlBox || !tableManagementBox) {
    throw new Error("管理 SQL とテーブル管理メニューの位置を取得できませんでした。");
  }
  expect(adminSqlBox.y).toBeLessThan(tableManagementBox.y);

  for (const label of ["フィードバック管理", "質問分類モデル管理", "品質評価"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(sidebar.getByRole("link", { name: "安全境界" })).toHaveCount(0);
  await expect(sidebar.getByText("エンジン運用", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("モデル学習", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("フィードバック学習", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("質問学習", { exact: true })).toHaveCount(0);
  await expect(sidebar.getByText("接続診断", { exact: true })).toHaveCount(0);

  for (const label of ["OCI 認証", "アップロード保存先", "モデル", "データベース"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  const securityLabels = ["ユーザー管理", "ロール・権限管理", "Deep Data Security", "監査ログ"];
  const securityItemBoxes = await Promise.all(
    securityLabels.map(async (label) => {
      const item = sidebar.getByText(label, { exact: true });
      await expect(item).toBeVisible();
      return item.boundingBox();
    })
  );
  if (securityItemBoxes.some((box) => box === null)) {
    throw new Error("セキュリティ管理メニューの位置を取得できませんでした。");
  }
  for (let index = 1; index < securityItemBoxes.length; index += 1) {
    expect(securityItemBoxes[index - 1]!.y).toBeLessThan(securityItemBoxes[index]!.y);
  }
});

test("削除済みの安全境界 URL は専用 API を呼ばず、既存の全体 fallback へ移動する", async ({ page }) => {
  let diagnosticsRequests = 0;
  page.on("request", (request) => {
    const path = new URL(request.url()).pathname;
    if (path === "/api/nl2sql/diagnostics") diagnosticsRequests += 1;
  });

  await page.goto("/settings/nl2sql-database");

  await expect(page).toHaveURL(/\/query$/);
  await expect(page.getByRole("heading", { name: "NL2SQL 安全境界・Readiness" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "安全境界" })).toHaveCount(0);
  expect(diagnosticsRequests).toBe(0);
});

test("共通ルールは用語・同義語の直下に独立メニューとして並び、専用ページへ遷移する", async ({
  page,
}) => {
  await page.route("**/api/nl2sql/legacy-learning-material", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        data: { glossary: {}, rules: ["共通ルール", "SELECT/WITH のみ"] },
        error_messages: [],
        warning_messages: [],
      }),
    });
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const glossaryBox = await sidebar.getByText("用語・同義語", { exact: true }).boundingBox();
  const globalRulesBox = await sidebar.getByText("共通ルール", { exact: true }).boundingBox();
  if (!glossaryBox || !globalRulesBox) {
    throw new Error("メニュー項目の位置を取得できませんでした。");
  }
  // 用語・同義語 の「下」に並ぶ（= 中のタブではなく独立メニュー）。
  expect(globalRulesBox.y).toBeGreaterThan(glossaryBox.y);

  await sidebar.getByRole("link", { name: "共通ルール" }).click();
  await expect(page).toHaveURL(/\/global-rules$/);
  await expect(page.getByRole("heading", { name: "共通 SQL 生成ルール", level: 1 })).toBeVisible();
  await expect(page.getByRole("heading", { name: "共通 SQL 生成ルール", level: 2 })).toBeVisible();
});

test("セクション折りたたみで所属項目だけを隠し、他セクションは維持する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await sidebar.getByRole("button", { name: "データ準備 を折りたたむ" }).click();

  await expect(sidebar.getByText("テーブルの管理", { exact: true })).toBeHidden();
  await expect(sidebar.getByText("SQL 生成", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("フィードバック管理", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("質問分類モデル管理", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("OCI 認証", { exact: true })).toBeVisible();

  await sidebar.getByRole("button", { name: "セキュリティ管理 を折りたたむ" }).click();
  await expect(sidebar.getByText("ユーザー管理", { exact: true })).toBeHidden();
  await expect(sidebar.getByText("Deep Data Security", { exact: true })).toBeHidden();
  await expect(sidebar.getByText("OCI 認証", { exact: true })).toBeVisible();
});

test("アクティブ経路のセクションは保存済み折りたたみ状態から自動展開する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await sidebar.getByRole("button", { name: "セキュリティ管理 を折りたたむ" }).click();
  await expect(sidebar.getByText("ユーザー管理", { exact: true })).toBeHidden();

  await page.goto("/settings/security/users");
  await expect(sidebar.getByRole("link", { name: "ユーザー管理" })).toHaveAttribute("aria-current", "page");
  await expect(sidebar.getByText("Deep Data Security", { exact: true })).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "セキュリティ管理 を折りたたむ" })).toHaveAttribute(
    "aria-expanded",
    "true"
  );
});

test("セクション見出しはキーボードで開閉できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const toggle = sidebar.getByRole("button", { name: "セキュリティ管理 を折りたたむ" });
  await toggle.press("Enter");

  await expect(sidebar.getByText("ユーザー管理", { exact: true })).toBeHidden();
  await expect(sidebar.getByText("監査ログ", { exact: true })).toBeHidden();
  await expect(sidebar.getByRole("button", { name: "セキュリティ管理 を展開" })).toHaveAttribute(
    "aria-expanded",
    "false"
  );

  await sidebar.getByRole("button", { name: "セキュリティ管理 を展開" }).press(" ");
  await expect(sidebar.getByText("ユーザー管理", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("監査ログ", { exact: true })).toBeVisible();
});

test("375px 幅では icon-only ナビとして開閉ボタンなしで主要リンクへ到達できる", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });

  await expect(sidebar.getByRole("button", { name: "AI 活用 を折りたたむ" })).toHaveCount(0);
  await expect(sidebar.getByRole("link", { name: "SQL 生成" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "SQL から質問を生成" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "検証用サンプルデータ" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "品質評価" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "安全境界" })).toHaveCount(0);
  await expect(sidebar.getByRole("link", { name: "ユーザー管理" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "ロール・権限管理" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "Deep Data Security" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "監査ログ" })).toBeVisible();
  await expect(sidebar.getByRole("link", { name: "データベース設定" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth
    )
  ).toBe(true);
});
