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

test("サイドバーのセクション再編とラベルを確認", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto("/settings/retrieval");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const pipelineSection = sidebar.locator("#nav-section-nav-section-pipeline");

  // 新セクション見出し
  await expect(sidebar.getByText("検索・回答設定", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("システム設定", { exact: true })).toBeVisible();

  // 検索・回答設定のユーザー向けラベル（表示テキスト）
  for (const label of [
    "文書解析",
    "文書分割",
    "検索インデックス",
    "検索方法",
    "根拠確認",
    "回答スタイル",
    "回答プロンプト",
    "安全チェック",
    "品質評価",
  ]) {
    await expect(pipelineSection.getByText(label, { exact: true })).toBeVisible();
  }

  // システム設定の短縮ラベル
  for (const label of ["OCI 認証", "アップロード保存先", "モデル", "データベース"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  // ページタイトルもユーザー向けの業務語にする。
  await expect(page.getByRole("heading", { name: "検索方法" })).toBeVisible();
  // 検索・回答設定は「システム設定」とは別セクションへ移設されている。
  await expect(sidebar.getByText("設定", { exact: true })).toHaveCount(0);
});

test("セクション見出しクリックで配下項目を開閉できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  // システム設定が現在地。検索・回答設定は非アクティブなので折りたためる。
  await page.goto("/settings/oci");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const parserLink = sidebar.getByText("文書解析", { exact: true });
  await expect(parserLink).toBeVisible();

  // 折りたたむ → 配下が隠れる。
  const collapseToggle = sidebar.getByRole("button", { name: "検索・回答設定 を折りたたむ" });
  await expect(collapseToggle).toHaveAttribute("aria-expanded", "true");
  await collapseToggle.click();
  await expect(parserLink).toBeHidden();

  // もう一度クリックで展開 → 配下が戻る。
  const expandToggle = sidebar.getByRole("button", { name: "検索・回答設定 を展開" });
  await expect(expandToggle).toHaveAttribute("aria-expanded", "false");
  await expandToggle.click();
  await expect(parserLink).toBeVisible();
});

test("アクティブ経路のセクションは折りたたみ状態でも自動展開する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto("/settings/oci");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  // まず検索・回答設定を畳む（localStorage に折りたたみ状態を保持）。
  await sidebar.getByRole("button", { name: "検索・回答設定 を折りたたむ" }).click();
  await expect(sidebar.getByText("文書解析", { exact: true })).toBeHidden();

  // 検索・回答設定配下のページへ遷移すると、保存状態に関わらず自動展開して現在地を表示する。
  await page.goto("/settings/retrieval");
  await expect(sidebar.getByText("検索方法", { exact: true })).toBeVisible();
  await expect(
    sidebar.getByRole("button", { name: "検索・回答設定 を折りたたむ" })
  ).toHaveAttribute("aria-expanded", "true");
});
