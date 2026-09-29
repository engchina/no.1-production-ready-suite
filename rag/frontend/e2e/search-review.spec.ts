import { expect, type Page, type Route, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

/**
 * RAG 検索のレビューで直した不具合の回帰テスト（#285）。
 * stream の途中の失敗・複数の業務ビューでの評価先・IME の確定の Enter・画面を離れたときの中断・
 * 類似 FAQ を飛ばした後の再試行。
 */

const envelope = (data: unknown) => ({ json: { data, error_messages: [], warning_messages: [] } });

const VIEWS = [
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
    knowledge_base_count: 1,
    created_at: "2026-06-19T00:00:00Z",
    updated_at: "2026-06-19T00:00:00Z",
    archived_at: null,
  },
];

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockSearchPage(page);
});

async function mockSearchPage(
  page: Page,
  { faqSuggestions = [] as unknown[] }: { faqSuggestions?: unknown[] } = {}
) {
  await page.route("**/api/business-views**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/approved-faq/suggest")) {
      await route.fulfill(envelope({ suggestions: faqSuggestions }));
      return;
    }
    if (pathname.endsWith("/query-suggestions")) {
      await route.fulfill(envelope({ suggestions: [] }));
      return;
    }
    await route.fulfill(
      envelope({ items: VIEWS, total: VIEWS.length, limit: 50, offset: 0, has_next: false })
    );
  });
  await page.route("**/api/search/answers**", (route) =>
    route.fulfill(envelope({ items: [], total: 0, limit: 10, offset: 0, has_next: false }))
  );
  await page.route("**/api/settings/answer-records", (route) =>
    route.fulfill(envelope({ retention_days: 30 }))
  );
  await page.route("**/api/feedback/current**", (route) => route.fulfill(envelope([])));
}

function sse(events: Array<[string, unknown]>): string {
  return events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
}

async function fulfillStream(route: Route, body: string) {
  await route.fulfill({ status: 200, headers: { "content-type": "text/event-stream" }, body });
}

async function selectViews(page: Page, names: RegExp[]) {
  await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
  const list = page.getByRole("listbox", { name: /対象の業務ビュー/ });
  for (const name of names) await list.getByRole("option", { name }).click();
  await page.keyboard.press("Escape");
}

async function search(page: Page, query: string) {
  await page.getByRole("textbox", { name: "RAG 検索" }).fill(query);
  await page.getByRole("button", { name: "検索", exact: true }).click();
}

test("stream の途中の timeout は該当なしではなくエラーとして再試行を出す", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/search/stream", async (route) => {
    calls += 1;
    await fulfillStream(
      route,
      sse([
        ["stage", { trace_id: "t1", stage: "retrieval", outcome: "started", elapsed_ms: 0, attributes: {} }],
        [
          "error",
          {
            trace_id: "t1",
            message: "検索処理がタイムアウトしました。条件を絞って再度お試しください。",
            error_type: "TimeoutError",
          },
        ],
      ])
    );
  });

  await page.goto("/search");
  await selectViews(page, [/経理ビュー/]);
  await search(page, "交通費の上限");

  const alert = page.getByRole("alert").filter({ hasText: "タイムアウトしました" });
  await expect(alert).toBeVisible();
  await expect(page.getByText("一致する根拠が見つかりませんでした。")).toHaveCount(0);
  await expectNoPageOverflow(page);

  await alert.getByRole("button", { name: "再試行" }).click();
  await expect.poll(() => calls).toBe(2);
});

test("done を受けずに途切れた stream は途中終了のエラーにする", async ({ page }) => {
  await page.route("**/api/search/stream", (route) =>
    fulfillStream(
      route,
      sse([
        ["metadata", { trace_id: "t2", elapsed_ms: 5, guardrail_warnings: [], diagnostics: {} }],
        ["delta", { text: "途中まで" }],
      ])
    )
  );

  await page.goto("/search");
  await selectViews(page, [/経理ビュー/]);
  await search(page, "交通費の上限");

  await expect(page.getByRole("alert").filter({ hasText: "回答の受信が途中で途切れました" })).toBeVisible();
});

test("複数の業務ビューで検索した回答の評価は先頭の業務ビューへ送る", async ({ page }) => {
  await page.route("**/api/search/stream", (route) =>
    fulfillStream(
      route,
      sse([
        [
          "metadata",
          {
            trace_id: "t3",
            elapsed_ms: 5,
            guardrail_warnings: [],
            // backend は複数選択のとき適用した業務ビューを "a,b" で返す。
            diagnostics: { business_view_applied: "bv-1,bv-2" },
          },
        ],
        ["delta", { text: "上限は 5,000 円です。" }],
        ["citations", []],
        ["done", { trace_id: "t3" }],
      ])
    )
  );
  let feedbackBody: Record<string, unknown> | null = null;
  await page.route("**/api/feedback", async (route) => {
    feedbackBody = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill(
      envelope({
        feedback_id: "f1",
        trace_id: "t3",
        business_view_id: feedbackBody.business_view_id,
        target_type: "answer",
        source_surface: "search",
        document_id: null,
        chunk_id: null,
        message_id: null,
        rating: "helpful",
        reason: null,
        comment: null,
        corrected_answer: null,
      })
    );
  });

  await page.goto("/search");
  await selectViews(page, [/経理ビュー/, /人事ビュー/]);
  await search(page, "交通費の上限");
  await expect(page.getByText("上限は 5,000 円です。")).toBeVisible();

  await page.getByRole("button", { name: "この回答は役に立った" }).click();
  await expect.poll(() => feedbackBody?.business_view_id).toBe("bv-1");
});

test("IME の変換を確定する Enter では検索しない", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/search/stream", async (route) => {
    calls += 1;
    await fulfillStream(route, sse([["done", { trace_id: "t4" }]]));
  });

  await page.goto("/search");
  await selectViews(page, [/経理ビュー/]);
  const input = page.getByRole("textbox", { name: "RAG 検索" });
  await input.fill("こうつうひ");
  // 変換中の Enter（isComposing=true / keyCode=229）。
  await input.dispatchEvent("keydown", { key: "Enter", keyCode: 229, isComposing: true, bubbles: true });
  await page.waitForTimeout(300);
  expect(calls).toBe(0);

  await input.press("Enter");
  await expect.poll(() => calls).toBe(1);
});

test("回答の生成中に画面を離れたら検索の stream を止める", async ({ page }) => {
  let aborted = false;
  page.on("requestfailed", (request) => {
    if (request.url().includes("/api/search/stream")) aborted = true;
  });
  // 応答を返さず、生成中のままにする。
  await page.route("**/api/search/stream", () => new Promise<void>(() => undefined));

  await page.goto("/search");
  await selectViews(page, [/経理ビュー/]);
  await search(page, "交通費の上限");
  await expect(page.getByRole("button", { name: "停止" })).toBeVisible();

  // SPA 内の遷移（再読み込みではない）。
  await page.evaluate(() => {
    window.history.pushState({}, "", "/business-views");
    window.dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect.poll(() => aborted).toBe(true);
});

test("類似 FAQ を使わずに生成した検索の再試行は FAQ を出し直さない", async ({ page }) => {
  await page.unrouteAll();
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockSearchPage(page, {
    faqSuggestions: [
      { id: "faq-1", question: "交通費の上限は？", answer: "5,000 円です。", score: 0.8, direct: false },
    ],
  });
  let calls = 0;
  await page.route("**/api/search/stream", async (route) => {
    calls += 1;
    await route.fulfill({
      status: 429,
      json: {
        data: null,
        error_messages: ["リクエスト数が上限を超えました。しばらく待ってから再度お試しください。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/search");
  await selectViews(page, [/経理ビュー/]);
  await search(page, "交通費の上限");
  await page.getByRole("button", { name: "類似問を使用しない（通常の回答生成）" }).click();

  const alert = page.getByRole("alert").filter({ hasText: "リクエスト数が上限を超えました" });
  await expect(alert).toBeVisible();
  await alert.getByRole("button", { name: "再試行" }).click();

  await expect.poll(() => calls).toBe(2);
  await expect(page.getByText("類似する承認済み FAQ があります")).toHaveCount(0);
});

test("業務ビューの読み込み中は読み込み中の状態として読み上げる", async ({ page }) => {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/business-views?**", async (route) => {
    await gate;
    await route.fallback();
  });

  await page.goto("/search");
  // 読み込み中は文言と経過時間（TimedLoadingState）を出し、状態として読み上げる（#265）。
  const loading = page.getByTestId("search-business-views-loading");
  await expect(loading).toBeVisible();
  await expect(loading.getByRole("timer")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "業務ビューを読み込んでいます。" })).toHaveCount(1);
  await expect(page.getByText("業務ビューを作成してください")).toHaveCount(0);
  release();
  await expect(page.getByRole("combobox", { name: /対象の業務ビュー/ })).toBeVisible();
});

test("詳細条件は条件を設定したままでも閉じられ、閉じると「設定中」を出す（#461）", async ({ page }) => {
  await page.goto("/search");
  await selectViews(page, [/経理ビュー/]);
  const advanced = page.getByRole("button", { name: /詳細条件/ });
  await expect(advanced).toHaveAttribute("aria-expanded", "false");

  await advanced.click();
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("combobox", { name: "内容種別" }).click();
  await page.getByRole("option", { name: "表", exact: true }).click();

  // 条件があっても閉じられる。閉じている間は「設定中」で条件が効いていることを示す。
  await advanced.click();
  await expect(advanced).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("combobox", { name: "内容種別" })).toHaveCount(0);
  await expect(advanced).toContainText("設定中");

  // キーボード（Space / Enter）でも開閉できる。
  await advanced.focus();
  await page.keyboard.press("Space");
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("combobox", { name: "内容種別" })).toContainText("表");
  await expect(advanced).not.toContainText("設定中");
  await page.keyboard.press("Enter");
  await expect(advanced).toHaveAttribute("aria-expanded", "false");
  await expectNoPageOverflow(page);
});

test("検索のボタンは詳細条件の下にあり、実行中は同じ位置・同じ要素のまま「停止」になる（#413）", async ({ page }) => {
  let calls = 0;
  let aborted = 0;
  page.on("requestfailed", (request) => {
    if (request.url().includes("/api/search/stream")) aborted += 1;
  });
  // 応答を返さず、生成中のままにする。
  await page.route("**/api/search/stream", () => {
    calls += 1;
    return new Promise<void>(() => undefined);
  });

  await page.goto("/search");
  await selectViews(page, [/経理ビュー/]);
  const input = page.getByRole("textbox", { name: "RAG 検索" });
  await input.fill("交通費の上限");

  // 配置: フォームの最後（詳細条件の下）。
  const button = page.getByTestId("search-run-stop");
  const advanced = page.getByRole("button", { name: "詳細条件" });
  const [buttonBox, advancedBox] = await Promise.all([button.boundingBox(), advanced.boundingBox()]);
  if (!buttonBox || !advancedBox) throw new Error("ボタンの位置を計測できません。");
  expect(buttonBox.y).toBeGreaterThan(advancedBox.y + advancedBox.height);
  await expect(button).toHaveAccessibleName("検索");
  await expect(page.getByRole("button", { name: "停止" })).toHaveCount(0);

  // キーボードで実行 → 同じ要素のまま「停止」になり、フォーカスと位置が変わらない。
  const marker = await button.evaluate((element) => {
    (element as HTMLElement).dataset.e2eMarker = "same-element";
    return true;
  });
  expect(marker).toBe(true);
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(button).toHaveAccessibleName("停止");
  await expect(button).toHaveAttribute("data-e2e-marker", "same-element");
  await expect(button).toBeFocused();
  await expect(button).not.toHaveAttribute("aria-disabled", "true");
  const runningBox = await button.boundingBox();
  expect(runningBox?.x).toBe(buttonBox.x);
  expect(runningBox?.y).toBe(buttonBox.y);
  expect(runningBox?.width).toBe(buttonBox.width);
  // 実行中に押せる操作は 1 つだけ（押せない「検索」が並ばない）。
  await expect(page.getByRole("button", { name: "検索", exact: true })).toHaveCount(0);
  expect(calls).toBe(1);

  // 実行中の質問欄の Enter では停止しない（二重に送信もしない）。
  await input.press("Enter");
  await page.waitForTimeout(300);
  await expect(button).toHaveAccessibleName("停止");
  expect(calls).toBe(1);
  expect(aborted).toBe(0);

  // 停止で中断する。フォーカスはボタンに残る。
  await button.focus();
  await page.keyboard.press("Enter");
  await expect(button).toHaveAccessibleName("検索");
  await expect(button).toBeFocused();
  await expect(page.getByText("検索ストリームを停止しました。")).toBeVisible();
  await expect.poll(() => aborted).toBe(1);

  // 停止の後に再び実行できる（ダブルクリックでも 1 回だけ実行し、停止しない）。
  await button.dblclick();
  await expect.poll(() => calls).toBe(2);
  await expect(button).toHaveAccessibleName("停止");
  await page.waitForTimeout(300);
  await expect(button).toHaveAccessibleName("停止");
  expect(aborted).toBe(1);
  await expectNoPageOverflow(page);
});
