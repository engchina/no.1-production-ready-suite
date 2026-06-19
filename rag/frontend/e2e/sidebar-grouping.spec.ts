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

test("セクション見出しクリックで配下項目を開閉できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  // システム設定が現在地。RAG パイプラインは非アクティブなので折りたためる。
  await page.goto("/settings/oci");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  const parserLink = sidebar.getByText("解析 (Parser)", { exact: true });
  await expect(parserLink).toBeVisible();

  // 折りたたむ → 配下が隠れる。
  const collapseToggle = sidebar.getByRole("button", { name: "RAG パイプライン を折りたたむ" });
  await expect(collapseToggle).toHaveAttribute("aria-expanded", "true");
  await collapseToggle.click();
  await expect(parserLink).toBeHidden();

  // もう一度クリックで展開 → 配下が戻る。
  const expandToggle = sidebar.getByRole("button", { name: "RAG パイプライン を展開" });
  await expect(expandToggle).toHaveAttribute("aria-expanded", "false");
  await expandToggle.click();
  await expect(parserLink).toBeVisible();
});

test("アクティブ経路のセクションは折りたたみ状態でも自動展開する", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockApi(page);
  await page.goto("/settings/oci");

  const sidebar = page.getByRole("complementary", { name: "サイドナビゲーション" });
  // まず RAG パイプラインを畳む（localStorage に折りたたみ状態を保持）。
  await sidebar.getByRole("button", { name: "RAG パイプライン を折りたたむ" }).click();
  await expect(sidebar.getByText("解析 (Parser)", { exact: true })).toBeHidden();

  // パイプライン配下のページへ遷移すると、保存状態に関わらず自動展開して現在地を表示する。
  await page.goto("/settings/retrieval");
  await expect(sidebar.getByText("検索 (Retrieval)", { exact: true })).toBeVisible();
  await expect(
    sidebar.getByRole("button", { name: "RAG パイプライン を折りたたむ" })
  ).toHaveAttribute("aria-expanded", "true");
});
