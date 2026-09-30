import { expect, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockKnowledgeBases(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`業務ビューの作成エディタは設定を表示し横崩れしない (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockBusinessViews(page, []);

    await page.goto("/business-views?id=new");

    await expect(page.getByRole("heading", { name: "業務ビューを作成" })).toBeVisible();
    // A 型のエディタの「一覧へ戻る」は左上（パンくずは出さない。page-archetypes.md §1 A、#618）。
    await expect(page.getByTestId("editor-back")).toHaveText("一覧へ戻る");
    await expect(page.getByTestId("editor-back")).toHaveAccessibleName(/の一覧へ戻る$/);
    await expect(page.getByRole("navigation", { name: "パンくず" })).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "名前", exact: true })).toBeVisible();
    await expect(page.getByText("参照するナレッジベース", { exact: false }).first()).toBeVisible();
    // ナレッジベースはコンボボックスを開くと候補として現れる。
    await page.getByRole("combobox", { name: "参照するナレッジベース" }).click();
    await expect(page.getByRole("option", { name: /社内規程/ })).toBeVisible();
    const settings = page.locator("fieldset").filter({ hasText: "検索・回答設定" });
    // 検索方法・検索オプション・根拠確認・回答スタイル・回答プロンプト（system prompt・既定言語）は削除した(#595)。
    await expect(settings.getByRole("heading", { level: 3 })).toHaveText([
      "質問の拡張",
      "回答の生成方式",
      "根拠の前後から加える数",
      "回答の検索のオプション",
      "安全チェック",
    ]);
    await expect(settings.getByRole("heading", { name: "検索インデックス" })).toHaveCount(0);
    // 品質評価は業務ビューで上書きしない(評価はグローバル設定だけで決まる。#301)。
    await expect(settings.getByRole("heading", { name: "品質評価" })).toHaveCount(0);
    // 全文検索の分割方式は 1 つにまとめ、選択を削除した(#588)。
    await expect(settings.getByRole("heading", { name: "全文検索の分割方式" })).toHaveCount(0);
    await expect(settings.getByRole("combobox", { name: "全文検索の分割方式" })).toHaveCount(0);
    // 回答エンジンの選択は削除した(#594)。
    await expect(settings.getByRole("combobox", { name: "回答エンジン" })).toHaveCount(0);
    // 継承 chip: セレクト4行(回答の 3 行 + 安全チェック) + 三値トグル2行(回答の Rerank・画面目録)。
    await expect(settings.getByRole("button", { name: "グローバル既定を継承" })).toHaveCount(6);
    await expect(settings.getByRole("button", { name: "業務ビューで上書き" })).toHaveCount(4);
    await expect(page.getByLabel("回答の役割・口調")).toHaveCount(0);
    await expect(page.getByLabel("既定の回答言語")).toHaveCount(0);
    await expect(settings.getByTestId("answer-prompt-unused-note")).toHaveCount(0);
    await expect(settings).not.toContainText("DocRAG");
    await expectNoPageOverflow(page);
  });
}

test("業務ビューを作成すると参照 KB と方針を含めて POST し、作成した業務ビューのエディタへ置き換えて移る", async ({
  page,
}) => {
  let createBody: Record<string, unknown> | null = null;
  await mockBusinessViews(page, [], (body) => {
    createBody = body;
  });

  await page.goto("/business-views");
  await page.getByRole("button", { name: "新規作成" }).click();
  await expect(page).toHaveURL(/\/business-views\?id=new$/);

  await page.getByRole("combobox", { name: "参照するナレッジベース" }).click();
  await page.getByRole("option", { name: /社内規程/ }).click();
  await page.getByRole("combobox", { name: "参照するナレッジベース" }).press("Escape");
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("経理ビュー");
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("経理規程の問い合わせに回答します");
  const guardrailSetting = page.getByRole("heading", { name: "安全チェック", level: 3 }).locator("..");
  const override = guardrailSetting.getByRole("button", { name: "業務ビューで上書き" });
  await override.click();
  await expect(override).toHaveAttribute("aria-pressed", "true");
  await guardrailSetting.getByRole("combobox", { name: "安全チェック" }).click();
  await page.getByRole("option", { name: "規制対応" }).click();
  await page.locator("[data-page-header-actions]").getByRole("button", { name: "作成", exact: true }).click();

  await expect
    .poll(() => (createBody?.config as { knowledge_base_ids?: string[] })?.knowledge_base_ids)
    .toEqual(["kb-1"]);
  expect(createBody?.name).toBe("経理ビュー");
  expect(createBody?.description).toBe("経理規程の問い合わせに回答します");
  expect(
    (createBody?.config as { query?: { guardrail_policy?: string } })?.query?.guardrail_policy
  ).toBe("regulated");
  // 削除した項目（#595）は送らない。
  const config = (createBody?.config ?? {}) as Record<string, unknown>;
  expect("system_prompt" in config).toBe(false);
  expect("default_language" in config).toBe(false);
  for (const removed of ["retrieval_strategy", "post_retrieval_pipeline", "generation_profile"]) {
    expect(removed in ((config.query as Record<string, unknown>) ?? {})).toBe(false);
  }
  expect(
    "vector_index_profile" in
      ((createBody?.config as { query?: Record<string, unknown> })?.query ?? {})
  ).toBe(false);
  // 3 層モデルでは配信モード UI を持たず、常に全 recipe を融合する。
  expect((createBody?.config as { serving_mode?: string })?.serving_mode).toBe("fused");

  // 作成に成功したら作成した業務ビューのエディタへ replace で移る（戻るで空の新規フォームへ戻らない）。
  await expect(page).toHaveURL(/\/business-views\?id=bv-new$/);
  await expect(page.getByRole("heading", { name: "経理ビュー", level: 1 })).toBeVisible();
  await expect(page.locator("[data-page-header-actions]").getByRole("button", { name: "保存", exact: true })).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/\/business-views$/);
  await expect(page.getByRole("button", { name: "新規作成" })).toBeVisible();
});

test("回答の設定は常に表示し、上書きした値を POST する（回答エンジンは送らない）", async ({ page }) => {
  let createBody: Record<string, unknown> | null = null;
  await mockBusinessViews(page, [], (body) => {
    createBody = body;
  });
  await page.goto("/business-views?id=new");

  await page.getByRole("combobox", { name: "参照するナレッジベース" }).click();
  await page.getByRole("option", { name: /社内規程/ }).click();
  await page.getByRole("combobox", { name: "参照するナレッジベース" }).press("Escape");
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("手順ビュー");
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("操作手順に回答する業務ビュー");

  const strategy = page.getByRole("heading", { name: "質問の拡張", level: 3 }).locator("..");
  await strategy.getByRole("button", { name: "業務ビューで上書き" }).click();
  await strategy.getByRole("combobox", { name: "質問の拡張" }).click();
  await page.getByRole("option", { name: "仮説文生成（HyDE）" }).click();
  const neighbor = page
    .getByRole("heading", { name: "根拠の前後から加える数", level: 3 })
    .locator("..");
  await neighbor.getByRole("button", { name: "業務ビューで上書き" }).click();
  await page
    .getByRole("group", { name: "Rerank で検索候補を並べ替える" })
    .getByRole("button", { name: "OFF" })
    .click();
  // 画面目録の連携は既定 無効(継承)。LLM の呼び出しが増えることを説明に出す(#554)。
  const screenLinking = page.getByRole("group", { name: "画面目録で操作画面を探す" });
  await expect(screenLinking.getByRole("button", { name: "グローバル既定を継承" })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
  await expect(screenLinking).toHaveAccessibleDescription(/AI の呼び出しが 1 回増えます/);
  await screenLinking.getByRole("button", { name: "ON" }).click();
  await page.locator("[data-page-header-actions]").getByRole("button", { name: "作成", exact: true }).click();

  await expect.poll(() => createBody?.name).toBe("手順ビュー");
  const query = (createBody?.config as { query?: Record<string, unknown> })?.query ?? {};
  expect("answer_engine" in query).toBe(false);
  expect(query.query_strategy).toBe("hyde");
  expect(query.neighbor_child_count).toBe(3);
  expect(query.rerank_enabled).toBe(false);
  expect(query.screen_linking_enabled).toBe(true);
  expect(query.answer_flow ?? null).toBeNull();
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`DEFAULT は参照 KB と名前を固定し設定だけ保存できる (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    let updateBody: Record<string, unknown> | null = null;
    await mockDefaultBusinessView(page, (body) => {
      updateBody = body;
    });

    await page.goto("/business-views");

    const row = page.getByTestId("business-view-row-bv-default");
    // アーカイブは行の RowActionMenu に入り、DEFAULT では理由付きで無効（#131）。
    await row.getByRole("button", { name: "DEFAULT の操作" }).click();
    await expect(page.getByRole("menuitem", { name: "DEFAULT はアーカイブできません" })).toBeDisabled();
    await page.keyboard.press("Escape");
    await expect(row.getByRole("button", { name: "DEFAULT の操作" })).toBeFocused();
    // キーボードは先頭セルの名前のボタンで全画面エディタを開く（page-archetypes.md §0-7）。
    await row.getByRole("link", { name: "DEFAULT を編集" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/business-views\?id=bv-default$/);
    await expect(page.getByRole("heading", { name: "DEFAULT", level: 1 })).toBeVisible();
    // エディタの操作のバーにも同じ定義（DEFAULT はアーカイブ不可）を出す。
    await page
      .getByTestId("business-view-detail-actions")
      .getByRole("button", { name: "その他の操作" })
      .click();
    await expect(page.getByRole("menuitem", { name: "DEFAULT はアーカイブできません" })).toBeDisabled();
    await page.keyboard.press("Escape");

    await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveAttribute("readonly", "");
    await expect(page.getByText("DEFAULT の名前は変更できません。")).toBeVisible();
    await expect(page.getByRole("combobox", { name: "参照するナレッジベース" })).toBeDisabled();
    await expect(page.getByText(/DEFAULT ナレッジベースだけを参照します/)).toBeVisible();

    // 説明は必須（#521）。説明が空の既存の業務ビューは、保存するときに入力を求めて送らない。
    const description = page.getByRole("textbox", { name: "説明", exact: true });
    await expect(description).toHaveAttribute("aria-required", "true");
    const guardrail = page.getByRole("heading", { name: "安全チェック", level: 3 }).locator("..");
    await guardrail.getByRole("button", { name: "業務ビューで上書き" }).click();
    await page.locator("[data-page-header-actions]").getByRole("button", { name: "保存", exact: true }).click();
    await expect(page.getByText("説明を入力してください。")).toBeVisible();
    await expect(description).toHaveAttribute("aria-invalid", "true");
    await expect(description).toBeFocused();
    expect(updateBody).toBeNull();

    await description.fill("全社共通の検索設定");
    await page.locator("[data-page-header-actions]").getByRole("button", { name: "保存", exact: true }).click();

    await expect.poll(() => updateBody?.name).toBeUndefined();
    await expect
      .poll(() => (updateBody?.config as { knowledge_base_ids?: string[] })?.knowledge_base_ids)
      .toEqual(["kb-default"]);
    expect(
      (updateBody?.config as { query?: { guardrail_policy?: string } })?.query?.guardrail_policy
    ).toBe("strict");
    expect(updateBody?.description).toBe("全社共通の検索設定");
    await expectNoPageOverflow(page);
  });
}

test("業務ビューの名前と説明は必須で、空・空白だけでは作成せず最初の不正な欄へフォーカスする", async ({
  page,
}) => {
  let createBody: Record<string, unknown> | null = null;
  await mockBusinessViews(page, [], (body) => {
    createBody = body;
  });
  await page.goto("/business-views?id=new");

  const name = page.getByRole("textbox", { name: "名前", exact: true });
  const description = page.getByRole("textbox", { name: "説明", exact: true });
  await expect(name).toHaveAttribute("aria-required", "true");
  await expect(description).toHaveAttribute("aria-required", "true");
  // placeholder に「任意」を出さない。
  await expect(description).toHaveAttribute("placeholder", "例: 経理規程の問い合わせに回答します");

  await page.locator("[data-page-header-actions]").getByRole("button", { name: "作成", exact: true }).click();
  await expect(page.getByText("名前を入力してください。")).toBeVisible();
  await expect(page.getByText("説明を入力してください。")).toBeVisible();
  await expect(name).toBeFocused();

  await name.fill("経理ビュー");
  await description.fill("   ");
  await page.locator("[data-page-header-actions]").getByRole("button", { name: "作成", exact: true }).click();
  await expect(page.getByText("名前を入力してください。")).toHaveCount(0);
  await expect(page.getByText("説明を入力してください。")).toBeVisible();
  await expect(description).toBeFocused();
  expect(createBody).toBeNull();
  await expect(page).toHaveURL(/\/business-views\?id=new$/);
  await expectNoPageOverflow(page);
});

test("業務ビュー作成では DEFAULT を予約名として拒否する", async ({ page }) => {
  await mockBusinessViews(page, []);
  await page.goto("/business-views?id=new");

  await page.getByRole("textbox", { name: "名前", exact: true }).fill(" default ");
  await page.getByRole("textbox", { name: "名前", exact: true }).blur();

  await expect(page.getByText("DEFAULT は予約名のため使用できません。")).toBeVisible();
});

test("RAG 検索は複数業務ビューを選ぶと business_view_ids を送る", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockBusinessViews(page, [
    {
      id: "bv-1",
      name: "経理ビュー",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 1,
      created_at: "2026-06-19T00:00:00Z",
      updated_at: "2026-06-19T00:00:00Z",
      archived_at: null,
    },
    {
      id: "bv-2",
      name: "人事ビュー",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 2,
      created_at: "2026-06-19T00:00:00Z",
      updated_at: "2026-06-19T00:00:00Z",
      archived_at: null,
    },
  ]);

  let searchPayload: Record<string, unknown> | null = null;
  await page.route("**/api/search/stream", async (route) => {
    searchPayload = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: searchStreamBody(),
    });
  });

  await page.goto("/search");

  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  const businessViewList = page.getByRole("listbox", { name: /対象の業務ビュー/ });
  await businessViewList.getByRole("option", { name: /経理ビュー/ }).click();
  await businessViewList.getByRole("option", { name: /人事ビュー/ }).click();
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("経費精算の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  await expect.poll(() => searchPayload?.business_view_ids).toEqual(["bv-1", "bv-2"]);
  await expect.poll(() => searchPayload?.knowledge_base_ids).toBeUndefined();
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`RAG 検索は業務ビューが無いと作成導線の空状態を出す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockBusinessViews(page, []);

    await page.goto("/search");

    // 作成を促す空状態と CTA を表示する。
    await expect(page.getByText("業務ビューを作成してください")).toBeVisible();
    await expect(page.getByRole("button", { name: "業務ビューを作成" })).toBeVisible();

    // 検索入力・ナレッジベースピッカーは出さない(業務ビュー一本化)。
    await expect(page.getByRole("textbox", { name: "RAG 検索" })).toHaveCount(0);
    await expect(page.getByText("ナレッジベース名で絞り込み")).toHaveCount(0);

    await expectNoPageOverflow(page);

    // CTA は業務ビュー管理へ遷移する。
    await page.getByRole("button", { name: "業務ビューを作成" }).click();
    await expect(page).toHaveURL(/\/business-views\?id=new$/);
    await expect(page.getByRole("heading", { name: "業務ビューを作成" })).toBeVisible();
  });
}

test("RAG 検索は業務ビュー未選択だと必須エラーを出し送信しない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await mockBusinessViews(page, [
    {
      id: "bv-1",
      name: "経理ビュー",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 1,
      created_at: "2026-06-19T00:00:00Z",
      updated_at: "2026-06-19T00:00:00Z",
      archived_at: null,
    },
  ]);

  let searchCalled = false;
  await page.route("**/api/search/stream", async (route) => {
    searchCalled = true;
    await route.fulfill({
      status: 200,
      headers: { "content-type": "text/event-stream" },
      body: searchStreamBody(),
    });
  });

  await page.goto("/search");

  // 業務ビューを選ばずに検索すると必須エラー。
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("経費精算の上限");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  await expect(page.getByText("対象の業務ビューを選択してください。")).toBeVisible();
  expect(searchCalled).toBe(false);
});

test("RAG 検索は DEFAULT を候補表示するが自動選択しない", async ({ page }) => {
  await mockBusinessViews(page, [
    {
      id: "bv-default",
      name: "DEFAULT",
      description: null,
      status: "ACTIVE",
      knowledge_base_count: 1,
      created_at: "2026-06-30T00:00:00Z",
      updated_at: "2026-06-30T00:00:00Z",
      archived_at: null,
    },
  ]);
  await page.goto("/search");

  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  await expect(page.getByRole("option", { name: /DEFAULT/ })).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("全社規程");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  await expect(page.getByText("対象の業務ビューを選択してください。")).toBeVisible();
});

const accountingView: BusinessViewSummaryFixture = {
  id: "bv-1",
  name: "経理ビュー",
  description: "経費精算の相談",
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-06-19T00:00:00Z",
  updated_at: "2026-06-19T00:00:00Z",
  archived_at: null,
};

test("業務ビューは行のクリックで ?id= の全画面エディタを開き、再読込・戻る / 進むで同じ対象を開く", async ({
  page,
}) => {
  await mockBusinessViews(page, [accountingView]);
  await page.goto("/business-views");

  const row = page.getByTestId("business-view-row-bv-1");
  // 行の操作以外の領域（参照 KB の列）のクリックで開く。
  await row.getByRole("cell").nth(2).click();
  await expect(page).toHaveURL(/\/business-views\?id=bv-1$/);
  await expect(page.getByRole("heading", { name: "経理ビュー", level: 1 })).toBeVisible();
  await expect(page.getByTestId("editor-back")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveValue("経理ビュー");
  await expect(page.getByRole("heading", { name: "業務ビューの知識" })).toBeVisible();
  await expectNoPageOverflow(page);

  await page.reload();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveValue("経理ビュー");

  await page.goBack();
  await expect(page).toHaveURL(/\/business-views$/);
  await expect(page.getByTestId("business-view-row-bv-1")).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/business-views\?id=bv-1$/);
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveValue("経理ビュー");

  // 一覧へ戻るボタンは履歴に積んで一覧へ移る。
  await clickBackToList(page);
  await expect(page).toHaveURL(/\/business-views$/);
});

test("業務ビューのエディタは未保存の変更があると一覧へ戻るの移動を確認し、下書きを対象ごとに残す", async ({
  page,
}) => {
  await mockBusinessViews(page, [accountingView]);
  await page.goto("/business-views?id=bv-1");
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("経費と出張の相談");

  await page.getByTestId("editor-back").click();
  const dialog = page.getByRole("alertdialog", { name: "保存していない変更があります" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page).toHaveURL(/\?id=bv-1$/);

  await clickBackToList(page);
  await page
    .getByRole("alertdialog", { name: "保存していない変更があります" })
    .getByRole("button", { name: "移動する" })
    .click();
  await expect(page).toHaveURL(/\/business-views$/);

  // 同じ対象を開き直すと下書きを復元する。新規（?id=new）には持ち込まない。
  await page.getByRole("link", { name: "経理ビュー を編集" }).click();
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveValue("経費と出張の相談");
  await expect(page.getByText("保存していない下書きを復元しました。")).toBeVisible();
  await page.getByRole("button", { name: "変更を破棄" }).click();
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveValue("経費精算の相談");
  await page.goto("/business-views?id=new");
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveValue("");
});

// #586: エディタには業務ビューのフォームのほかに知識の節のフォーム（それぞれ離脱の確認を持つ）がある。
// どのフォームが未保存でも、ブラウザの戻る・進むで確認し、キャンセルで留まる。
test("業務ビューのエディタは未保存の変更があるとブラウザの戻る・進むでも確認する", async ({ page }) => {
  const consoleWarnings: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "warning") consoleWarnings.push(message.text());
  });
  await mockBusinessViews(page, [accountingView]);
  await page.goto("/business-views");
  await page.getByRole("link", { name: "経理ビュー を編集" }).click();
  await expect(page).toHaveURL(/\?id=bv-1$/);
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("経費と出張の相談");

  await page.goBack();
  const dialog = page.getByRole("alertdialog", { name: "保存していない変更があります" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page).toHaveURL(/\?id=bv-1$/);
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveValue("経費と出張の相談");

  await page.goBack();
  await dialog.getByRole("button", { name: "移動する" }).click();
  await expect(page).toHaveURL(/\/business-views$/);

  // 業務ビューのフォームは未変更のまま、知識の節（重要語句）だけを変えても、戻るで確認する。
  // （移動しても下書きはこのタブに残るため、開き直したら先に元に戻す。）
  await page.goForward();
  await expect(page).toHaveURL(/\?id=bv-1$/);
  await page.getByRole("button", { name: "変更を破棄" }).click();
  await page.locator("#domain-keywords-editor").fill("経費精算");
  await page.goBack();
  const discardDialog = page.getByRole("alertdialog", { name: "変更を破棄しますか" });
  await expect(discardDialog).toBeVisible();
  await discardDialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page).toHaveURL(/\?id=bv-1$/);
  await expect(page.locator("#domain-keywords-editor")).toHaveValue("経費精算");
  // サイドナビのリンクとリロード（beforeunload）も同じく確認する。
  await page.getByTestId("editor-back").click();
  await expect(discardDialog).toBeVisible();
  await discardDialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page).toHaveURL(/\?id=bv-1$/);
  const reloadPrevented = await page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
  expect(reloadPrevented).toBe(true);
  // 1 画面に離脱の確認が複数あっても、router の blocker は 1 つだけにする。
  expect(consoleWarnings.filter((text) => text.includes("only supports one blocker"))).toEqual([]);
});

// #585: ヘッダーに保存がある全画面のエディタは、欄に結び付かない保存の失敗をヘッダーの直下の 1 か所だけに出す
// （Toast とフォームの下の FormStatus に重ねない。UX 契約 messaging.md §3.3.1）。
test("業務ビューの保存に失敗すると、理由をヘッダーの直下だけに出し、下までスクロールしていても見える", async ({
  page,
}) => {
  const message = "アーカイブ済みのナレッジベースは参照できません。";
  await mockBusinessViews(page, [accountingView]);
  let attempts = 0;
  await page.route("**/api/business-views/bv-1", async (route) => {
    if (route.request().method() !== "PATCH") return route.fallback();
    attempts += 1;
    await route.fulfill({
      status: 409,
      json: { data: null, error_messages: [message], warning_messages: [] },
    });
  });
  await page.goto("/business-views?id=bv-1");
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("経費と出張の相談");
  // 長いフォームの下までスクロールしてから、ヘッダーの保存を押す。
  await page.getByRole("heading", { name: "業務ビューの知識" }).scrollIntoViewIfNeeded();
  const actions = page.getByRole("group", { name: "ページ操作" });
  const save = actions.getByRole("button", { name: "保存", exact: true });
  if (await save.isVisible()) {
    await save.click();
  } else {
    await actions.getByRole("button", { name: "その他の操作" }).click();
    await page.getByRole("menuitem", { name: "保存", exact: true }).click();
  }

  const banner = page.getByTestId("business-view-save-error");
  await expect(banner.getByRole("alert")).toHaveText(message);
  await expect(banner).toBeInViewport();
  await expect.poll(() => attempts).toBe(1);
  // 同じ失敗を Toast とフォームの下に重ねない。
  await expect(page.getByText(message)).toHaveCount(1);
  // 入力は残り、未保存のまま（離脱の確認の対象）。
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveValue("経費と出張の相談");
  await expectNoPageOverflow(page);
});

test("URL の業務ビューが存在しないときは別の対象へ置き換えず、一覧へ戻る導線を出す", async ({ page }) => {
  await mockBusinessViews(page, [accountingView]);
  await page.goto("/business-views?id=bv-missing");

  await expect(page.getByText("対象が見つかりません")).toBeVisible();
  await expect(page.getByText("「bv-missing」は削除されたか、存在しません。")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveCount(0);
  await expect(page).toHaveURL(/\?id=bv-missing$/);
  await expectNoPageOverflow(page);

  await page.getByRole("button", { name: "一覧へ戻る" }).click();
  await expect(page).toHaveURL(/\/business-views$/);
  await expect(page.getByTestId("business-view-row-bv-1")).toBeVisible();
});

test("エディタからアーカイブすると確認のうえ一覧へ置き換えて戻る", async ({ page }) => {
  let archived = false;
  await mockBusinessViews(page, [accountingView], undefined, () => {
    archived = true;
  });
  await page.goto("/business-views");
  await page.getByRole("link", { name: "経理ビュー を編集" }).click();
  await expect(page).toHaveURL(/\?id=bv-1$/);

  await page
    .getByTestId("business-view-detail-actions")
    .getByRole("button", { name: "その他の操作" })
    .click();
  await page.getByRole("menuitem", { name: "アーカイブ" }).click();
  const dialog = page.getByRole("alertdialog", { name: "業務ビューをアーカイブしますか?" });
  await dialog.getByRole("button", { name: "アーカイブ" }).click();
  await expect.poll(() => archived).toBe(true);
  await expect(page).toHaveURL(/\/business-views$/);
  // アーカイブ後は replace で一覧へ戻るため、戻るで消えた対象のエディタへ戻らない。
  await page.goBack();
  await expect(page).toHaveURL(/\/business-views$/);
});

// #555: エディタの PageHeader に状態と件数・更新日時を出し、アーカイブ済みは入力できないようにする。
test("エディタの見出しに状態と参照 KB の件数を出し、アーカイブ済みは読み取り専用で保存できない", async ({
  page,
}) => {
  await mockBusinessViews(page, [
    accountingView,
    { ...accountingView, id: "bv-old", name: "旧ビュー", status: "ARCHIVED" },
  ]);
  await page.goto("/business-views?id=bv-1");
  const header = page.locator("header[data-page-header]");
  await expect(header.getByText("有効")).toBeVisible();
  await expect(page.getByTestId("business-view-meta")).toContainText("参照 KB 1 件");

  await page.goto("/business-views?id=bv-old");
  await expect(header.getByText("アーカイブ済み")).toBeVisible();
  await expect(page.getByText("アーカイブ済みの業務ビューは編集・保存できません。")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveAttribute("readonly", "");
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveAttribute("readonly", "");
  await expect(page.locator("[data-page-header-actions]").getByRole("button", { name: "保存", exact: true })).toBeDisabled();
  await expect(
    page.getByRole("group", { name: "安全チェック" }).getByRole("button", { name: "業務ビューで上書き" })
  ).toBeDisabled();
  await expectNoPageOverflow(page);
});

test("業務ビューが無いときは、空の状態から作成エディタへ進める", async ({ page }) => {
  await mockBusinessViews(page, []);
  await page.goto("/business-views");
  await expect(page.getByText("業務ビューがありません")).toBeVisible();
  await page.getByRole("button", { name: "最初の業務ビューを作成" }).click();
  await expect(page).toHaveURL(/\/business-views\?id=new$/);
});

/**
 * エディタの「一覧へ戻る」。ページの左上（タイトルの上）にあり、375px でも 1 タップで押せる。
 * 右の操作の列（変更を破棄・保存）は 2 つだけなので畳まない（#582 / #618）。
 */
async function clickBackToList(page: Page) {
  const actions = page.getByRole("group", { name: "ページ操作" });
  await expect(actions.getByRole("button", { name: /^その他の操作/ })).toHaveCount(0);
  await expect(actions.getByTestId("editor-back")).toHaveCount(0);
  await page.getByTestId("editor-back").click();
}

interface BusinessViewSummaryFixture {
  id: string;
  name: string;
  description: string | null;
  status: string;
  knowledge_base_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

async function mockBusinessViews(
  page: Page,
  items: BusinessViewSummaryFixture[],
  onCreate?: (body: Record<string, unknown>) => void,
  onArchive?: (id: string) => void
) {
  const details = new Map<string, Record<string, unknown>>(
    items.map((item) => [
      item.id,
      {
        ...item,
        config: {
          version: 1,
          knowledge_base_ids: ["kb-1"],
          query: {
            guardrail_policy: null,
          },
          serving_mode: "fused",
        },
        knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
      },
    ])
  );
  const envelope = (data: unknown) => ({ json: { data, error_messages: [], warning_messages: [] } });
  await page.route("**/api/business-views**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    const id = pathname.split("/")[3];
    if (request.method() === "POST" && pathname === "/api/business-views") {
      const body = request.postDataJSON() as Record<string, unknown>;
      onCreate?.(body);
      const created = {
        id: "bv-new",
        name: body.name,
        description: body.description ?? null,
        status: "ACTIVE",
        knowledge_base_count:
          (body.config as { knowledge_base_ids?: string[] })?.knowledge_base_ids?.length ?? 0,
        config: body.config,
        knowledge_bases: [],
        created_at: "2026-06-19T00:00:00Z",
        updated_at: "2026-06-19T00:00:00Z",
        archived_at: null,
      };
      details.set("bv-new", created);
      await route.fulfill(envelope(created));
      return;
    }
    if (pathname.endsWith("/archive")) {
      onArchive?.(id);
      await route.fulfill(envelope({ ...details.get(id), status: "ARCHIVED" }));
      return;
    }
    if (pathname.endsWith("/domain-keywords")) {
      await route.fulfill(envelope({ business_view_id: id, keywords: [] }));
      return;
    }
    if (pathname.endsWith("/approved-faq")) {
      await route.fulfill(envelope({ business_view_id: id, records: [] }));
      return;
    }
    if (pathname.endsWith("/runtime-knowledge")) {
      await route.fulfill(envelope({ business_view_id: id, terms: [], rules: [] }));
      return;
    }
    if (id) {
      const detail = details.get(id);
      if (!detail) {
        await route.fulfill({
          status: 404,
          json: { data: null, error_messages: ["業務ビューが見つかりません。"], warning_messages: [] },
        });
        return;
      }
      await route.fulfill(envelope(detail));
      return;
    }
    await route.fulfill(
      envelope({ items, total: items.length, limit: 50, offset: 0, has_next: false })
    );
  });
}

async function mockKnowledgeBases(page: Page) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: [
            {
              id: "kb-default",
              name: "DEFAULT",
              description: null,
              status: "ACTIVE",
              default_search_mode: "hybrid",
              document_count: 0,
              indexed_document_count: 0,
              error_document_count: 0,
              searchable_chunk_count: 0,
              created_at: "2026-06-30T00:00:00Z",
              updated_at: "2026-06-30T00:00:00Z",
              archived_at: null,
            },
            {
              id: "kb-1",
              name: "社内規程",
              description: "経費・人事・情報管理",
              status: "ACTIVE",
              default_search_mode: "hybrid",
              document_count: 3,
              indexed_document_count: 3,
              error_document_count: 0,
              searchable_chunk_count: 16,
              created_at: "2026-06-15T00:00:00Z",
              updated_at: "2026-06-15T00:00:00Z",
              archived_at: null,
            },
          ],
          total: 1,
          limit: 50,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function mockDefaultBusinessView(
  page: Page,
  onUpdate: (body: Record<string, unknown>) => void
) {
  const summary: BusinessViewSummaryFixture = {
    id: "bv-default",
    name: "DEFAULT",
    description: null,
    status: "ACTIVE",
    knowledge_base_count: 1,
    created_at: "2026-06-30T00:00:00Z",
    updated_at: "2026-06-30T00:00:00Z",
    archived_at: null,
  };
  const config = {
    version: 1,
    knowledge_base_ids: ["kb-default"],
    query: {
      guardrail_policy: null,
    },
    serving_mode: "single",
  };

  await page.route("**/api/business-views**", async (route) => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "PATCH") {
      const body = request.postDataJSON() as Record<string, unknown>;
      onUpdate(body);
      await route.fulfill({
        json: {
          data: { ...summary, description: body.description, config: body.config, knowledge_bases: [] },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    if (pathname.endsWith("/bv-default")) {
      await route.fulfill({
        json: {
          data: {
            ...summary,
            config,
            knowledge_bases: [{ id: "kb-default", name: "DEFAULT" }],
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        data: { items: [summary], total: 1, limit: 50, offset: 0, has_next: false },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

function searchStreamBody(answer = "上限額を確認しました。"): string {
  return [
    `event: metadata\ndata: ${JSON.stringify({
      trace_id: "trace-bv",
      elapsed_ms: 10,
      guardrail_warnings: [],
      diagnostics: {
        retrieval_strategy: "hybrid",
        retrieval_strategy_adapter: "grounded",
        filter_keys: [],
        knowledge_base_count: 1,
        business_view_applied: "bv-1",
        config_fingerprint: "fp-bv",
      },
    })}\n\n`,
    `event: delta\ndata: ${JSON.stringify({ text: answer })}\n\n`,
    `event: citations\ndata: ${JSON.stringify([])}\n\n`,
    `event: done\ndata: ${JSON.stringify({ trace_id: "trace-bv" })}\n\n`,
  ].join("");
}
