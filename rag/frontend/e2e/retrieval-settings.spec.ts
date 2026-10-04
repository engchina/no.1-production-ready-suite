import { expect, test, type Page } from "./fixtures/test";
import { apiEnvelope, expectNoPageOverflow, mockAuthUser, mockLocalAuth, openSidebarNav } from "./_helpers";

// 検索方法の画面（#595）。回答の検索と生成・回答の記録の保存期間・質問履歴の 3 つのカードだけを持つ。
// 以前の検索モード・検索オプションと、根拠確認・回答スタイル・高度な検索の画面は削除した。

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`検索方法の画面は回答の設定の 3 つのカードだけを出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);

    await page.goto("/settings/retrieval");

    await expect(page.getByRole("heading", { name: "検索方法", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByTestId("answering-settings-card")).toBeVisible();
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "保存期間を保存" })).toBeVisible();
    await expect(page.getByRole("button", { name: "質問履歴の設定を保存" })).toBeVisible();
    // 削除した検索モード・検索オプション（#595）は出さない。
    await expect(page.getByRole("radiogroup", { name: "検索モード" })).toHaveCount(0);
    await expect(page.getByRole("switch", { name: "クエリ拡張" })).toHaveCount(0);
    await expect(page.getByTestId("answer-prompt-unused-note")).toHaveCount(0);
    // 画面の文言に移植元の呼び名（DocRAG）を出さない。
    await expect(page.locator("main")).not.toContainText("DocRAG");
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。削除した画面はナビに出さない。
    const nav = await openSidebarNav(page);
    await expect(nav.getByRole("link", { name: "検索方法" })).toHaveAttribute("aria-current", "page");
    for (const removed of ["根拠確認", "回答スタイル", "高度な検索"]) {
      await expect(nav.getByRole("link", { name: removed })).toHaveCount(0);
    }
    await expectNoPageOverflow(page);
  });

  test(`検索方法の画面は読み込み中は経過時間を先頭の 1 か所に出し、カードは形を保つ (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await mockRetrievalCards(page, gate);

    await page.goto("/settings/retrieval");

    const loading = page.getByTestId("settings-retrieval-loading");
    await expect(loading).toBeVisible();
    // カードは読み込み中も枠を出し、保存のボタンはまだ出さない（寸法を予約する）。
    await expect(page.getByTestId("answering-settings-card")).toBeVisible();
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toHaveCount(0);
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test(`削除した設定の画面の URL は検索方法へ移す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);

    for (const removed of ["/settings/grounding", "/settings/generation", "/settings/agentic"]) {
      await page.goto(removed);
      await expect(page).toHaveURL(/\/settings\/retrieval$/);
      await expect(page.getByRole("heading", { name: "検索方法", exact: true, level: 1 })).toBeVisible();
    }
  });
}

test("検索方法の権限が無い利用者は、削除した画面の URL から権限なしの画面へ移る", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockAuthUser(page, { permissions: ["menu.chat"] });

  await page.goto("/settings/grounding");

  await expect(page).toHaveURL(/\/forbidden/);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`検索方法の画面で回答の記録の保存期間を保存できる（#593） (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);
    let saved: unknown = null;
    await page.route("**/api/settings/answer-records", async (route) => {
      if (route.request().method() === "PATCH") {
        saved = route.request().postDataJSON();
        await route.fulfill({
          json: { data: { retention_days: 0, config_source: "runtime" }, error_messages: [], warning_messages: [] },
        });
        return;
      }
      await route.fulfill({
        json: { data: { retention_days: 90, config_source: "runtime" }, error_messages: [], warning_messages: [] },
      });
    });

    await page.goto("/settings/retrieval");
    const save = page.getByRole("button", { name: "保存期間を保存" });
    await expect(save).toBeDisabled();
    await page.getByRole("combobox", { name: "保存期間", exact: true }).click();
    await page.getByRole("option", { name: "無期限（手動で削除）" }).click();
    await save.click();

    await expect(page.getByText("保存期間を保存しました。")).toBeVisible();
    expect(saved).toEqual({ retention_days: 0 });
    await expect(save).toBeDisabled();
    await expectNoPageOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 900, collapse: true },
]) {
  test(`検索方法の画面で質問履歴を有効にし、除外する語を保存できる（#593） (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);
    let saved: unknown = null;
    const initial = { enabled: false, retention_days: 90, min_count: 3, suggestion_limit: 5, blocklist: [] };
    await page.route("**/api/settings/query-history", async (route) => {
      if (route.request().method() === "PATCH") saved = route.request().postDataJSON();
      await route.fulfill({ json: { data: saved ?? initial, error_messages: [], warning_messages: [] } });
    });

    await page.goto("/settings/retrieval");
    const save = page.getByRole("button", { name: "質問履歴の設定を保存" });
    await expect(save).toBeDisabled();
    await page.getByRole("switch", { name: "質問を保存して候補に使う" }).click();
    await page.getByLabel("保存・表示しない語").fill("給与\n\n 住所 ");
    await save.click();

    await expect(page.getByText("質問履歴の設定を保存しました。")).toBeVisible();
    expect(saved).toEqual({ ...initial, enabled: true, blocklist: ["給与", "住所"] });
    await expect(page.getByRole("switch", { name: "質問を保存して候補に使う" })).toBeChecked();
    await expectNoPageOverflow(page);
  });
}

const ANSWERING_SETTINGS = {
  query_strategy: "auto_routing",
  answer_flow: "crag",
  neighbor_child_count: 3,
  rerank_enabled: true,
  screen_linking_enabled: false,
  auto_field_filter_enabled: false,
  config_source: "runtime",
};

async function mockAnsweringSettings(page: Page, saved: unknown[] = []) {
  await page.route("**/api/settings/answering", async (route) => {
    if (route.request().method() === "PATCH") {
      const body = route.request().postDataJSON() as Record<string, unknown>;
      saved.push(body);
      await route.fulfill({
        json: { data: { ...ANSWERING_SETTINGS, ...body }, error_messages: [], warning_messages: [] },
      });
      return;
    }
    await route.fulfill({ json: { data: ANSWERING_SETTINGS, error_messages: [], warning_messages: [] } });
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 900, collapse: true },
]) {
  for (const theme of ["light", "dark"] as const) {
    test(`検索方法の画面で回答の検索と生成の既定を変えて保存できる (#593, ${viewport.name}, ${theme})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      // 外観（テーマ）とサイドバーの折りたたみは共有 UI の ui-store の保存値で決まる。
      await page.addInitScript(
        (state) => window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state, version: 0 })),
        { theme, sidebarCollapsed: viewport.collapse }
      );
      await mockRetrievalCards(page);
      const saved: unknown[] = [];
      await mockAnsweringSettings(page, saved);

      await page.goto("/settings/retrieval");
      await expect
        .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
        .toBe(theme === "dark");

      await expect(page.getByText("回答の検索と生成", { exact: true })).toBeVisible();
      // 画面の文言に移植元の呼び名（DocRAG）を出さない（#593）。
      const card = page.getByTestId("answering-settings-card");
      await expect(card).not.toContainText("DocRAG");
      const strategy = page.getByRole("combobox", { name: "質問の拡張", exact: true });
      await expect(strategy).toContainText("自動ルーティング");
      await expect(page.getByRole("combobox", { name: "回答の生成方式", exact: true })).toContainText("CRAG");
      await expect(page.getByRole("switch", { name: "Rerank で検索候補を並べ替える" })).toBeChecked();
      const screenLinking = page.getByRole("switch", { name: "画面目録で操作画面を探す" });
      await expect(screenLinking).not.toBeChecked();
      const autoFieldFilter = page.getByRole("switch", { name: "質問から項目の条件を読み取る" });
      await expect(autoFieldFilter).not.toBeChecked();
      const save = page.getByRole("button", { name: "回答の設定を保存", exact: true });
      await expect(save).toBeDisabled();

      await strategy.click();
      await page.getByRole("option", { name: /RAG フュージョン/ }).click();
      await page.getByRole("combobox", { name: "根拠の前後から加える数", exact: true }).click();
      await page.getByRole("option", { name: "5", exact: true }).click();
      await screenLinking.click();
      await autoFieldFilter.click();
      await expect(save).toBeEnabled();
      await save.click();

      await expect(page.getByText("回答の検索と生成の設定を保存しました。")).toBeVisible();
      expect(saved).toEqual([
        {
          query_strategy: "rag_fusion",
          answer_flow: "crag",
          neighbor_child_count: 5,
          rerank_enabled: true,
          screen_linking_enabled: true,
          auto_field_filter_enabled: true,
        },
      ]);
      await expect(save).toBeDisabled();
      await expect(screenLinking).toBeChecked();
      await expectNoPageOverflow(page);
    });
  }
}

test("回答の検索と生成の既定を読み込めないときは、その欄だけに理由を出す (#593)", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockRetrievalCards(page);
  await page.route("**/api/settings/answering", (route) =>
    route.fulfill({
      status: 503,
      json: { data: null, error_messages: ["読み込めません"], warning_messages: [] },
    })
  );

  await page.goto("/settings/retrieval");

  await expect(page.getByText("回答の検索と生成の設定を読み込めませんでした。")).toBeVisible();
  await expect(page.getByRole("button", { name: "回答の設定を保存" })).toHaveCount(0);
  // ほかのカード（回答の記録・質問履歴）はそのまま使える。
  await expect(page.getByRole("button", { name: "保存期間を保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "質問履歴の設定を保存" })).toBeVisible();
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}


const ANSWER_RECORDS = { retention_days: 90, config_source: "runtime" };
const QUERY_HISTORY = { enabled: false, retention_days: 90, min_count: 3, suggestion_limit: 5, blocklist: [] };

/** 3 つのカードの取得（GET）を mock する。`gate` を渡すと、その間は応答を止める（読み込み中の確認）。 */
async function mockRetrievalCards(page: Page, gate?: Promise<void>) {
  const cards: [string, unknown][] = [
    ["**/api/settings/answering", ANSWERING_SETTINGS],
    ["**/api/settings/answer-records", ANSWER_RECORDS],
    ["**/api/settings/query-history", QUERY_HISTORY],
  ];
  for (const [url, data] of cards) {
    await page.route(url, async (route) => {
      if (gate) await gate;
      await route.fulfill({ json: apiEnvelope(data) });
    });
  }
}
