import { expect, test, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { measureHorizontalOverflow } from "../../../../platform/packages/ui/e2e/layout-checks";

// テーブルの管理・ビューの管理の全幅の一覧 → 全幅の詳細（共有 ListDetailLayout の layout="single" / listVariant="table"。
// #2281・#4645）。幅に関わらず 1 ペイン（一覧 → 詳細 → 「一覧へ戻る」）で、分割線・開閉・未選択の詳細を持たない。
// 選択は URL が持ち、先頭を自動で選ばない。詳細の見出しは PageHeader の対象名。

const TABLES = Array.from({ length: 12 }, (_, index) => ({
  name: `TABLE_${String(index + 1).padStart(2, "0")}`,
  owner: "APP",
  qualified_name: `APP.TABLE_${String(index + 1).padStart(2, "0")}`,
  object_type: "table",
  row_count: (index + 1) * 10,
  comment: `合成のテーブル ${index + 1}`,
}));

const VIEWS = Array.from({ length: 4 }, (_, index) => ({
  name: `VIEW_${String(index + 1).padStart(2, "0")}`,
  owner: "APP",
  qualified_name: `APP.VIEW_${String(index + 1).padStart(2, "0")}`,
  object_type: "view",
  row_count: null,
  comment: `合成のビュー ${index + 1}`,
}));

const COLUMN_TYPES = ["NUMBER", "VARCHAR2(200)", "DATE", "TIMESTAMP(6)", "VARCHAR2(4000)", "NUMBER(10,2)"];

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data }) });
}

function objectPage(items: unknown[], tableCount: number, viewCount: number) {
  return {
    runtime: "oracle",
    owner: "APP",
    items,
    total: items.length,
    table_count: tableCount,
    view_count: viewCount,
    next_cursor: null,
    refreshed_at: "2026-10-18T09:00:00.000Z",
    catalog_version: 1,
    warnings: [],
  };
}

function detailColumns(columnCount?: number) {
  const types = columnCount
    ? Array.from({ length: columnCount }, (_, index) => COLUMN_TYPES[index % COLUMN_TYPES.length])
    : COLUMN_TYPES;
  return types.map((dataType, index) => ({
    column_name: `COLUMN_${index + 1}`,
    logical_name: `列 ${index + 1}`,
    data_type: dataType,
    nullable: index % 2 === 0,
    comment: `合成の列のコメント ${index + 1}`,
    sample_values: [`値 ${index + 1}-a`, `値 ${index + 1}-b`],
  }));
}

async function mockTableManagement(page: Page, options: { tableCount?: number; columnCount?: number } = {}) {
  const tables = options.tableCount
    ? Array.from({ length: options.tableCount }, (_, index) => ({
        ...TABLES[0],
        name: `TABLE_${String(index + 1).padStart(2, "0")}`,
        qualified_name: `APP.TABLE_${String(index + 1).padStart(2, "0")}`,
        row_count: (index + 1) * 10,
        comment: `合成のテーブル ${index + 1}`,
      }))
    : TABLES;
  const detailRequests: string[] = [];
  const exactCountRequests: string[] = [];
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/synthetic-data/runs", (route) => fulfillJson(route, []));
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => fulfillJson(route, objectPage(tables, tables.length, 0)));
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) => {
    const url = new URL(route.request().url());
    const name = decodeURIComponent(url.pathname.split("/").pop() ?? "");
    const table = tables.find((item) => item.name === name) ?? tables[0];
    const exact = url.searchParams.get("exact_count") === "1";
    if (exact) exactCountRequests.push(name);
    else detailRequests.push(name);
    return fulfillJson(route, {
      ...table,
      row_count: exact ? 12345 : table.row_count,
      columns: detailColumns(options.columnCount),
      ddl: `CREATE TABLE "APP"."${table.name}" ("COLUMN_1" NUMBER)`,
      warnings: [],
    });
  });
  return { detailRequests, exactCountRequests };
}

async function mockViewManagement(page: Page) {
  const extractRequests: string[] = [];
  await mockDatabaseGateReady(page);
  await page.route("**/api/nl2sql/synthetic-data/runs", (route) => fulfillJson(route, []));
  await page.route("**/api/nl2sql/db-admin/objects?*", (route) => fulfillJson(route, objectPage(VIEWS, 0, VIEWS.length)));
  await page.route("**/api/nl2sql/db-admin/views/*", (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "");
    const view = VIEWS.find((item) => item.name === name) ?? VIEWS[0];
    return fulfillJson(route, {
      ...view,
      columns: detailColumns(3),
      ddl: `CREATE OR REPLACE VIEW "APP"."${view.name}" AS SELECT a.ID FROM APP.T_A a JOIN APP.T_B b ON a.ID = b.ID WHERE a.FLAG = 1`,
      warnings: [],
    });
  });
  await page.route("**/api/nl2sql/db-admin/extract-join-where", (route) => {
    extractRequests.push((route.request().postDataJSON() as { ddl: string }).ddl);
    return fulfillJson(route, {
      join_text: "a.ID = b.ID",
      where_text: "a.FLAG = 1",
      source: "deterministic",
      warnings: [],
      prompt_profile: "sql_structure",
    });
  });
  return { extractRequests };
}

function rowButton(page: Page, name: string) {
  return page.getByRole("button", { name: `APP.${name} を表示` });
}

const isNarrow = (page: Page) => page.viewportSize()!.width < 768;

test.describe("テーブルの管理の全幅の一覧 → 全幅の詳細（ListDetailLayout の single。#2281・#4645）", () => {
  test("全幅の一覧は名前・コメント・行数・行の操作を出し、先頭を自動で選ばず、分割線・開閉・未選択の詳細を持たない", { tag: "@responsive" }, async ({ page }, testInfo) => {
    const { detailRequests } = await mockTableManagement(page);
    await page.goto("/table-management");

    const layout = page.getByTestId("list-detail-table-management");
    const list = page.getByTestId("list-detail-table-management-list");
    const detail = page.getByTestId("list-detail-table-management-detail");
    const grid = page.getByTestId("table-management-grid");
    await expect(layout).toHaveAttribute("data-list-detail-layout", "single");
    await expect(rowButton(page, "TABLE_01")).toBeVisible();
    await expect(grid.locator("tbody tr")).toHaveCount(12);
    await expect(list).toBeVisible();
    await expect(detail).toBeHidden();
    await expect(page.getByTestId("list-detail-table-management-resizer")).toHaveCount(0);
    await expect(page.getByTestId("list-detail-table-management-toggle")).toHaveCount(0);
    await expect(page.getByTestId("table-management-detail-back")).toHaveCount(0);
    await expect(page.getByText("オブジェクトが未選択です")).toBeHidden();
    // 先頭の行を自動で選ばない（詳細の取得を走らせない）。
    expect(detailRequests).toEqual([]);
    await expect(page).not.toHaveURL(/table=/);
    await expect(page.getByRole("heading", { level: 1, name: "テーブルの管理" })).toBeVisible();

    // 名前は行の見出し（rowheader）。行の操作のメニューは名前ごとにある。
    await expect(grid.getByRole("rowheader").filter({ has: rowButton(page, "TABLE_01") })).toHaveCount(1);
    await expect(page.getByTestId("table-management-row-actions-APP.TABLE_01-trigger")).toBeVisible();
    const firstRow = grid.locator("tbody tr").first();
    if (isNarrow(page)) {
      // md 未満は名前の列だけを残し、コメント・行数を名前の下の 1 行に畳む。
      await expect(grid.getByRole("columnheader", { name: "コメント" })).toBeHidden();
      await expect(grid.getByRole("columnheader", { name: "行数" })).toBeHidden();
      const summary = firstRow.getByTestId("table-management-row-summary");
      await expect(summary).toBeVisible();
      await expect(summary).toHaveText("合成のテーブル 1 · 10 行");
    } else {
      await expect(grid.getByRole("columnheader", { name: "テーブル名" })).toBeVisible();
      await expect(grid.getByRole("columnheader", { name: "コメント" })).toBeVisible();
      await expect(grid.getByRole("columnheader", { name: "行数" })).toBeVisible();
      await expect(firstRow.getByTestId("table-management-row-summary")).toBeHidden();
      await expect(firstRow.locator(":scope > :visible")).toHaveText([/APP\.TABLE_01/, "合成のテーブル 1", "10 行", ""]);
      // 一覧は全幅（狭い列ではない）。
      const [listBox, layoutBox] = await Promise.all([list.boundingBox(), layout.boundingBox()]);
      expect(Math.abs(listBox!.width - layoutBox!.width)).toBeLessThanOrEqual(1);
    }
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`table-management-list-${testInfo.project.name}.png`), fullPage: true });
  });

  test("名前で詳細を開くと見出しは対象名・バッジ・コメントになり、作成などの操作を隠し、「一覧へ戻る」でスクロールとフォーカスを戻す", { tag: "@responsive" }, async ({ page }, testInfo) => {
    await mockTableManagement(page, { tableCount: 40 });
    await page.goto("/table-management");
    const list = page.getByTestId("list-detail-table-management-list");
    const detail = page.getByTestId("list-detail-table-management-detail");
    const actions = page.getByTestId("table-management-actions");
    await expect(actions).toBeVisible();

    // 一覧の下の方の行（表の中と本文をスクロールした位置）から開く。
    const target = rowButton(page, "TABLE_30");
    await target.scrollIntoViewIfNeeded();
    await target.click();
    await expect(page).toHaveURL(/\?table=APP\.TABLE_30$/);
    await expect(list).toBeHidden();
    await expect(detail).toBeVisible();
    await expect(detail).toBeFocused();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_30");
    const badges = page.getByTestId("db-object-header-badges");
    await expect(badges).toContainText("table");
    await expect(badges).toContainText("6 列");
    await expect(badges).toContainText("300 行");
    await expect(page.locator("[data-page-header]")).toContainText("合成のテーブル 30");
    // 詳細を開いている間は、作成・取込・再取得の操作を出さない。
    await expect(actions).toHaveCount(0);
    await expect(page.getByRole("button", { name: "テーブル作成" })).toHaveCount(0);
    // 詳細の中の見える見出しは無く（sr-only）、操作の行の右端に前へ / 次へを置く。
    await expect(page.getByTestId("table-management-detail").getByRole("heading", { level: 2 })).toHaveClass(/sr-only/);
    await expect(page.getByTestId("table-management-detail-actions")).toBeVisible();
    await expect(page.getByTestId("table-management-detail-pager")).toBeVisible();
    await expect(detail.getByRole("tab", { name: "列情報" })).toHaveAttribute("aria-selected", "true");
    await expect(detail.getByRole("tab", { name: "DDL" })).toBeVisible();
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`table-management-detail-${testInfo.project.name}.png`), fullPage: true });

    const back = page.getByTestId("table-management-detail-back");
    await expect(back).toBeVisible();
    await back.click();
    await expect(page).not.toHaveURL(/table=/);
    await expect(list).toBeVisible();
    await expect(detail).toBeHidden();
    await expect(page.getByRole("heading", { level: 1, name: "テーブルの管理" })).toBeVisible();
    await expect(actions).toBeVisible();
    // 開いた行へフォーカスとスクロールを戻す。
    await expect(target).toBeFocused();
    await expect(target).toBeInViewport();

    // ブラウザの「戻る」でも一覧へ戻る（一覧から入るときは履歴に積む）。
    await rowButton(page, "TABLE_02").click();
    await expect(detail).toBeVisible();
    await page.goBack();
    await expect(list).toBeVisible();
    await expect(page).not.toHaveURL(/table=/);
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  });

  test("URL で詳細を開き、再読込でも同じ対象、前へ / 次へで一覧の並びの前後へ移り、戻ると最後の行へフォーカスする", { tag: "@responsive" }, async ({ page }) => {
    await mockTableManagement(page);
    await page.goto("/table-management?table=APP.TABLE_05");
    const detail = page.getByTestId("list-detail-table-management-detail");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_05");
    await expect(page.getByTestId("list-detail-table-management-list")).toBeHidden();
    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_05");
    await expect(page.getByTestId("db-object-header-badges")).toContainText("50 行");

    if (!isNarrow(page)) {
      // 前へ / 次へは操作の行（ObjectActionBar）の右端で、同じ行に並ぶ。
      const [actionsBox, pagerBox, detailBox] = await Promise.all([
        page.getByTestId("table-management-detail-actions").boundingBox(),
        page.getByTestId("table-management-detail-pager").boundingBox(),
        detail.boundingBox(),
      ]);
      expect(Math.abs(actionsBox!.y + actionsBox!.height / 2 - (pagerBox!.y + pagerBox!.height / 2))).toBeLessThanOrEqual(2);
      expect(pagerBox!.x).toBeGreaterThan(actionsBox!.x);
      expect(detailBox!.x + detailBox!.width - (pagerBox!.x + pagerBox!.width)).toBeLessThanOrEqual(24);
    }

    await page.getByTestId("table-management-detail-pager-next").click();
    await expect(page).toHaveURL(/\?table=APP\.TABLE_06$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_06");
    await expect(page.getByTestId("db-object-header-badges")).toContainText("60 行");
    await page.getByTestId("table-management-detail-pager-previous").click();
    await expect(page).toHaveURL(/\?table=APP\.TABLE_05$/);
    await expect(page.getByTestId("db-object-header-badges")).toContainText("50 行");
    await page.getByTestId("table-management-detail-pager-next").click();
    await expect(page.getByTestId("db-object-header-badges")).toContainText("60 行");

    await page.getByTestId("table-management-detail-back").click();
    await expect(page).not.toHaveURL(/table=/);
    await expect(rowButton(page, "TABLE_06")).toBeFocused();

    // 先頭の行では「前へ」を押せない。
    await rowButton(page, "TABLE_01").click();
    await expect(page.getByTestId("table-management-detail-pager-previous")).toBeDisabled();
    await expect(page.getByTestId("table-management-detail-pager-next")).toBeEnabled();
  });

  test("前へ / 次へは詳細の読み込み中も同じ位置に残り、続けて押すとその数だけ移る", { tag: "@responsive" }, async ({ page }) => {
    await mockTableManagement(page);
    // 詳細の応答を遅らせ、読み込み中の前へ / 次への位置を測る（後から登録した route が先に当たる）。
    await page.route("**/api/nl2sql/db-admin/tables/*", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 600));
      await route.fallback();
    });
    await page.goto("/table-management?table=APP.TABLE_05");
    await expect(page.getByTestId("db-object-header-badges")).toContainText("50 行");
    const pager = page.getByTestId("table-management-detail-pager");
    const loadedBox = await pager.boundingBox();

    await page.getByTestId("table-management-detail-pager-next").click();
    await expect(page).toHaveURL(/\?table=APP\.TABLE_06$/);
    // 読み込み中（操作の行がまだ無い）でも、前へ / 次へは読み込んだ後と同じ位置。
    await expect(page.getByTestId("table-management-detail-actions")).toHaveCount(0);
    const loadingBox = await pager.boundingBox();
    expect(Math.abs(loadingBox!.y - loadedBox!.y)).toBeLessThanOrEqual(1);
    expect(Math.abs(loadingBox!.x - loadedBox!.x)).toBeLessThanOrEqual(1);

    // 読み終わりを待たずに続けて押すと、押した数だけ移る（ほかの操作を押さない）。
    await page.getByTestId("table-management-detail-pager-next").click();
    await expect(page).toHaveURL(/\?table=APP\.TABLE_07$/);
    await expect(page.getByTestId("db-object-header-badges")).toContainText("70 行");
    const settledBox = await pager.boundingBox();
    expect(Math.abs(settledBox!.y - loadedBox!.y)).toBeLessThanOrEqual(1);
  });

  test("行の操作のメニューは詳細と同じ操作を出し、「正確な件数を取得」はその詳細を開いて数える", async ({ page }) => {
    const { exactCountRequests } = await mockTableManagement(page);
    await page.goto("/table-management");
    await page.getByTestId("table-management-row-actions-APP.TABLE_03-trigger").click();
    await expect(page.getByRole("menuitem")).toHaveText(["正確な件数を取得", "XLSX ダウンロード", "削除"]);
    await page.getByRole("menuitem", { name: "COUNT(*) で正確な行数を取得" }).click();
    await expect(page).toHaveURL(/\?table=APP\.TABLE_03$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_03");
    await expect.poll(() => exactCountRequests).toEqual(["TABLE_03"]);
    await expect(page.getByTestId("db-object-header-badges")).toContainText("12345 行");

    await page.getByTestId("table-management-detail-back").click();
    await page.getByTestId("table-management-row-actions-APP.TABLE_04-trigger").click();
    await page.getByRole("menuitem", { name: "削除" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("APP.TABLE_04");
    // 削除の確認は一覧のまま開く（詳細へ移らない）。
    await expect(page).not.toHaveURL(/table=/);
  });

  test("↑ / ↓ で一覧の行の間を移り、Enter で開く", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "desktop", "キーボードの移動は desktop で確かめる");
    await mockTableManagement(page);
    await page.goto("/table-management");
    await rowButton(page, "TABLE_01").focus();
    await page.keyboard.press("ArrowDown");
    await expect(rowButton(page, "TABLE_02")).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(rowButton(page, "TABLE_03")).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(rowButton(page, "TABLE_02")).toBeFocused();
    await page.keyboard.press("End");
    await expect(rowButton(page, "TABLE_12")).toBeFocused();
    await page.keyboard.press("Home");
    await expect(rowButton(page, "TABLE_01")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\?table=APP\.TABLE_01$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_01");
  });

  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 1920, height: 1000 },
  ]) {
    test(`${viewport.width}px: 全幅の一覧は表頭を固定して表の中でスクロールし、詳細の列情報とともに横にあふれない`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "1280px / 1920px は desktop で確かめる");
      await page.setViewportSize(viewport);
      await mockTableManagement(page, { tableCount: 40 });
      await page.goto("/table-management");
      await expect(rowButton(page, "TABLE_01")).toBeVisible();
      const layout = page.getByTestId("list-detail-table-management");
      const list = page.getByTestId("list-detail-table-management-list");
      const [listBox, layoutBox, title] = await Promise.all([
        list.boundingBox(),
        layout.boundingBox(),
        page.getByRole("heading", { level: 1, name: "テーブルの管理" }).boundingBox(),
      ]);
      expect(Math.abs(listBox!.width - layoutBox!.width)).toBeLessThanOrEqual(1);
      expect(Math.abs(listBox!.x - title!.x)).toBeLessThanOrEqual(1);
      // 40 行は表の中でスクロールし、表頭は固定する。
      const scroll = page.getByTestId("db-admin-object-list");
      expect(await scroll.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);
      await scroll.evaluate((node) => {
        node.scrollTop = node.scrollHeight;
      });
      await expect(rowButton(page, "TABLE_40")).toBeInViewport();
      await expect(page.getByTestId("table-management-grid").getByRole("columnheader", { name: "テーブル名" })).toBeInViewport();
      expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: testInfo.outputPath(`table-management-list-${viewport.width}.png`) });

      await rowButton(page, "TABLE_03").click();
      await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.TABLE_03");
      // 列情報の 6 列が全幅の詳細に横スクロールなしで収まる。
      const columns = page.getByTestId("db-admin-detail-columns");
      await expect(columns.getByRole("rowheader", { name: "COLUMN_6" })).toBeVisible();
      expect(await columns.evaluate((node) => node.scrollWidth - node.clientWidth)).toBeLessThanOrEqual(1);
      const [detailBox, titleInDetail] = await Promise.all([
        page.getByTestId("list-detail-table-management-detail").boundingBox(),
        page.getByRole("heading", { level: 1 }).boundingBox(),
      ]);
      expect(Math.abs(detailBox!.x - titleInDetail!.x)).toBeLessThanOrEqual(48);
      expect(Math.abs(detailBox!.width - layoutBox!.width)).toBeLessThanOrEqual(1);
      expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: testInfo.outputPath(`table-management-detail-${viewport.width}.png`) });
    });
  }
});

test.describe("ビューの管理の全幅の一覧 → 全幅の詳細（#4645）", () => {
  test("ビューの一覧は行数の列を持たず、PageHeader に「JOIN/WHERE 条件抽出」を出さず、詳細の「JOIN・WHERE 条件」のタブで抽出する", { tag: "@responsive" }, async ({ page }, testInfo) => {
    const { extractRequests } = await mockViewManagement(page);
    await page.goto("/view-management");
    const grid = page.getByTestId("view-management-grid");
    await expect(page.getByTestId("list-detail-view-management")).toHaveAttribute("data-list-detail-layout", "single");
    await expect(grid.locator("tbody tr")).toHaveCount(4);
    await expect(grid.getByRole("columnheader", { name: "行数" })).toHaveCount(0);
    await expect(page.getByTestId("view-management-row-actions-APP.VIEW_01-trigger")).toBeVisible();
    if (isNarrow(page)) {
      await expect(grid.locator("tbody tr").first().getByTestId("view-management-row-summary")).toHaveText("合成のビュー 1");
    } else {
      await expect(grid.getByRole("columnheader", { name: "ビュー名" })).toBeVisible();
      await expect(grid.getByRole("columnheader", { name: "コメント" })).toBeVisible();
    }
    // JOIN/WHERE の抽出は PageHeader の操作ではなく、詳細のタブ。
    const actions = page.getByTestId("view-management-actions");
    await expect(actions).toBeVisible();
    await expect(actions).not.toContainText("JOIN/WHERE 条件抽出");
    await expect(page.getByRole("button", { name: "JOIN/WHERE 条件抽出" })).toHaveCount(0);
    await expect(page.locator("#view-management-panel-joinWhere")).toHaveCount(0);

    await page.getByTestId("view-management-row-actions-APP.VIEW_02-trigger").click();
    await expect(page.getByRole("menuitem")).toHaveText(["XLSX ダウンロード", "削除"]);
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "APP.VIEW_02 を表示" }).click();
    await expect(page).toHaveURL(/\?view=APP\.VIEW_02$/);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("APP.VIEW_02");
    await expect(page.getByTestId("db-object-header-badges")).toContainText("3 列");
    await expect(actions).toHaveCount(0);
    const detail = page.getByTestId("list-detail-view-management-detail");
    await expect(detail.getByRole("tab")).toHaveText(["列情報", "DDL", "JOIN・WHERE 条件"]);
    const joinWhereTab = page.locator("#view-management-detail-tab-joinWhere");
    await joinWhereTab.click();
    await expect(joinWhereTab).toHaveAttribute("aria-selected", "true");
    const panel = page.locator("#view-management-detail-panel-joinWhere");
    await expect(panel).toBeVisible();
    // 対象の選択の段・選んだビューのカードは持たない（対象は詳細を開いているビュー）。
    await expect(page.getByTestId("view-join-where-steps")).toHaveCount(0);
    await expect(page.getByTestId("view-join-where-selected-view")).toHaveCount(0);
    await expect(panel.getByRole("region", { name: "解析設定" })).toBeAttached();
    await panel.getByRole("button", { name: "AI で抽出" }).click();
    await expect(panel.getByRole("region", { name: "JOIN/WHERE 条件抽出結果" })).toContainText("a.ID = b.ID");
    await expect(panel.getByRole("region", { name: "JOIN/WHERE 条件抽出結果" })).toContainText("a.FLAG = 1");
    expect(extractRequests).toHaveLength(1);
    expect(extractRequests[0]).toContain('"APP"."VIEW_02"');
    expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
    await page.screenshot({ path: testInfo.outputPath(`view-management-join-where-${testInfo.project.name}.png`), fullPage: true });

    await page.getByTestId("view-management-detail-back").click();
    await expect(page).not.toHaveURL(/view=/);
    await expect(page.getByRole("button", { name: "APP.VIEW_02 を表示" })).toBeFocused();
    await expect(actions).toBeVisible();
  });
});
