import { expect, test, type Page } from "@playwright/test";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: authStatus });
      return;
    }
    await route.fulfill({ json: { data: null, error_messages: [], warning_messages: [] } });
  });
}

const modifier = process.platform === "darwin" ? "Meta" : "Control";

test("Cmd/Ctrl+K で開き、絞り込み → Enter で対象ページへ遷移する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto("/dashboard");
  // アプリ（CommandPalette の keydown リスナー）が mount 済みになるまで待つ。
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toBeVisible();

  await page.keyboard.press(`${modifier}+KeyK`);

  const dialog = page.getByRole("dialog", { name: "ページへ移動" });
  await expect(dialog).toBeVisible();

  const input = dialog.getByRole("combobox");
  await input.fill("Retrieval");

  // 絞り込み結果に Retrieval アダプターが含まれる。
  await expect(dialog.getByRole("option", { name: /Retrieval アダプター/ })).toBeVisible();

  await page.keyboard.press("Enter");

  // 遷移してパレットが閉じる。
  await expect(page).toHaveURL(/\/settings\/retrieval$/);
  await expect(dialog).toBeHidden();
});

test("一致なしの空状態を表示し、Esc で閉じる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toBeVisible();

  await page.keyboard.press(`${modifier}+KeyK`);
  const dialog = page.getByRole("dialog", { name: "ページへ移動" });
  await dialog.getByRole("combobox").fill("存在しないページ名zzz");
  await expect(dialog.getByText("一致するページがありません。")).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("サイドバーの検索トリガーから開ける（375px・タッチ導線）", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await mockApi(page);
  await page.goto("/dashboard");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  await sidebar.getByRole("button", { name: "コマンドパレットを開く" }).click();

  const dialog = page.getByRole("dialog", { name: "ページへ移動" });
  await expect(dialog).toBeVisible();

  // クリックでも遷移できる。
  await dialog.getByRole("combobox").fill("評価");
  await dialog.getByRole("option", { name: /RAG 評価/ }).click();
  await expect(page).toHaveURL(/\/evaluation$/);
});

test("クリアボタンで入力をリセットし、件数フッターを更新する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto("/dashboard");
  await expect(page.getByRole("complementary", { name: "サイドナビゲーション" })).toBeVisible();

  await page.keyboard.press(`${modifier}+KeyK`);
  const dialog = page.getByRole("dialog", { name: "ページへ移動" });
  const input = dialog.getByRole("combobox");

  // 入力前はクリアボタンなし。絞り込むと出現し、件数フッターが反映される。
  await expect(dialog.getByRole("button", { name: "検索をクリア" })).toHaveCount(0);
  await input.fill("Retrieval");
  await expect(dialog.getByText("1 件", { exact: true })).toBeVisible();

  // クリアで空に戻り、全件表示・フォーカスは入力へ。
  await dialog.getByRole("button", { name: "検索をクリア" }).click();
  await expect(input).toHaveValue("");
  await expect(input).toBeFocused();
  // 全件 = NAV_SECTIONS の総項目数(取込4+RAG4+パイプライン12+設定5=25)。
  await expect(dialog.getByText("25 件", { exact: true })).toBeVisible();
});
