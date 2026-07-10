import { expect, test, type Page } from "@playwright/test";

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const data =
      path === "/api/schema/catalog"
        ? { refreshed_at: "2026-07-10T00:00:00Z", tables: [] }
        : path === "/api/nl2sql/profiles"
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

test("サイドバーを producer / consumer 思想のユーザー向け 4 セクションで表示する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });

  for (const section of ["データ準備", "AI 活用", "改善・運用", "システム設定"]) {
    await expect(sidebar.getByText(section, { exact: true })).toBeVisible();
  }

  const aiUseBox = await sidebar.getByText("AI 活用", { exact: true }).boundingBox();
  const dataPrepareBox = await sidebar.getByText("データ準備", { exact: true }).boundingBox();
  if (!aiUseBox || !dataPrepareBox) {
    throw new Error("セクション見出しの位置を取得できませんでした。");
  }
  expect(aiUseBox.y).toBeLessThan(dataPrepareBox.y);

  for (const label of [
    "テーブルの管理",
    "ビューの管理",
    "データの管理",
    "コメント管理",
    "アノテーション管理",
    "検証用サンプルデータ",
    "業務プロファイル",
    "用語・ルール",
  ]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  for (const label of ["SQL 生成", "SQL 確認・修復", "SQL から質問を生成", "実行履歴"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  for (const label of ["フィードバック学習", "品質評価", "エンジン運用", "モデル学習", "データ運用"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(sidebar.getByText("接続診断", { exact: true })).toHaveCount(0);

  for (const label of ["OCI 認証", "アップロード保存先", "モデル", "データベース"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }
});

test("セクション折りたたみで所属項目だけを隠し、他セクションは維持する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await sidebar.getByRole("button", { name: "データ準備 を折りたたむ" }).click();

  await expect(sidebar.getByText("テーブルの管理", { exact: true })).toBeHidden();
  await expect(sidebar.getByText("SQL 生成", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("フィードバック学習", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("OCI 認証", { exact: true })).toBeVisible();
});

test("アクティブ経路のセクションは保存済み折りたたみ状態から自動展開する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await sidebar.getByRole("button", { name: "AI 活用 を折りたたむ" }).click();
  await expect(sidebar.getByText("SQL 生成", { exact: true })).toBeHidden();

  await page.goto("/sql-analysis");
  await expect(sidebar.getByText("SQL 確認・修復", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("SQL から質問を生成", { exact: true })).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "AI 活用 を折りたたむ" })).toHaveAttribute(
    "aria-expanded",
    "true"
  );
});

test("セクション見出しはキーボードで開閉できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const toggle = sidebar.getByRole("button", { name: "改善・運用 を折りたたむ" });
  await toggle.press("Enter");

  await expect(sidebar.getByText("フィードバック学習", { exact: true })).toBeHidden();
  await expect(sidebar.getByRole("button", { name: "改善・運用 を展開" })).toHaveAttribute(
    "aria-expanded",
    "false"
  );

  await sidebar.getByRole("button", { name: "改善・運用 を展開" }).press(" ");
  await expect(sidebar.getByText("フィードバック学習", { exact: true })).toBeVisible();
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
  await expect(sidebar.getByRole("link", { name: "データベース設定" })).toBeVisible();
});
