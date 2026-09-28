import { expect, test } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
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
  // 関係情報の工程は取込時の構築の設定なので「関係情報の構築」の名前でナレッジ構築フェーズに並ぶ(#301)。
  await expect(
    page
      .getByRole("region", { name: "ナレッジ構築" })
      .getByRole("link", { name: "関係情報の構築 の設定を開く" })
  ).toHaveAttribute("href", "/settings/graph");

  // カードから実際に詳細設定へ遷移できる。
  await page.getByRole("link", { name: "文書分割 の設定を開く" }).click();
  await expect(page).toHaveURL(/\/settings\/chunking$/);

  await expectNoPageOverflow(page);
});

test("サイドバーの設定の概要リンクからハブへ到達できる", async ({ page }) => {
  await page.goto("/settings/pipeline");
  const sidebar = await openSidebarNav(page);
  // 現在地がハブなので「検索・回答設定」セクションは自動展開し、画面のタイトルと同じ名前のリンクが見える（#267）。
  await expect(sidebar.getByRole("link", { name: "設定の概要" })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("サイドバーの検索・回答設定の名前と順番は設定の概要の工程と同じ（#267）", async ({ page }) => {
  await page.goto("/settings/pipeline");
  await expect(page.getByRole("heading", { name: "設定の概要" })).toBeVisible();
  // 概要の工程カード（1. 〜 12.）の名前を順番どおりに読む。
  const cardNames = await page
    .getByRole("link", { name: / の設定を開く$/ })
    .evaluateAll((links) => links.map((link) => (link.getAttribute("aria-label") ?? "").replace(/ の設定を開く$/, "")));
  expect(cardNames.length).toBeGreaterThan(0);
  const pipelineHrefs = await page
    .getByRole("link", { name: / の設定を開く$/ })
    .evaluateAll((links) => links.map((link) => link.getAttribute("href")));
  // サイドバーの同じ工程のリンクを、サイドバー上の並び順で読む（先頭の「設定の概要」は除く）。
  const sidebar = await openSidebarNav(page);
  const sidebarNames = await sidebar.locator("a").evaluateAll(
    (links, hrefs) =>
      links
        .filter((link) => hrefs.includes(link.getAttribute("href")))
        .map((link) => (link.textContent ?? "").trim()),
    pipelineHrefs
  );
  expect(sidebarNames).toEqual(cardNames);
});
