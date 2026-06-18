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

  // 新セクション見出し
  await expect(sidebar.getByText("RAG パイプライン", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("システム設定", { exact: true })).toBeVisible();

  // パイプラインの日本語+英語併記ラベル（表示テキスト）
  for (const label of [
    "解析 (Parser)",
    "分割 (Chunking)",
    "索引 (Vector Index)",
    "検索 (Retrieval)",
    "後処理 (Grounding)",
    "生成 (Generation)",
    "ガードレール (Guardrail)",
    "評価 (Evaluation)",
  ]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  // システム設定の短縮ラベル
  for (const label of ["OCI 認証", "アップロード保存先", "モデル", "データベース"]) {
    await expect(sidebar.getByText(label, { exact: true })).toBeVisible();
  }

  // ページタイトルは正式名（AGENTS.md 準拠）を維持する。
  await expect(page.getByRole("heading", { name: "Retrieval アダプター" })).toBeVisible();
  // 8 アダプターは「設定」ではなく「RAG パイプライン」へ移設されている。
  await expect(sidebar.getByText("設定", { exact: true })).toHaveCount(0);
});
