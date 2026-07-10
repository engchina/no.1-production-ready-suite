import { expect, test, type Locator, type Page, type Route } from "@playwright/test";

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

const scenarios = [
  {
    title: "テーブル一覧",
    path: "/table-management",
    apiPath: "**/api/nl2sql/db-admin/tables",
    prefix: "TABLE",
    objectType: "table",
  },
  {
    title: "ビュー一覧",
    path: "/view-management",
    apiPath: "**/api/nl2sql/db-admin/views",
    prefix: "VIEW",
    objectType: "view",
  },
] as const;

async function mockObjectManagementApi(page: Page, scenario: (typeof scenarios)[number]) {
  const items = Array.from({ length: 30 }, (_, index) => ({
    name: `${scenario.prefix}_${String(index + 1).padStart(2, "0")}`,
    owner: "APP",
    object_type: scenario.objectType,
    row_count: null,
    comment: "",
  }));

  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, {
      refreshed_at: "2026-06-21T10:00:00.000Z",
      tables: [],
    })
  );
  await page.route(scenario.apiPath, (route) =>
    fulfillJson(route, {
      runtime: "deterministic",
      items,
      warnings: [],
    })
  );
  await page.route("**/api/nl2sql/db-admin/tables/*", (route) =>
    fulfillJson(route, {
      name: "TABLE_01",
      owner: "APP",
      object_type: "table",
      row_count: null,
      comment: "",
      columns: [],
      ddl: 'CREATE TABLE "TABLE_01" ("ID" NUMBER)',
      warnings: [],
    })
  );
}

async function expectSingleLine(locator: Locator) {
  const lineCount = await locator.evaluate((node) => {
    const range = document.createRange();
    range.selectNodeContents(node);
    const rects = Array.from(range.getClientRects()).filter((rect) => rect.width > 0 && rect.height > 0);
    range.detach();
    return rects.length;
  });
  expect(lineCount).toBeLessThanOrEqual(1);
}

async function topLevelPanelStyle(page: Page, id: "list" | "create" | "import") {
  return page.locator(`#table-management-panel-${id}`).evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      backgroundColor: computed.backgroundColor,
      borderTopColor: computed.borderTopColor,
      borderTopWidth: computed.borderTopWidth,
      borderRadius: computed.borderRadius,
      boxShadow: computed.boxShadow,
      display: computed.display,
      gap: computed.gap,
      paddingTop: computed.paddingTop,
    };
  });
}

async function compactVisualStyle(locator: Locator) {
  return locator.evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      alignItems: computed.alignItems,
      backgroundColor: computed.backgroundColor,
      borderTopColor: computed.borderTopColor,
      borderTopWidth: computed.borderTopWidth,
      color: computed.color,
      display: computed.display,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      gap: computed.gap,
      minHeight: computed.minHeight,
      paddingTop: computed.paddingTop,
      paddingBottom: computed.paddingBottom,
    };
  });
}

for (const scenario of scenarios) {
  test(
    scenario.objectType === "table"
      ? `${scenario.title}はグリッドの高さを上限内に収める`
      : `${scenario.title}は 10 件分の高さで表示する`,
    async ({ page }) => {
    await mockObjectManagementApi(page, scenario);
    await page.goto(scenario.path);

    const list = page.getByTestId("db-admin-object-list");
    if (scenario.objectType === "table") {
      await expect(page.getByTestId("table-management-grid").locator("tbody tr")).toHaveCount(30);
    } else {
      await expect(list.getByRole("listitem")).toHaveCount(30);
    }

    const fit = await list.evaluate((node) => {
      const listBox = node.getBoundingClientRect();
      const rowSelector = node.querySelector("tbody tr") ? "tbody tr" : "button";
      const rows = Array.from(node.querySelectorAll(rowSelector)).map((row) => row.getBoundingClientRect());
      return {
        listHeight: listBox.height,
        firstInside: rows[0].top >= listBox.top - 1 && rows[0].bottom <= listBox.bottom + 1,
        tenthInside: rows[9].bottom <= listBox.bottom + 1,
        eleventhBelow: rows[10].bottom > listBox.bottom + 1,
        lastBelow: rows[rows.length - 1].bottom > listBox.bottom + 1,
      };
    });

    if (scenario.objectType === "table") {
      expect(fit.listHeight).toBeGreaterThanOrEqual(320);
      expect(fit.listHeight).toBeLessThanOrEqual(680);
      expect(fit.firstInside).toBe(true);
      expect(fit.lastBelow).toBe(true);
    } else {
      expect(fit.listHeight).toBeGreaterThanOrEqual(622);
      expect(fit.tenthInside).toBe(true);
      expect(fit.eleventhBelow).toBe(true);
    }
    }
  );
}

test("テーブル管理は 150% zoom 相当でもタブ・列名・操作ボタンを折り返さない", async ({ page }) => {
  await page.setViewportSize({ width: 1365, height: 900 });
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  await expect(page.getByRole("tab", { name: "テーブル一覧と詳細" })).toHaveAttribute("aria-selected", "true");
  await expectSingleLine(page.getByRole("tab", { name: "テーブル一覧と詳細" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "テーブル作成" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "Excel/CSV 取込(新規テーブル)" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "列情報" }).locator("span").last());
  await expectSingleLine(page.getByRole("tab", { name: "DDL" }).locator("span").last());

  const grid = page.getByTestId("table-management-grid");
  await expect(grid.locator("tbody tr")).toHaveCount(30);
  await expect(grid.getByRole("columnheader", { name: /コメント/ })).toHaveCount(0);
  await expectSingleLine(grid.getByRole("columnheader", { name: /所有者/ }).locator("span").first());
  await expectSingleLine(grid.getByRole("button", { name: "詳細" }).first().locator("span").first());
  await expectSingleLine(grid.getByRole("button", { name: "削除" }).first().locator("span").first());

  const scroll = await page.getByTestId("db-admin-object-list").evaluate((node) => ({
    internalWidthStable: node.scrollWidth >= node.clientWidth,
    pageHorizontal:
      document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
      document.body.scrollWidth > document.body.clientWidth + 1,
  }));
  expect(scroll.internalWidthStable).toBe(true);
  expect(scroll.pageHorizontal).toBe(false);
});

test("Excel/CSV 取込フォームはファイル選択と取込方法の高さを揃える", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");
  const importTab = page.getByRole("tab", { name: "Excel/CSV 取込(新規テーブル)" });
  await importTab.click();
  await expect(importTab).toHaveAttribute("aria-selected", "true");

  const importPanel = page.locator("#table-management-panel-import");
  await expect(importPanel).toBeVisible();
  const fileField = importPanel.getByTestId("table-import-file-field");
  const modeField = importPanel.getByTestId("table-import-mode-field");
  await expect(fileField).toBeVisible();
  await expect(modeField).toBeVisible();
  await expect(modeField.getByText("取込方法")).toBeVisible();
  await expect(modeField.locator("option")).toHaveText([
    "新規作成",
    "既存データに追加",
    "データを全削除して取込",
    "テーブルを再作成",
  ]);

  const fileFieldBox = await fileField.boundingBox();
  const modeFieldBox = await modeField.boundingBox();
  const filePickerBox = await fileField.locator("label").first().boundingBox();
  const modeSelectBox = await modeField.locator("select").boundingBox();
  const clearButtonBox = await fileField.getByRole("button", { name: "取込ファイルをクリア" }).boundingBox();

  expect(fileFieldBox).not.toBeNull();
  expect(modeFieldBox).not.toBeNull();
  expect(filePickerBox).not.toBeNull();
  expect(modeSelectBox).not.toBeNull();
  expect(clearButtonBox).not.toBeNull();
  expect(Math.abs(fileFieldBox!.height - modeFieldBox!.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(filePickerBox!.height - modeSelectBox!.height)).toBeLessThanOrEqual(1);
  expect(Math.abs(clearButtonBox!.height - modeSelectBox!.height)).toBeLessThanOrEqual(1);
});

test("テーブル管理の3つのトップレベルタブは同じパネル外枠で表示する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  const listStyle = await topLevelPanelStyle(page, "list");
  expect(listStyle.backgroundColor).toBe("rgb(255, 255, 255)");
  expect(listStyle.borderTopWidth).toBe("1px");
  expect(Number.parseFloat(listStyle.paddingTop)).toBeGreaterThan(0);

  for (const target of [
    { id: "list", tabName: "テーブル一覧と詳細" },
    { id: "create", tabName: "テーブル作成" },
    { id: "import", tabName: "Excel/CSV 取込(新規テーブル)" },
  ] as const) {
    await page.getByRole("tab", { name: target.tabName }).click();
    const panel = page.locator(`#table-management-panel-${target.id}`);
    await expect(panel).toBeVisible();
    expect(await topLevelPanelStyle(page, target.id)).toEqual(listStyle);

    const hasPageHorizontalScroll = await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1
    );
    expect(hasPageHorizontalScroll).toBe(false);
  }
});

test("テーブル作成フォームの見出し・実行ボタン・ステップはExcel/CSV取込と同じ階層で表示する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  await page.getByRole("tab", { name: "テーブル作成" }).click();
  const createPanel = page.locator("#table-management-panel-create");
  const createHeading = createPanel.getByRole("heading", { name: "テーブル作成", level: 2 });
  const createButton = createPanel.getByRole("button", { name: "SQL 実行" });
  const createSteps = createPanel.getByTestId("table-create-steps");
  await expect(createHeading).toBeVisible();
  await expect(createPanel.getByText("実行できるのは CREATE TABLE / COMMENT ON / DROP TABLE のみです。")).toBeVisible();
  await expect(createPanel.getByText("Oracle への SQL 実行")).toBeVisible();
  await expect(createButton).toBeDisabled();
  await expect(createSteps.getByText("1. SQL 入力")).toBeVisible();
  await expect(createSteps.getByText("2. 実行確認")).toBeVisible();

  const createStyle = await createHeading.evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      tagName: node.tagName,
      display: computed.display,
      alignItems: computed.alignItems,
      gap: computed.gap,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      color: computed.color,
    };
  });
  const createButtonStyle = await compactVisualStyle(createButton);
  const createStepStyle = await compactVisualStyle(createSteps.locator("li").first());

  await page.getByRole("tab", { name: "Excel/CSV 取込(新規テーブル)" }).click();
  const importPanel = page.locator("#table-management-panel-import");
  const importHeading = importPanel.getByRole("heading", { name: "Excel/CSV 取込(新規テーブル)", level: 2 });
  const importButton = importPanel.getByRole("button", { name: "取込を実行" });
  const importSteps = importPanel.getByTestId("table-import-steps");
  await expect(importHeading).toBeVisible();
  await expect(importButton).toBeDisabled();
  await expect(importSteps.getByText("1. ファイル選択")).toBeVisible();
  await expect(importSteps.getByText("2. 実行確認")).toBeVisible();

  const importStyle = await importHeading.evaluate((node) => {
    const computed = window.getComputedStyle(node);
    return {
      tagName: node.tagName,
      display: computed.display,
      alignItems: computed.alignItems,
      gap: computed.gap,
      fontSize: computed.fontSize,
      fontWeight: computed.fontWeight,
      color: computed.color,
    };
  });
  const importButtonStyle = await compactVisualStyle(importButton);
  const importStepStyle = await compactVisualStyle(importSteps.locator("li").first());

  expect(createStyle).toEqual(importStyle);
  expect(createButtonStyle).toEqual(importButtonStyle);
  expect(createStepStyle).toEqual(importStepStyle);
});

test("テーブル管理トップレベルタブはキーボードで切り替えられる", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");

  const listTab = page.getByRole("tab", { name: "テーブル一覧と詳細" });
  const createTab = page.getByRole("tab", { name: "テーブル作成" });
  const importTab = page.getByRole("tab", { name: "Excel/CSV 取込(新規テーブル)" });

  await expect(listTab).toHaveAttribute("aria-selected", "true");
  await listTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#table-management-panel-create")).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(importTab).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("#table-management-panel-import")).toBeVisible();
  await page.keyboard.press("ArrowLeft");
  await expect(createTab).toHaveAttribute("aria-selected", "true");
});

test("Excel/CSV 取込の実行確認語は ADMIN_EXECUTE 固定で判定する", async ({ page }) => {
  await mockObjectManagementApi(page, scenarios[0]);
  await page.goto("/table-management");
  await page.getByRole("tab", { name: "Excel/CSV 取込(新規テーブル)" }).click();

  const importPanel = page.locator("#table-management-panel-import");
  const confirmationField = importPanel.getByTestId("execution-confirmation-field");
  const executeButton = importPanel.getByRole("button", { name: "取込を実行" });
  await expect(importPanel.getByText("入力条件: ADMIN_EXECUTE")).toBeVisible();

  await importPanel.getByLabel("実行確認語").fill("ADMIN_EXECUTE");
  await expect(confirmationField.getByText("確認済み")).toBeVisible();
  await expect(executeButton).toBeDisabled();

  await importPanel.getByLabel("Oracle 表名").fill("IMPORTED_ORDERS");
  await importPanel.getByLabel("実行確認語").fill("IMPORTED_ORDERS");
  await expect(confirmationField.getByText("不一致")).toBeVisible();
  await expect(executeButton).toBeDisabled();
});
