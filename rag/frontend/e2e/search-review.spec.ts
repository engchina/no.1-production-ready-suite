import { expect, type Page, type Route, test } from "@playwright/test";

import {
  enableSearchAnswer,
  expectNoPageOverflow,
  mockDatabaseReady,
  mockLocalAuth,
  selectSearchAnswerProfile,
} from "./_helpers";

/**
 * RAG 検索のレビューで直した不具合の回帰テスト（#285）。
 * stream の途中の失敗・回答の評価先の検索・回答プロファイル・IME の確定の Enter・画面を離れたときの中断・
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
  await page.route("**/api/search-answer-profiles**", async (route) => {
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

async function search(page: Page, query: string) {
  await enableSearchAnswer(page);
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
  await selectSearchAnswerProfile(page, /経理ビュー/);
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
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await search(page, "交通費の上限");

  await expect(page.getByRole("alert").filter({ hasText: "回答の受信が途中で途切れました" })).toBeVisible();
});

test("回答の評価は検索したときの検索・回答プロファイルへ送る（検索後に選択を変えても変えない）", async ({ page }) => {
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
            diagnostics: { search_answer_profile_applied: "bv-1" },
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
        search_answer_profile_id: feedbackBody.search_answer_profile_id,
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
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await search(page, "交通費の上限");
  await expect(page.getByText("上限は 5,000 円です。")).toBeVisible();
  await selectSearchAnswerProfile(page, /人事ビュー/);

  await page.getByRole("button", { name: "この回答は役に立った" }).click();
  await expect.poll(() => feedbackBody?.search_answer_profile_id).toBe("bv-1");
});

test("IME の変換を確定する Enter では検索しない", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/search/stream", async (route) => {
    calls += 1;
    await fulfillStream(route, sse([["done", { trace_id: "t4" }]]));
  });

  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
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
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await search(page, "交通費の上限");
  await expect(page.getByRole("button", { name: "停止" })).toBeVisible();

  // SPA 内の遷移（再読み込みではない）。
  await page.evaluate(() => {
    window.history.pushState({}, "", "/search-answer-profiles");
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
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await search(page, "交通費の上限");
  await page.getByRole("button", { name: "類似問を使用しない" }).click();

  const alert = page.getByRole("alert").filter({ hasText: "リクエスト数が上限を超えました" });
  await expect(alert).toBeVisible();
  await alert.getByRole("button", { name: "再試行" }).click();

  await expect.poll(() => calls).toBe(2);
  await expect(page.getByText("類似する承認済み FAQ があります")).toHaveCount(0);
});

test("質問から読み取った条件を「自動」のチップで出し、外して検索し直せる（#652）", async ({ page }) => {
  const requests: Array<Record<string, unknown>> = [];
  await page.route("**/api/search/stream", (route) => {
    requests.push(route.request().postDataJSON() as Record<string, unknown>);
    const first = requests.length === 1;
    const answer = {
      execution_steps: [],
      evidence_tree: [],
      ...(first
        ? {
            auto_field_filter: {
              conditions: [
                { name: "契約日", value_type: "date", op: "gte", value: "2025-01-01" },
                { name: "金額", value_type: "number", op: "gte", value: "100000" },
              ],
              relaxed: true,
            },
          }
        : {}),
    };
    return fulfillStream(
      route,
      sse([
        ["metadata", { trace_id: `t${requests.length}`, elapsed_ms: 5, guardrail_warnings: [], diagnostics: { answer } }],
        ["citations", []],
        ["done", { trace_id: `t${requests.length}` }],
      ])
    );
  });

  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
  await page.getByRole("textbox", { name: "RAG 検索" }).fill("2025年以降の10万円以上の契約");
  await page.getByRole("button", { name: "検索", exact: true }).click();

  const chips = page.getByTestId("auto-field-filter-chips");
  await expect(chips.getByText("自動: 契約日 ≥ 2025-01-01")).toBeVisible();
  await expect(chips.getByText("自動: 金額 ≥ 100000")).toBeVisible();
  // 読み取った条件で見つからず、外して検索したことを示す。
  await expect(chips.getByRole("status")).toContainText("条件を外して検索しました");
  await expectNoPageOverflow(page);

  await chips.getByRole("button", { name: "金額 ≥ 100000 を外して検索し直す" }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toMatchObject({
    query: "2025年以降の10万円以上の契約",
    auto_field_filter_excluded: ["金額"],
  });
  expect("auto_field_filter_excluded" in requests[0]).toBe(false);
  await expect(page.getByTestId("auto-field-filter-chips")).toHaveCount(0);
});

test("検索・回答プロファイルの読み込み中は読み込み中の状態として読み上げる", async ({ page }) => {
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/search-answer-profiles?**", async (route) => {
    await gate;
    await route.fallback();
  });

  await page.goto("/search");
  // 読み込み中は文言と経過時間（TimedLoadingState）を出し、状態として読み上げる（#265）。
  const loading = page.getByTestId("search-search-answer-profiles-loading");
  await expect(loading).toBeVisible();
  await expect(loading.getByRole("timer")).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "検索・回答プロファイルを読み込んでいます。" })).toHaveCount(1);
  await expect(page.getByText("検索・回答プロファイルを作成してください")).toHaveCount(0);
  release();
  await expect(page.getByRole("button", { name: /検索・回答プロファイル/ })).toBeVisible();
});

test("詳細条件は条件を設定したままでも閉じられ、閉じると「設定中」を出す（#461）", async ({ page }) => {
  await page.goto("/search");
  await selectSearchAnswerProfile(page, /経理ビュー/);
  const advanced = page.getByRole("button", { name: /詳細条件/ });
  await expect(advanced).toHaveAttribute("aria-expanded", "false");

  await advanced.click();
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("combobox", { name: "候補取得数" }).click();
  await page.getByRole("option", { name: "50", exact: true }).click();

  // 条件があっても閉じられる。閉じている間は「設定中」で条件が効いていることを示す。
  await advanced.click();
  await expect(advanced).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("combobox", { name: "候補取得数" })).toHaveCount(0);
  await expect(advanced).toContainText("設定中");

  // キーボード（Space / Enter）でも開閉できる。
  await advanced.focus();
  await page.keyboard.press("Space");
  await expect(advanced).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("combobox", { name: "候補取得数" })).toContainText("50");
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
  await selectSearchAnswerProfile(page, /経理ビュー/);
  const input = page.getByRole("textbox", { name: "RAG 検索" });
  await input.fill("交通費の上限");

  // 配置: フォームの最後（詳細条件の下）。
  const button = page.getByTestId("search-run-stop");
  const advanced = page.getByRole("button", { name: "詳細条件" });
  const [buttonBox, advancedBox] = await Promise.all([button.boundingBox(), advanced.boundingBox()]);
  if (!buttonBox || !advancedBox) throw new Error("ボタンの位置を計測できません。");
  expect(buttonBox.y).toBeGreaterThan(advancedBox.y + advancedBox.height);
  // 質問欄は検索のボタンと同じ行（詳細条件の下）で、チャットの入力欄と同じ 2 行の複数行入力（Shift+Enter で改行）。
  const inputBox = await input.boundingBox();
  if (!inputBox) throw new Error("質問欄の位置を計測できません。");
  expect(inputBox.y).toBeGreaterThan(advancedBox.y + advancedBox.height);
  await expect(input).toHaveJSProperty("tagName", "TEXTAREA");
  await expect(input).toHaveAttribute("rows", "2");
  await input.press("Shift+Enter");
  await expect(input).toHaveValue("交通費の上限\n");
  expect(calls).toBe(0);
  await input.fill("交通費の上限");
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
