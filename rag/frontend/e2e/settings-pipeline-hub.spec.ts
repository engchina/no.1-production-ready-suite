import { expect, test } from "@playwright/test";
import { expectNoPageOverflow } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("検索・回答設定の概要ハブが工程をフェーズ別カードで俯瞰し各設定へ導線を出す", async ({ page }) => {
  await page.goto("/settings/pipeline");

  await expect(page.getByRole("heading", { name: "設定の概要" })).toBeVisible();

  // 2 フェーズの見出し。
  await expect(page.getByRole("region", { name: "ナレッジ構築" })).toBeVisible();
  await expect(page.getByRole("region", { name: "検索・回答" })).toBeVisible();

  // 取込フェーズの工程カードは構築側設定へ、検索フェーズは検索側設定へ遷移する。
  await expect(page.getByRole("link", { name: "文書分割 の設定を開く" })).toHaveAttribute(
    "href",
    "/settings/chunking"
  );
  await expect(page.getByRole("link", { name: "検索方法 の設定を開く" })).toHaveAttribute(
    "href",
    "/settings/retrieval"
  );

  // カードから実際に詳細設定へ遷移できる。
  await page.getByRole("link", { name: "文書分割 の設定を開く" }).click();
  await expect(page).toHaveURL(/\/settings\/chunking$/);

  await expectNoPageOverflow(page);
});

test("サイドバーの概要リンクからハブへ到達できる", async ({ page }) => {
  await page.goto("/settings/pipeline");
  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  // 現在地がハブなので「検索・回答設定」セクションは自動展開し、概要リンクが見える。
  await expect(sidebar.getByRole("link", { name: "概要" })).toBeVisible();
  await expectNoPageOverflow(page);
});
