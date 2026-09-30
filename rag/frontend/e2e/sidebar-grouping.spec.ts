import { expect, test, type Page } from "@playwright/test";
import { DB_STATUS_OK, LOCAL_AUTH_ME } from "./_helpers";

async function mockApi(page: Page) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") {
      await route.fulfill({ json: LOCAL_AUTH_ME });
      return;
    }
    // 検索・回答設定は DB ゲートを通す（#325）。
    if (url.pathname === "/api/ready/database") {
      await route.fulfill({ json: DB_STATUS_OK });
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
    "回答プロンプト",
    "安全チェック",
    "評価の基準",
  ]) {
    await expect(pipelineSection.getByText(label, { exact: true })).toBeVisible();
  }
  // 根拠確認・回答スタイル・高度な検索の画面は削除した（#595）。
  for (const removed of ["根拠確認", "回答スタイル", "高度な検索"]) {
    await expect(pipelineSection.getByText(removed, { exact: true })).toHaveCount(0);
  }

  // セクションは 3 製品で同じ並び方で「… → 検索・回答設定 → 改善・運用 → セキュリティ設定 →
  // ユーザーとロール → 運用設定 → システム設定」の順に並ぶ（#80 / #214 / #658）。
  const sectionIds = await sidebar
    .locator('[id^="nav-section-nav-section-"]')
    .evaluateAll((elements) => elements.map((element) => element.id));
  // 「改善・運用」（品質評価・フィードバック）は検索・回答設定の後、セキュリティ設定の前（#409）。
  expect(sectionIds.slice(-6)).toEqual([
    "nav-section-nav-section-pipeline",
    "nav-section-nav-section-improve",
    "nav-section-nav-section-security",
    "nav-section-nav-section-userRoles",
    "nav-section-nav-section-operations",
    "nav-section-nav-section-settings",
  ]);

  // セキュリティ設定は権限管理だけ、ユーザーとロールは3製品共通の2項目を持つ。
  await expect(sidebar.getByText("セキュリティ設定", { exact: true })).toBeVisible();
  await expect(sidebar.locator("#nav-section-nav-section-security").getByRole("link")).toHaveCount(1);
  const userRolesSection = sidebar.locator("#nav-section-nav-section-userRoles");
  await expect(userRolesSection.getByRole("link")).toHaveCount(2);
  for (const label of ["ユーザー管理", "ロール管理"]) {
    await expect(userRolesSection.getByText(label, { exact: true })).toBeVisible();
  }

  // 運用設定は RAG 固有の項目だけを持ち、NL2SQL と同じくシステムテーブルが先頭（#658）。
  const operationsSection = sidebar.locator("#nav-section-nav-section-operations");
  await expect(sidebar.getByText("運用設定", { exact: true })).toBeVisible();
  await expect(operationsSection.getByRole("link")).toHaveCount(3);
  await expect(operationsSection.getByRole("link").first()).toHaveAccessibleName(/システムテーブル/);
  await expect(operationsSection.getByRole("link", { name: /HuggingFace/ })).toBeVisible();
  await expect(operationsSection.getByRole("link", { name: /サービス管理/ })).toBeVisible();

  // システム設定は3製品で共通の5項目だけを持つ。
  const settingsSection = sidebar.locator("#nav-section-nav-section-settings");
  await expect(settingsSection.getByRole("link")).toHaveCount(5);
  for (const label of ["OCI 認証", "アップロード保存先", "モデル", "データベース", "外観"]) {
    await expect(settingsSection.getByText(label, { exact: true })).toBeVisible();
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
