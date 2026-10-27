import { expect, test, type Page } from "./fixtures/test";
import { FLOW_STAGES } from "./_prompt-stage-fixtures";
import { apiEnvelope, expectNoPageOverflow, mockAuthUser, mockLocalAuth, openSidebarNav } from "./_helpers";

// 検索方法の画面。検索・回答の既定、検索関連プロンプト、回答の記録・質問履歴を持つ。
// 以前の検索モード・検索オプションと、根拠確認・回答スタイル・高度な検索の画面は削除した。

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`検索方法の画面は回答の設定と検索プロンプトを出す (${viewport.name})`, async ({ page }) => {
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

for (const viewport of [
  { name: "1280px", width: 1280, height: 900 },
  { name: "1920px", width: 1920, height: 1080 },
  { name: "375px", width: 375, height: 900 },
]) {
  test(`質問履歴の数値の欄（xs）の補足は欄の幅ではなく列の幅で折り返す（#2063） (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockRetrievalCards(page);
    await page.route("**/api/settings/query-history", (route) =>
      route.fulfill({
        json: {
          data: { enabled: true, retention_days: 90, min_count: 3, suggestion_limit: 5, blocklist: [] },
          error_messages: [],
          warning_messages: [],
        },
      })
    );

    await page.goto("/settings/retrieval");
    const minCount = page.getByRole("spinbutton", { name: "候補にする最小回数" });
    const limit = page.getByRole("spinbutton", { name: "候補の最大件数" });
    await expect(minCount).toBeVisible();
    const hint = page.getByText("同じ質問がこの回数以上聞かれたときだけ候補にします。", { exact: true });
    const layout = await hint.evaluate((element) => {
      const lineHeight = Number.parseFloat(getComputedStyle(element).lineHeight);
      const rect = element.getBoundingClientRect();
      return { lines: Math.round(rect.height / lineHeight), right: rect.right, width: rect.width };
    });
    if (viewport.width >= 640) {
      // 1 文の補足は、入力欄（xs 8rem）や 20rem ではなく列の幅で折り返すので 1 行に収まる。
      expect(layout.lines).toBe(1);
    } else {
      // 375px は欄も補足も全幅（補足は欄と同じ幅で折り返す）。
      expect(Math.abs(layout.width - ((await minCount.boundingBox())?.width ?? 0))).toBeLessThanOrEqual(1);
    }
    // 列の中に収まり、隣の「候補の最大件数」の欄（desktop は右の列）にはみ出さない。
    const minCountBox = await minCount.boundingBox();
    const limitBox = await limit.boundingBox();
    if (minCountBox && limitBox && limitBox.y < minCountBox.y + minCountBox.height) {
      expect(layout.right).toBeLessThanOrEqual(limitBox.x);
    }
    // 入力欄の幅は段（xs）のまま。
    if (viewport.width >= 640) expect(Math.round(minCountBox?.width ?? 0)).toBeLessThanOrEqual(8 * 14 + 1);
    await expectNoPageOverflow(page);
  });
}

const ANSWERING_SETTINGS = {
  query_strategy: "auto_routing",
  answer_flow: "standard_rag",
  neighbor_child_count: 3,
  rerank_enabled: true,
  screen_linking_enabled: false,
  request_coverage_retrieval_enabled: false,
  auto_field_filter_enabled: false,
  graph_expansion_enabled: false,
  config_source: "runtime",
  entity_expansion_enabled: true,
  entity_expansion_max_chunks: 6,
  section_expansion_enabled: true,
  section_expansion_max_levels: 4,
  section_expansion_input_token_percent: 50,
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
      await expect(card.getByRole("switch")).toHaveCount(7);
      expect(await card.getByRole("switch").evaluateAll((elements) => elements.map((element) => element.getAttribute("aria-label"))))
        .toEqual(["質問から項目の条件を読み取る", "実体・別名による検索拡張", "文書構造による検索拡張", "画面目録で操作画面を探す", "Rerank で検索候補を並べ替える", "章節で広げる根拠", "根拠の無い要求を探し直す"]);
      const strategy = page.getByRole("combobox", { name: "質問の拡張", exact: true });
      await expect(strategy).toContainText("自動ルーティング");
      // 回答の生成方式の既定は標準 RAG。先頭に「（既定）」付きで並べ、CRAG は任意で AI の呼び出しが増えると説明する（#3509）。
      const answerFlow = page.getByRole("combobox", { name: "回答の生成方式", exact: true });
      await expect(answerFlow).toContainText("標準 RAG（既定）");
      await expect(answerFlow).toHaveAccessibleDescription(/CRAG は必要なときだけ選ぶ方式.*AI の呼び出しが増え/);
      await expect(page.getByRole("switch", { name: "Rerank で検索候補を並べ替える" })).toBeChecked();
      await page.getByRole("switch", { name: "質問から項目の条件を読み取る" }).scrollIntoViewIfNeeded();
      await page.screenshot({ path: test.info().outputPath("retrieval-option-order.png") });
      const screenLinking = page.getByRole("switch", { name: "画面目録で操作画面を探す" });
      await expect(screenLinking).not.toBeChecked();
      // 根拠の無い要求を探し直す（#1279）は既定で無効。
      const requestCoverage = page.getByRole("switch", { name: "根拠の無い要求を探し直す" });
      await expect(requestCoverage).not.toBeChecked();
      await expect(requestCoverage).toHaveAccessibleDescription(/語の一致だけで.*十分に回答できるとは判定しません/);
      await expect(requestCoverage).toHaveAccessibleDescription(/生成前後.*上限を共有.*モデル呼び出しと待ち時間が増え/);
      await expect(answerFlow).toHaveAccessibleDescription(/初回を含め最大 3 段.*書き換えは最大 2 回/);
      await expect(screenLinking).toHaveAccessibleDescription(/目録がない場合.*省略/);
      const autoFieldFilter = page.getByRole("switch", { name: "質問から項目の条件を読み取る" });
      await expect(autoFieldFilter).not.toBeChecked();
      // 文書構造による検索拡張（#2055）は既定で無効。AI の呼び出しが増えないことを説明に出す。
      const graphExpansion = page.getByRole("switch", { name: "文書構造による検索拡張" });
      await expect(graphExpansion).not.toBeChecked();
      await graphExpansion.scrollIntoViewIfNeeded();
      await page.screenshot({ path: test.info().outputPath("entity-structure-expansion.png") });
      await expect(graphExpansion).toHaveAccessibleDescription(/AI の呼び出しは増えません/);
      await expect(graphExpansion).toHaveAccessibleDescription(/文書構造索引.*併用でき.*重複して加えません/);
      await expect(page.getByRole("switch", { name: "実体・別名による検索拡張" })).toHaveAccessibleDescription(/名称・ID・別名.*1 段.*文書構造による検索拡張と併用/);
      const save = page.getByRole("button", { name: "回答の設定を保存", exact: true });
      await expect(save).toBeDisabled();

      await strategy.click();
      await page.getByRole("option", { name: /RAG フュージョン/ }).click();
      await answerFlow.click();
      await expect(page.getByRole("option")).toHaveText(["標準 RAG（既定）", "CRAG（根拠を評価し、必要なら補正検索）"]);
      await page.getByRole("option", { name: /^CRAG/ }).click();
      await expect(answerFlow).toContainText("CRAG");
      await page.getByRole("combobox", { name: "根拠の前後から加える数", exact: true }).click();
      await page.getByRole("option", { name: "5", exact: true }).click();
      await screenLinking.click();
      await autoFieldFilter.click();
      await requestCoverage.click();
      await graphExpansion.click();
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
          request_coverage_retrieval_enabled: true,
          auto_field_filter_enabled: true,
          graph_expansion_enabled: true,
          entity_expansion_enabled: true,
          entity_expansion_max_chunks: 6,
          section_expansion_enabled: true,
          section_expansion_max_levels: 4,
          section_expansion_input_token_percent: 50,
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

  // 取得の失敗は API の理由と再試行をそのカードに出す（ApiErrorState。#906 / #1002）。
  const card = page.getByTestId("answering-settings-card");
  await expect(card.getByRole("alert")).toContainText("読み込めません");
  await expect(card.getByRole("button", { name: "再試行" })).toBeVisible();
  await expect(page.getByRole("button", { name: "回答の設定を保存" })).toHaveCount(0);
  // ほかのカード（回答の記録・質問履歴）はそのまま使える。
  await expect(page.getByRole("button", { name: "保存期間を保存" })).toBeVisible();
  await expect(page.getByRole("button", { name: "質問履歴の設定を保存" })).toBeVisible();
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 1000, collapse: false },
  { name: "mobile", width: 375, height: 900, collapse: true },
]) {
  test(`質問履歴の数値の欄は空・範囲外を保存の前に欄の下で知らせ、backend の英語の 422 を出さない（#1002, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);
    const saved: unknown[] = [];
    const initial = { ...QUERY_HISTORY, retention_days: 60 };
    await page.route("**/api/settings/query-history", async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        saved.push(body);
        await route.fulfill({ json: apiEnvelope(body) });
        return;
      }
      await route.fulfill({ json: apiEnvelope(initial) });
    });

    await page.goto("/settings/retrieval");
    // .env で決めた選択肢に無い保存期間も「N 日」と出す。
    await expect(page.getByRole("combobox", { name: "履歴の保存期間", exact: true })).toHaveText(/60 日/);
    const minCount = page.getByRole("spinbutton", { name: "候補にする最小回数" });
    const actions = page.getByRole("group", { name: "質問履歴の設定の操作" });
    const save = actions.getByRole("button", { name: "質問履歴の設定を保存" });

    // 空にしても 0 に戻さない。保存を押すと欄の下に理由を出し、その欄へ移る。
    await minCount.fill("");
    await expect(minCount).toHaveValue("");
    await save.click();
    await expect(page.getByText("候補にする最小回数を入力してください。")).toBeVisible();
    await expect(minCount).toBeFocused();

    await minCount.fill("1001");
    await expect(
      page.getByText("候補にする最小回数は 1 以上 1,000 以下の整数を入力してください。")
    ).toBeVisible();
    await save.click();
    await expect(minCount).toBeFocused();
    expect(saved).toEqual([]);
    await expectNoPageOverflow(page);

    // 変更を破棄で保存値へ戻る。
    await actions.getByRole("button", { name: "変更を破棄" }).click();
    await expect(minCount).toHaveValue("3");
    await expect(save).toBeDisabled();

    await minCount.fill("5");
    await save.click();
    await expect(page.getByText("質問履歴の設定を保存しました。")).toBeVisible();
    expect(saved).toEqual([{ ...initial, min_count: 5 }]);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900, collapse: false },
  { name: "mobile", width: 375, height: 900, collapse: true },
]) {
  test(`回答の記録の保存期間を短くするときは、すぐに削除することを確認してから保存する（#1002, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await mockRetrievalCards(page);
    const saved: unknown[] = [];
    await page.route("**/api/settings/answer-records", async (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON() as { retention_days: number };
        saved.push(body);
        await route.fulfill({ json: apiEnvelope({ ...body, config_source: "runtime" }) });
        return;
      }
      await route.fulfill({ json: apiEnvelope(ANSWER_RECORDS) });
    });

    await page.goto("/settings/retrieval");
    const actions = page.getByRole("group", { name: "回答の記録の保存期間の操作" });
    await page.getByRole("combobox", { name: "保存期間", exact: true }).click();
    await page.getByRole("option", { name: "30 日" }).click();
    await actions.getByRole("button", { name: "保存期間を保存" }).click();

    const dialog = page.getByRole("alertdialog", { name: "回答の記録の保存期間を短くしますか？" });
    await expect(dialog).toContainText("30 日より前の回答の記録");
    await dialog.getByRole("button", { name: "キャンセル" }).click();
    // 取り消すと送らず、選んだ値と未保存の表示を残す。
    expect(saved).toEqual([]);
    await expect(page.getByRole("combobox", { name: "保存期間", exact: true })).toHaveText(/30 日/);
    await expect(actions).toContainText("未保存の変更があります。");

    await actions.getByRole("button", { name: "保存期間を保存" }).click();
    await page
      .getByRole("alertdialog", { name: "回答の記録の保存期間を短くしますか？" })
      .getByRole("button", { name: "短くして保存" })
      .click();
    await expect(page.getByText("保存期間を保存しました。")).toBeVisible();
    expect(saved).toEqual([{ retention_days: 30 }]);
    await expect(actions.getByRole("button", { name: "保存期間を保存" })).toBeDisabled();
    await expectNoPageOverflow(page);
  });
}

test("回答の検索と生成の保存に失敗しても入力を残して操作の行に出し、変更を破棄で保存値へ戻せる（#1002）", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockRetrievalCards(page);
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/settings/answering", async (route) => {
    if (route.request().method() === "PATCH") {
      await gate;
      await route.fulfill({
        status: 500,
        json: {
          data: null,
          error_messages: ["回答の検索と生成の設定を backend/.env へ保存できませんでした。"],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: apiEnvelope(ANSWERING_SETTINGS) });
  });

  await page.goto("/settings/retrieval");
  const actions = page.getByRole("group", { name: "回答の検索と生成の設定の操作" });
  const strategy = page.getByRole("combobox", { name: "質問の拡張", exact: true });
  await strategy.click();
  await page.getByRole("option", { name: /RAG フュージョン/ }).click();
  await actions.getByRole("button", { name: "回答の設定を保存" }).click();
  // 保存中は欄を変えられない（保存の後に保存値で作り直すため、保存中の変更は消える）。
  await expect(strategy).toBeDisabled();
  release();

  await expect(actions).toContainText("回答の検索と生成の設定を backend/.env へ保存できませんでした。");
  await expect(strategy).toContainText("RAG フュージョン");
  await expect(strategy).toBeEnabled();
  await actions.getByRole("button", { name: "変更を破棄" }).click();
  await expect(strategy).toContainText("自動ルーティング");
  await expect(actions.getByRole("button", { name: "回答の設定を保存" })).toBeDisabled();
});

test("検索方法のカードは読み込みに失敗しても再試行できる（#1002）", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await mockRetrievalCards(page);
  let failing = true;
  await page.route("**/api/settings/query-history", async (route) => {
    if (failing) {
      await route.fulfill({
        status: 503,
        json: { data: null, error_messages: ["読み込めません"], warning_messages: [] },
      });
      return;
    }
    await route.fulfill({ json: apiEnvelope(QUERY_HISTORY) });
  });

  await page.goto("/settings/retrieval");
  await expect(page.getByRole("alert")).toContainText("読み込めません", { timeout: 15_000 });
  failing = false;
  await page.getByRole("button", { name: "再試行" }).click();
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
async function mockRetrievalCards(page: Page, gate?: Promise<void>, stages = FLOW_STAGES) {
  const cards: [string, unknown][] = [
    ["**/api/settings/answer-prompts", { prompts: [], stages }],
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

for (const width of [1280, 375]) {
  test(`検索プロンプトは処理順・使用条件・読み取り専用で確認できる (${width}px)`, { tag: "@responsive" }, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await mockAuthUser(page, { permissions: ["menu.settings_retrieval"] });
    const displayStages = FLOW_STAGES.map((stage) => stage.id === "history_rewrite" ? {
      ...stage, prompts: [{ id: "system", content: "## 目的と手順\n\n- 合成の対象名を補います。\n- 質問だけを 1 行で返します。" }],
    } : stage);
    await mockRetrievalCards(page, undefined, displayStages);
    await page.goto("/settings/retrieval");
    const prompts = page.getByRole("region", { name: "検索に使うプロンプト", exact: true });
    const card = page.getByTestId("answer-prompt-stages-card");
    await expect(card).toBeVisible();
    const bounds = await card.boundingBox();
    const reference = await page.getByTestId("answering-settings-card").boundingBox();
    expect(Math.abs(bounds!.width-reference!.width)).toBeLessThanOrEqual(1);
    expect(await card.evaluate(node => parseFloat(getComputedStyle(node).borderTopWidth))).toBeGreaterThan(0);

    await expect(prompts.getByRole("heading", { level: 3 })).toHaveText(["質問の解釈", "検索", "根拠の判定"]);
    const expected = displayStages.filter((stage) => stage.id !== "support_guide_conditions" && ["question", "retrieval", "grounding"].includes(stage.phase));
    await expect(prompts.locator("details")).toHaveCount(expected.length);
    for (const stage of expected) {
      const panel = prompts.getByTestId(`answer-prompt-stage-${stage.id}`);
      await expect(panel).toContainText("使われる条件:");
      await panel.locator("summary").click();
      const fields = panel.getByRole("textbox");
      expect(await fields.evaluateAll((elements) => elements.map((element) => (element as HTMLTextAreaElement).value))).toEqual(stage.prompts.map((part) => part.content));
      for (const field of await fields.all()) await expect(field).toHaveAttribute("readonly", "");
      await panel.locator("summary").click();
    }
    await expect(prompts.getByRole("button", { name: "プロンプトを保存" })).toHaveCount(0);
    await expect(prompts.getByTestId("answer-prompt-stage-generation")).toHaveCount(0);
    await expect(prompts.getByTestId("answer-prompt-stage-support_guide_conditions")).toHaveCount(0);
    await expect(prompts.getByTestId("answer-prompt-stage-history_rewrite")).toContainText("書き換えを有効にしたとき");
    await expect(prompts.getByTestId("answer-prompt-stage-section_extraction")).toContainText("原文抽出は既定で無効");
    const rewrite = prompts.getByTestId("answer-prompt-stage-history_rewrite");
    await rewrite.locator("summary").click();
    await expect(rewrite.getByRole("textbox")).toHaveValue(/\n- .+\n- /);
    await rewrite.getByRole("textbox").scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("history-rewrite-bullets.png") });
    await rewrite.locator("summary").click();
    await prompts.getByRole("heading", { level: 2 }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("retrieval-prompts.png") });
    await expectNoPageOverflow(page);
  });

  test(`検索プロンプトの取得失敗は他の設定を妨げず再試行できる (${width}px)`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mockRetrievalCards(page);
    let failed = true;
    await page.route("**/api/settings/answer-prompts", (route) => failed
      ? route.fulfill({ status: 503, json: { data: null, error_messages: ["取得できませんでした"], warning_messages: [] } })
      : route.fulfill({ json: apiEnvelope({ prompts: [], stages: FLOW_STAGES }) }));
    await page.goto("/settings/retrieval");
    await expect(page.getByText("プロンプトを読み込めませんでした。", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole("button", { name: "回答の設定を保存", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "保存期間を保存" })).toBeVisible();
    failed = false;
    await page.getByRole("button", { name: "再試行", exact: true }).click();
    await expect(page.getByRole("region", { name: "検索に使うプロンプト", exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

 test("根拠の前後から加える数に不要なゼロの説明を表示しない", {tag: "@responsive"}, async ({page}) => {
  await mockLocalAuth(page);
  await mockRetrievalCards(page);
  await page.goto("/settings/retrieval");
  await expect(page.getByText("当たった根拠の前と後から、それぞれ最大でこの数のチャンクを文脈に加えます。同じ親やページの範囲、取得できる根拠、入力上限によって実際の数は減ります。", {exact:true})).toBeVisible();
  await expect(page.getByText("0 は加えません。", {exact:false})).toHaveCount(0);
 });
