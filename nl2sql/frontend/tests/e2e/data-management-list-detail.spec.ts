import { expect, test, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { measureHorizontalOverflow } from "../../../../platform/packages/ui/e2e/layout-checks";

// データの管理の「テーブル・ビューデータの表示」の全幅の一覧 → 全幅の詳細（共有 ListDetailLayout の layout="single" /
// listVariant="table"。#2315・#4645）。幅に関わらず 1 ペイン（一覧 → 詳細 → 「一覧へ戻る」）。選択は URL の ?object= が持ち、
// 先頭を自動で選ばない。行を選ぶとその対象のデータを今の取得件数上限で表示し、詳細を開いている間は画面のタブを隠す。

const OBJECTS = Array.from({ length: 10 }, (_, index) => ({
  name: index < 8 ? `TABLE_${String(index + 1).padStart(2, "0")}` : `VIEW_${String(index - 7).padStart(2, "0")}`,
  owner: "APP",
  object_type: index < 8 ? "table" : "view",
  row_count: index < 8 ? (index + 1) * 10 : null,
  comment: index < 8 ? `合成のテーブル ${index + 1}` : `合成のビュー ${index - 7}`,
}));

// 列の多い結果（詳細の全幅を使うことを確かめる）。
const RESULT_COLUMNS = Array.from({ length: 14 }, (_, index) => `COLUMN_${String(index + 1).padStart(2, "0")}`);

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data }) });
}

async function mockDataManagement(page: Page) {
  const previewRequests: Array<{ name: string; limit: number }> = [];
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/synthetic-data/runs", (route) => fulfillJson(route, []));
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => {
    const url = new URL(route.request().url());
    const type = url.searchParams.get("type") ?? "all";
    const items = OBJECTS.filter((item) => type === "all" || item.object_type === type);
    return fulfillJson(route, {
      runtime: "oracle",
      owner: "APP",
      items,
      total: items.length,
      table_count: items.filter((item) => item.object_type === "table").length,
      view_count: items.filter((item) => item.object_type === "view").length,
      next_cursor: null,
      refreshed_at: "2026-10-18T09:00:00.000Z",
      catalog_version: 1,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/preview-data", (route) => {
    const body = route.request().postDataJSON() as { object_name: string; owner: string; limit: number };
    previewRequests.push({ name: `${body.owner}.${body.object_name}`, limit: body.limit });
    return fulfillJson(route, {
      runtime: "oracle",
      sql: `SELECT * FROM "${body.owner}"."${body.object_name}" FETCH FIRST ${body.limit} ROWS ONLY`,
      results: {
        columns: RESULT_COLUMNS,
        rows: Array.from({ length: 12 }, (_, row) =>
          Object.fromEntries(RESULT_COLUMNS.map((column, col) => [column, `値 ${row + 1}-${col + 1}`]))
        ),
        total: 12,
      },
      warnings: [],
    });
  });
  return { previewRequests };
}

// 表の高さに収まらない数の対象と続き（「さらに読み込む」）。
async function mockManyObjects(page: Page) {
  const items = Array.from({ length: 40 }, (_, index) => ({
    name: `TABLE_${String(index + 1).padStart(2, "0")}`,
    owner: "APP",
    object_type: "table",
    row_count: (index + 1) * 10,
    comment: `合成のテーブル ${index + 1}`,
  }));
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) =>
    fulfillJson(route, {
      runtime: "oracle",
      owner: "APP",
      items,
      total: 80,
      table_count: 80,
      view_count: 0,
      next_cursor: "cursor-2",
      refreshed_at: "2026-10-18T09:00:00.000Z",
      catalog_version: 1,
      warnings: [],
    })
  );
}

function rowButton(page: Page, name: string) {
  return page.getByRole("button", { name: `APP.${name} を選択` });
}

const isNarrow = (page: Page) => page.viewportSize()!.width < 768;

test.describe("データの管理のデータの表示の全幅の一覧 → 全幅の詳細（ListDetailLayout の single。#2315・#4645）", () => {
  test("全幅の一覧は対象名・種別・コメント・行数を出し、先頭を自動で選ばず、分割線・未選択の詳細・行の操作を持たない", { tag: "@responsive" }, async ({ page }, testInfo) => {
    const { previewRequests } = await mockDataManagement(page);
    await page.goto("/data-management");

    const layout = page.getByTestId("list-detail-data-management-preview");
    const list = page.getByTestId("list-detail-data-management-preview-list");
    const detail = page.getByTestId("list-detail-data-management-preview-detail");
    const grid = page.getByTestId("data-management-preview-grid");
    await expect(layout).toHaveAttribute("data-list-detail-layout", "single");
    await expect(rowButton(page, "TABLE_01")).toBeVisible();
    await expect(list).toBeVisible();
    await expect(detail).toBeHidden();
    await expect(page.getByTestId("list-detail-data-management-preview-resizer")).toHaveCount(0);
    await expect(page.getByTestId("list-detail-data-management-preview-toggle")).toHaveCount(0);
    await expect(page.getByTestId("data-management-detail-back")).toHaveCount(0);
    // 左右 1:1 の分割と、ステップの表示を持たず、未選択の詳細を見せない。
    await expect(page.getByTestId("fixed-split-pane-data-management-preview")).toHaveCount(0);
    await expect(page.getByTestId("data-preview-steps")).toHaveCount(0);
    await expect(page.getByText("対象が未選択です")).toBeHidden();
    // 先頭の行を自動で選ばない（データの取得を走らせない）。
    expect(previewRequests).toEqual([]);
    await expect(page).not.toHaveURL(/object=/);
    // 一覧の間は画面のタブを出す。
    await expect(page.getByRole("tab", { name: "テーブル・ビューデータの表示" })).toBeVisible();

    // 行の操作は持たない（データの表示は詳細で行う）。
    await expect(grid.locator('[data-testid^="data-management-preview-row-actions-"]')).toHaveCount(0);
    const viewRow = grid.locator("tbody tr").filter({ has: rowButton(page, "VIEW_01") });
    if (isNarrow(page)) {
      // md 未満は名前の列だけを残し、種別・コメント・行数を名前の下の 1 行に畳む。
      await expect(grid.getByRole("columnheader", { name: "種別" })).toBeHidden();
      await expect(viewRow.getByTestId("data-management-preview-row-summary")).toHaveText("ビュー · 合成のビュー 1 · 統計未取得");
      await expect(grid.locator("tbody tr").first().getByTestId("data-management-preview-row-summary")).toHaveText(
        "テーブル · 合成のテーブル 1 · 10 行"
      );
    } else {
      for (const header of ["対象名", "種別", "コメント", "行数"]) {
        await expect(grid.getByRole("columnheader", { name: header })).toBeVisible();
      }
      await expect(viewRow.getByTestId("data-management-preview-row-summary")).toBeHidden();
      await expect(viewRow.locator(":scope > :visible")).toHaveText([/APP\.VIEW_01/, "ビュー", "合成のビュー 1", "統計未取得"]);
      const [listBox, layoutBox] = await Promise.all([list.boundingBox(), layout.boundingBox()]);
      expect(Math.abs(listBox!.width - layoutBox!.width)).toBeLessThanOrEqual(1);
    }
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`data-management-list-${testInfo.project.name}.png`), fullPage: true });
  });

  test("名前で詳細を開くとデータを表示し、見出しは対象名・バッジ・コメント、タブと操作を隠し、「一覧へ戻る」でフォーカスを戻す", { tag: "@responsive" }, async ({ page }, testInfo) => {
    const { previewRequests } = await mockDataManagement(page);
    await page.goto("/data-management");
    const list = page.getByTestId("list-detail-data-management-preview-list");
    const detail = page.getByTestId("list-detail-data-management-preview-detail");
    const pageTab = page.getByRole("tab", { name: "テーブル・ビューデータの表示" });
    await expect(pageTab).toBeVisible();

    await rowButton(page, "TABLE_03").click();
    await expect(page).toHaveURL(/\?object=APP\.TABLE_03$/);
    await expect(list).toBeHidden();
    await expect(detail).toBeVisible();
    await expect(detail).toBeFocused();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_03");
    const badges = page.getByTestId("data-preview-header-badges");
    await expect(badges).toContainText("テーブル");
    await expect(badges).toContainText("30 行");
    await expect(page.locator("[data-page-header]")).toContainText("合成のテーブル 3");
    // 詳細を開いている間は画面のタブと PageHeader の操作を出さない。
    await expect(pageTab).toBeHidden();
    await expect(page.locator("[data-page-header-actions]")).toHaveCount(0);
    // 詳細の中の見える見出しは無く（sr-only）、操作の行の右端に前へ / 次へを置く。
    await expect(page.getByTestId("data-preview-detail").getByRole("heading", { level: 2 })).toHaveClass(/sr-only/);
    await expect(page.getByTestId("data-preview-results-actions")).toBeVisible();
    await expect(page.getByTestId("data-management-preview-detail-pager")).toBeVisible();
    const results = page.getByTestId("query-results");
    await expect(results.getByTestId("query-results-summary")).toBeVisible();
    expect(previewRequests).toEqual([{ name: "APP.TABLE_03", limit: 100 }]);
    // データの結果は詳細の全幅を使う。
    const [resultsBox, detailBox] = await Promise.all([results.boundingBox(), detail.boundingBox()]);
    expect(resultsBox!.width).toBeGreaterThan(detailBox!.width - 64);
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`data-management-detail-${testInfo.project.name}.png`), fullPage: true });

    const back = page.getByTestId("data-management-detail-back");
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).not.toHaveURL(/object=/);
    await expect(list).toBeVisible();
    await expect(detail).toBeHidden();
    await expect(pageTab).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "データの管理" })).toBeVisible();
    await expect(rowButton(page, "TABLE_03")).toBeFocused();

    // 「一覧へ戻る」で外した選択は、ほかのタブから戻っても URL に戻さない。
    await page.getByRole("tab", { name: "Excel/CSV アップロード(既存テーブル)" }).click();
    await pageTab.click();
    await expect(list).toBeVisible();
    await expect(page).not.toHaveURL(/object=/);

    // ブラウザの「戻る」でも一覧へ戻る（一覧から入るときは履歴に積む）。
    await rowButton(page, "TABLE_02").click();
    await expect(detail).toBeVisible();
    await page.goBack();
    await expect(list).toBeVisible();
    await expect(page).not.toHaveURL(/object=/);
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  });

  test("URL で詳細を開き、再読込でも同じ対象、前へ / 次へは今の取得件数上限でその対象のデータを表示する", { tag: "@responsive" }, async ({ page }) => {
    const { previewRequests } = await mockDataManagement(page);
    await page.goto("/data-management?object=APP.TABLE_02");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_02");
    await expect(page.getByTestId("query-results-summary")).toBeVisible();
    await expect(page.getByTestId("list-detail-data-management-preview-list")).toBeHidden();
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_02");
    await expect(page.getByTestId("query-results-summary")).toBeVisible();

    if (!isNarrow(page)) {
      // 前へ / 次へは操作の行の右端で、同じ行に並ぶ（上に別のツールバーの行を作らない）。
      const [actionsBox, pagerBox] = await Promise.all([
        page.getByTestId("data-preview-results-actions").boundingBox(),
        page.getByTestId("data-management-preview-detail-pager").boundingBox(),
      ]);
      expect(Math.abs(actionsBox!.y + actionsBox!.height / 2 - (pagerBox!.y + pagerBox!.height / 2))).toBeLessThanOrEqual(2);
      expect(pagerBox!.x).toBeGreaterThan(actionsBox!.x);
    }

    // 取得件数上限を変えてから次へ移ると、その上限で次の対象のデータを取る。
    const rowLimit = page.getByLabel("取得件数上限");
    await rowLimit.fill("25");
    const before = previewRequests.length;
    await page.getByTestId("data-management-preview-detail-pager-next").click();
    await expect(page).toHaveURL(/\?object=APP\.TABLE_03$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_03");
    await expect.poll(() => previewRequests.slice(before)).toEqual([{ name: "APP.TABLE_03", limit: 25 }]);
    await expect(page.getByTestId("query-results-summary")).toBeVisible();
    await page.getByTestId("data-management-preview-detail-pager-previous").click();
    await expect(page).toHaveURL(/\?object=APP\.TABLE_02$/);
    await expect(page.getByTestId("query-results-summary")).toBeVisible();
    await expect.poll(() => previewRequests.at(-1)).toEqual({ name: "APP.TABLE_02", limit: 25 });

    await page.getByTestId("data-management-detail-back").click();
    await expect(rowButton(page, "TABLE_02")).toBeFocused();
  });

  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 1920, height: 1000 },
  ]) {
    test(`${viewport.width}px: 全幅の一覧は表の中でスクロールし、「さらに読み込む」と詳細の結果が横にあふれない`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "1280px / 1920px は desktop で確かめる");
      await page.setViewportSize(viewport);
      await mockDataManagement(page);
      await mockManyObjects(page);
      await page.goto("/data-management");
      await expect(rowButton(page, "TABLE_01")).toBeVisible();
      const layout = page.getByTestId("list-detail-data-management-preview");
      const [listBox, layoutBox, title] = await Promise.all([
        page.getByTestId("list-detail-data-management-preview-list").boundingBox(),
        layout.boundingBox(),
        page.getByRole("heading", { level: 1, name: "データの管理" }).boundingBox(),
      ]);
      expect(Math.abs(listBox!.width - layoutBox!.width)).toBeLessThanOrEqual(1);
      expect(Math.abs(listBox!.x - title!.x)).toBeLessThanOrEqual(1);
      const scroll = page.getByTestId("db-admin-object-list");
      expect(await scroll.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);
      await expect(page.getByTestId("data-management-preview-footer").getByRole("button", { name: "さらに読み込む" })).toBeVisible();
      expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: testInfo.outputPath(`data-management-list-${viewport.width}.png`) });

      await rowButton(page, "TABLE_02").click();
      await expect(page.getByTestId("query-results-summary")).toBeVisible();
      const detailBox = await page.getByTestId("list-detail-data-management-preview-detail").boundingBox();
      expect(Math.abs(detailBox!.width - layoutBox!.width)).toBeLessThanOrEqual(1);
      expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: testInfo.outputPath(`data-management-detail-${viewport.width}.png`), fullPage: true });
    });
  }
});
