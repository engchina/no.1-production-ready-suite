// 実行履歴（#1955。UX 契約 run-history.md §7）: 共通の一覧（RunHistoryToolbar → RunHistoryTable → OffsetPagination）と
// `?id=<run_id>` の全幅の詳細。状態は `[data-run-status]` の値で確かめる（文言・色に頼らない）。
import { expectLocalUiFonts } from "./_helpers/local-fonts";
import { expect, test, type Locator, type Page, type Route } from "./_helpers/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { chooseSelectFieldOption } from "./_helpers/select-field";
import {
  findClippedPlaceholders,
  findToolbarMisalignment,
  jumpToPage,
  measureHorizontalOverflow,
} from "../../../../platform/packages/ui/e2e/layout-checks";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

// 各主要導線の最終状態で全テキスト・入力欄の字体継承を確認する。
test.afterEach(async ({ page }, testInfo) => {
  if (testInfo.status === "skipped") return;
  await expectLocalUiFonts(page);
});

async function fulfillJson(route: Route, data: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(status === 200 ? { data } : { detail: data }),
  });
}

type HistoryRecord = Record<string, unknown> & { id: string };

const historyItems: HistoryRecord[] = [
  {
    id: "history-new",
    question: "未入金の顧客を確認",
    engine: "select_ai_agent",
    generated_sql: "SELECT CUSTOMER_NAME FROM INVOICES WHERE PAID_AT IS NULL",
    executable_sql: "SELECT CUSTOMER_NAME FROM INVOICES WHERE PAID_AT IS NULL FETCH FIRST 100 ROWS ONLY",
    created_at: "2026-06-22T10:00:00.000Z",
    elapsed_ms: 250,
    generation_elapsed_ms: 180,
    engine_timings: [
      { engine: "select_ai_agent", elapsed_ms: 80, status: "failed", error: "team execution failed" },
      { engine: "enterprise_ai_direct", elapsed_ms: 100, status: "success", error: "" },
    ],
    stage_timings: [
      { stage: "prepare_context", elapsed_ms: 20 },
      { stage: "generate_sql", elapsed_ms: 190 },
      { stage: "safety_check", elapsed_ms: 15 },
      { stage: "execute_sql", elapsed_ms: 25 },
    ],
    feedback_rating: "good",
    profile_id: "finance",
    profile_name: "経理プロファイル",
    rewritten_question: "未入金顧客",
    safety_is_safe: true,
    result_row_count: 2,
    result_columns: ["CUSTOMER_NAME"],
    feedback_comment: "期待通りです",
    actor_user_uuid: "me",
    executed_by_me: true,
    executor_display_name: "システム管理者",
    conversation_owner_display_name: "システム管理者",
    owned_by_me: true,
    kind: "chat",
  },
  {
    id: "history-middle",
    question: "請求金額を確認",
    engine: "select_ai",
    generated_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    executable_sql: "SELECT TOTAL_AMOUNT FROM INVOICES",
    created_at: "2026-06-21T10:00:00.000Z",
    elapsed_ms: 120,
    feedback_rating: "bad",
    profile_id: "default",
    profile_name: "既定プロファイル",
    rewritten_question: "請求金額一覧",
    safety_is_safe: true,
    result_row_count: 4,
    result_columns: ["TOTAL_AMOUNT"],
    feedback_comment: "集計条件が違います",
    actor_user_uuid: "me",
    executed_by_me: true,
    executor_display_name: "システム管理者",
    conversation_owner_display_name: null,
    owned_by_me: null,
    kind: "query",
  },
  {
    id: "history-old",
    question: "監査ログを削除",
    engine: "select_ai",
    generated_sql: "DELETE FROM AUDIT_LOG",
    executable_sql: "",
    created_at: "2026-06-20T10:00:00.000Z",
    elapsed_ms: 30,
    stage_timings: [
      { stage: "generate_sql", elapsed_ms: 20 },
      { stage: "safety_check", elapsed_ms: 10 },
    ],
    feedback_rating: null,
    profile_id: "audit",
    profile_name: "監査プロファイル",
    rewritten_question: "",
    safety_is_safe: false,
    result_row_count: 0,
    result_columns: [],
    feedback_comment: "",
    actor_user_uuid: "me",
    executed_by_me: true,
    executor_display_name: "システム管理者",
    conversation_owner_display_name: null,
    owned_by_me: null,
    kind: "query",
  },
];

function manyItems(count: number): HistoryRecord[] {
  return Array.from({ length: count }, (_, index) => ({
    ...historyItems[1],
    id: `history-${String(index + 1).padStart(3, "0")}`,
    question: `ページ確認の質問 ${index + 1}`,
    created_at: new Date(Date.UTC(2026, 5, 1, 0, 0, count - index)).toISOString(),
  }));
}

interface MockOptions {
  items?: readonly HistoryRecord[];
  othersVisible?: boolean;
  requests?: URL[];
  /** 一覧の応答を待たせる（読み込み中を確かめる）。 */
  gate?: Promise<void>;
  /** 一覧の取得を失敗させる。 */
  fail?: () => boolean;
}

/** 種類の絞り込み（backend の `history_kind_filters`。`not_evaluation` は品質評価を除く。#1986）。 */
function kindMatches(item: HistoryRecord, kind: string) {
  if (kind === "all") return true;
  if (kind === "not_evaluation") return item.kind !== "evaluation";
  return item.kind === kind;
}

/** backend と同じく、絞り込み（q・status・kind・since・executor）→ 新しい順 → offset / limit で返す。 */
function historyPage(url: URL, items: readonly HistoryRecord[], othersVisible: boolean) {
  const q = (url.searchParams.get("q") ?? "").trim().toLocaleLowerCase("ja-JP");
  const status = url.searchParams.get("status") ?? "all";
  const kind = url.searchParams.get("kind") ?? "all";
  const since = url.searchParams.get("since");
  const executor = url.searchParams.get("executor") ?? "all";
  const filtered = items.filter((item) => {
    if (status === "succeeded" && item.safety_is_safe !== true) return false;
    if (status === "blocked" && item.safety_is_safe !== false) return false;
    if (!kindMatches(item, kind)) return false;
    if (since && String(item.created_at) < since) return false;
    if (executor === "mine" && item.executed_by_me === false) return false;
    if (executor === "others" && item.executed_by_me !== false) return false;
    const text = [item.question, item.generated_sql, item.feedback_comment].join("\n").toLocaleLowerCase("ja-JP");
    return !q || text.includes(q);
  });
  const sorted = [...filtered].sort((a, b) => (String(a.created_at) < String(b.created_at) ? 1 : -1));
  const limit = Number(url.searchParams.get("limit") ?? "25");
  const offset = Number(url.searchParams.get("offset") ?? "0");
  const pageItems = sorted.slice(offset, offset + limit);
  // 「全 M 件」は種類の範囲で数える（品質評価を含まない種類なら品質評価を除く。#1986）。
  const evaluations = items.filter((item) => item.kind === "evaluation").length;
  const unfilteredTotal = kind === "all" || kind === "evaluation" ? items.length : items.length - evaluations;
  return {
    items: pageItems,
    total: sorted.length,
    limit,
    offset,
    has_next: offset + pageItems.length < sorted.length,
    unfiltered_total: unfilteredTotal,
    others_visible: othersVisible,
    hidden_evaluation_total: kind === "not_evaluation" && unfilteredTotal === 0 ? evaluations : 0,
  };
}

async function mockHistory(page: Page, options: MockOptions = {}) {
  const items = options.items ?? historyItems;
  await page.route("**/api/nl2sql/history**", async (route) => {
    const url = new URL(route.request().url());
    const detail = /\/api\/nl2sql\/history\/([^/?]+)$/.exec(url.pathname);
    if (detail) {
      const item = items.find((entry) => entry.id === decodeURIComponent(detail[1]));
      return item ? fulfillJson(route, item) : fulfillJson(route, "実行履歴が見つかりません。", 404);
    }
    options.requests?.push(url);
    if (options.gate) await options.gate;
    if (options.fail?.()) return fulfillJson(route, "履歴更新テストエラー", 503);
    return fulfillJson(route, historyPage(url, items, options.othersVisible ?? false));
  });
}

function table(page: Page) {
  return page.getByTestId("history-table");
}

function row(page: Page, id: string) {
  return page.getByTestId(`history-table-row-${id}`);
}

function titleLink(page: Page, question: string) {
  return page.getByRole("link", { name: `${question}（詳細を開く）`, exact: true });
}

/** 375px は絞り込みをシートにまとめる（3 つ以上）。広い画面はツールバーの中の選択欄。 */
async function chooseFilter(page: Page, name: "status" | "kind" | "period" | "executor", value: string) {
  const toolbar = page.getByTestId("history-toolbar");
  const sheetMode = (await toolbar.getAttribute("data-list-toolbar-filters-layout")) === "sheet";
  if (sheetMode) await toolbar.getByRole("button", { name: /^絞り込み/ }).click();
  await chooseSelectFieldOption(page.getByTestId(`history-toolbar-${name}`), value);
  if (sheetMode) await page.keyboard.press("Escape");
}

async function expectAlignedHeader(page: Page) {
  const title = await page.getByRole("heading", { level: 1 }).boundingBox();
  const card = await page.getByTestId("history-list-card").boundingBox();
  expect(title).not.toBeNull();
  expect(card).not.toBeNull();
  expect(Math.abs(title!.x - card!.x)).toBeLessThanOrEqual(1);
}

test("実行履歴は共通の表で質問・状態・種類・開始日時・所要時間を並べ、状態を data-run-status で示す", { tag: "@responsive" }, async ({ page, isMobile }, testInfo) => {
  await mockHistory(page);
  await page.goto("/history");
  await expect(page.getByRole("heading", { level: 1, name: "実行履歴" })).toBeVisible();
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
  await expect(page.getByTestId("history-list-count")).toHaveText("全 3 件");

  const newRow = row(page, "history-new");
  await expect(newRow.locator("[data-run-status]:visible")).toHaveAttribute("data-run-status", "succeeded");
  await expect(row(page, "history-old").locator("[data-run-status]:visible")).toHaveAttribute("data-run-status", "blocked");
  await expect(newRow).toContainText("経理プロファイル");
  // 実行者の列は、他の人の実行が見えない利用者には出さない（#1954。この一覧は自分の実行だけ）。
  await expect(table(page).getByRole("columnheader", { name: "実行者" })).toHaveCount(0);
  const compact = newRow.locator("[data-run-history-compact-meta]");
  if (isMobile) {
    // 375px は題名と操作の 2 列で、状態・日時・所要時間・種類を題名の下の 1 行にまとめる。
    await expect(compact).toBeVisible();
    await expect(compact).toContainText("2026/06/22");
    await expect(compact).toContainText("チャット");
    await expect(table(page).getByRole("columnheader", { name: "状態" })).toBeHidden();
  } else {
    await expect(compact).toBeHidden();
    for (const header of ["質問", "状態", "種類", "開始日時", "所要時間", "評価", "行数"]) {
      await expect(table(page).getByRole("columnheader", { name: header, exact: true })).toBeVisible();
    }
    await expect(newRow).toContainText("チャット");
    await expect(row(page, "history-middle")).toContainText("SQL 生成");
    await expect(row(page, "history-old")).toContainText("未実行");
  }
  expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  expect(await findToolbarMisalignment(page)).toEqual([]);
  expect(await findClippedPlaceholders(page)).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("history-list.png"), fullPage: true });
});

test("管理者と、自分の会話で他の人が実行した行を持つ利用者には実行者の列と絞り込みを出す（#1954）", { tag: "@responsive" }, async ({ page, isMobile }) => {
  await page.route("**/api/auth/me", (route) => fulfillJson(route, {
    ...systemAdminMe, is_system_admin: false, role_codes: ["ANALYST"], permissions: ["menu.history"],
  }));
  const requests: URL[] = [];
  const [own, byAdmin, standalone] = historyItems;
  await mockHistory(page, {
    othersVisible: true,
    requests,
    items: [
      { ...own, executor_display_name: "アリス", conversation_owner_display_name: "アリス" },
      {
        ...byAdmin, actor_user_uuid: "admin", executed_by_me: false, executor_display_name: "管理者 太郎",
        conversation_owner_display_name: "アリス", owned_by_me: true, kind: "chat",
      },
      { ...standalone, executor_display_name: "アリス" },
    ],
  });
  await page.goto("/history");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
  if (!isMobile) {
    await expect(table(page).getByRole("columnheader", { name: "実行者", exact: true })).toBeVisible();
    await expect(row(page, "history-middle")).toContainText("管理者 太郎");
    // 自分の実行は「自分」（UUID・ログイン ID を出さない）。
    await expect(row(page, "history-new")).toContainText("自分");
  } else {
    await expect(row(page, "history-middle").locator("[data-run-history-compact-meta]")).toContainText("管理者 太郎");
  }
  await expect(page.getByText("admin", { exact: true })).toHaveCount(0);

  // 実行者の絞り込み（既定 すべて）。サーバーへ executor を渡し、件数は「N 件（全 M 件）」。
  expect(requests.at(-1)?.searchParams.get("executor")).toBeNull();
  await chooseFilter(page, "executor", "others");
  await expect.poll(() => requests.at(-1)?.searchParams.get("executor")).toBe("others");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(1);
  await expect(page.getByTestId("history-list-count")).toHaveText("1 件（全 3 件）");
  await chooseFilter(page, "executor", "self");
  await expect.poll(() => requests.at(-1)?.searchParams.get("executor")).toBe("mine");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(2);
  // 絞り込みは作業状態に残り、再読み込みの後も同じ条件で取り直す。
  await page.reload();
  await expect.poll(() => requests.at(-1)?.searchParams.get("executor")).toBe("mine");

  // 詳細は実行者と、実行者と違うときだけ会話の持ち主を出す。
  await chooseFilter(page, "executor", "all");
  await titleLink(page, "請求金額を確認").click();
  const summary = page.getByTestId("history-detail-summary");
  await expect(summary.locator('[data-run-fact="executor"] dd')).toHaveText("管理者 太郎");
  await expect(summary.locator('[data-run-fact="owner"] dd')).toHaveText("自分");
  await page.getByRole("button", { name: "実行履歴の一覧へ戻る" }).click();
  await titleLink(page, "監査ログを削除").click();
  await expect(page.getByTestId("history-detail-summary").locator('[data-run-fact="owner"]')).toHaveCount(0);
});

test("SQL 生成を開けない利用者には再実行を出さず、開ける利用者は詳細から質問を引き継いで再実行できる (#912)", async ({ page }) => {
  let permissions = ["menu.history"];
  await page.route("**/api/auth/me", (route) => fulfillJson(route, {
    ...systemAdminMe, is_system_admin: false, role_codes: ["ANALYST"], permissions,
  }));
  await mockHistory(page);
  await page.goto("/history");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
  await expect(page.getByTestId("history-table-row-actions-history-new")).toHaveCount(0);
  await titleLink(page, "未入金の顧客を確認").click();
  await expect(page.getByTestId("history-detail-summary")).toBeVisible();
  await expect(page.getByRole("button", { name: "この質問で再実行" })).toHaveCount(0);

  permissions = ["menu.history", "menu.query"];
  await page.reload();
  await page.getByRole("button", { name: "この質問で再実行" }).click();
  await expect(page).toHaveURL(/\/query\?question=/);
  expect(new URL(page.url()).searchParams.get("question")).toBe(historyItems[0].question);
});

test("検索・状態・種類・期間の絞り込みはサーバーへ渡し、0 件なら条件のクリアを案内する", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const requests: URL[] = [];
  await mockHistory(page, { requests });
  await page.goto("/history");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);

  await chooseFilter(page, "status", "blocked");
  await expect.poll(() => requests.at(-1)?.searchParams.get("status")).toBe("blocked");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(1);
  await expect(page.getByTestId("history-list-count")).toHaveText("1 件（全 3 件）");

  await chooseFilter(page, "status", "all");
  await chooseFilter(page, "kind", "chat");
  await expect.poll(() => requests.at(-1)?.searchParams.get("kind")).toBe("chat");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(1);

  // 期間は今の時刻からの ISO 8601（since）。モックの記録は古いので 0 件になる。
  await chooseFilter(page, "period", "7d");
  await expect.poll(() => requests.at(-1)?.searchParams.get("since") ?? "").toMatch(/^\d{4}-\d{2}-\d{2}T/);
  await expect(page.getByText("条件に一致する実行がありません")).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("history-no-results.png"), fullPage: true });
  // 0 件の取り直しの間も、絞り込みの欄を残す（スケルトンに置き換えない。#739）。
  await expect(page.getByTestId("history-toolbar")).toBeVisible();
  await page.getByRole("button", { name: "条件をクリア" }).first().click();
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
  expect(requests.at(-1)?.searchParams.get("since")).toBeNull();
  // 「条件をクリア」は種類を既定（品質評価以外）へ戻す（#1986）。
  expect(requests.at(-1)?.searchParams.get("kind")).toBe("not_evaluation");

  // 検索欄（debounce・IME は SearchField）。
  await page.getByRole("searchbox", { name: "実行履歴を検索" }).fill("AUDIT_LOG");
  await expect.poll(() => requests.at(-1)?.searchParams.get("q")).toBe("AUDIT_LOG");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(1);
});

// #1986: Agent の品質評価の Run が MCP で呼んだ実行は既定で隠し、「種類: 品質評価」「すべて」で出す。
// MCP（外部のクライアント・Agent の Run）は種類に呼び出し元を添える。件数と「条件をクリア」は既定が基準。
const originItems: HistoryRecord[] = [
  ...historyItems,
  {
    ...historyItems[1],
    id: "history-mcp",
    question: "MCP で売上を確認",
    created_at: "2026-06-23T10:00:00.000Z",
    run_source: "mcp",
    api_key_name: "Claude Code",
    kind: "mcp",
  },
  {
    ...historyItems[1],
    id: "history-eval-1",
    question: "品質評価のケース 1",
    created_at: "2026-06-24T10:00:00.000Z",
    run_source: "evaluation",
    agent_run_id: "run-eval-1",
    kind: "evaluation",
  },
  {
    ...historyItems[1],
    id: "history-eval-2",
    question: "品質評価のケース 2",
    created_at: "2026-06-24T11:00:00.000Z",
    run_source: "evaluation",
    agent_run_id: "run-eval-2",
    kind: "evaluation",
  },
];

test("品質評価の実行は既定で隠し、種類で品質評価・すべてを選ぶと出る（#1986）", { tag: "@responsive" }, async ({ page, isMobile }) => {
  const requests: URL[] = [];
  await mockHistory(page, { items: originItems, requests });
  await page.goto("/history");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(4);
  await expect(table(page).locator('[data-run-id^="history-eval-"]')).toHaveCount(0);
  expect(requests.at(-1)?.searchParams.get("kind")).toBe("not_evaluation");
  await expect(page.getByTestId("history-list-count")).toHaveText("全 4 件");
  await expect(page.getByRole("button", { name: "条件をクリア", exact: true })).toHaveCount(0);
  const mcpKind = isMobile ? row(page, "history-mcp").locator("[data-run-history-compact-meta]") : row(page, "history-mcp");
  await expect(mcpKind).toContainText("MCP（Claude Code）");

  await chooseFilter(page, "kind", "evaluation");
  await expect.poll(() => requests.at(-1)?.searchParams.get("kind")).toBe("evaluation");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(2);
  await expect(page.getByTestId("history-list-count")).toHaveText("2 件（全 6 件）");
  await chooseFilter(page, "kind", "all");
  await expect.poll(() => requests.at(-1)?.searchParams.has("kind")).toBe(false);
  await expect(table(page).locator("[data-run-id]")).toHaveCount(6);

  // 品質評価の実行の詳細は種類「品質評価」と、技術的な詳細に Agent の実行 ID。
  await titleLink(page, "品質評価のケース 1").click();
  const summary = page.getByTestId("history-detail-summary");
  await expect(summary.locator('[data-run-fact="kind"] dd')).toHaveText("品質評価");
  const technical = page.getByTestId("history-detail-technical");
  await technical.getByText("技術的な詳細").click();
  await expect(page.getByTestId("history-detail-caller")).toContainText("run-eval-1");
  await page.getByRole("button", { name: "実行履歴の一覧へ戻る" }).click();

  await page.getByRole("button", { name: "条件をクリア", exact: true }).first().click();
  await expect(table(page).locator("[data-run-id]")).toHaveCount(4);
  expect(requests.at(-1)?.searchParams.get("kind")).toBe("not_evaluation");
});

test("品質評価の実行だけがあるときは、絞り込みを残して既定で隠していることと出し方を出す（#1986）", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockHistory(page, { items: originItems.filter((item) => item.kind === "evaluation") });
  await page.goto("/history");
  await expect(page.getByText("品質評価の実行（2 件）は既定で表示しません。")).toBeVisible();
  await expect(page.getByTestId("history-empty")).toHaveCount(0);
  await expect(page.getByTestId("history-toolbar")).toBeVisible();
  expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath("history-evaluation-hidden.png"), fullPage: true });
  await chooseFilter(page, "kind", "evaluation");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(2);
});

test("実行履歴は初回の読み込み中を経過時間とスケルトンで示し、実行が無いときは空の状態を出す", async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await mockHistory(page, { items: [], gate });
  await page.goto("/history");
  await expect(page.getByTestId("history-list-loading")).toContainText("実行履歴を読み込んでいます");
  await expect(page.getByTestId("history-list-skeleton")).toBeVisible();
  release();
  await expect(page.getByTestId("history-empty")).toContainText("まだ実行がありません");
  await expect(page.getByTestId("history-toolbar")).toHaveCount(0);
});

test("実行履歴は 25 件ずつのサーバーのページを表の直下で移り、ページ番号の入力・表示件数を残す (#1891)", { tag: "@responsive" }, async ({ page }, testInfo) => {
  const requests: URL[] = [];
  await mockHistory(page, { items: manyItems(60), requests });
  await page.goto("/history");
  const pagination = page.getByTestId("history-pagination");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(25);
  await expect(pagination).toContainText("1–25 / 60 件");
  await jumpToPage(pagination, 3);
  await expect.poll(() => requests.at(-1)?.searchParams.get("offset")).toBe("50");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(10);
  await expect(pagination).toContainText("51–60 / 60 件");
  await page.screenshot({ path: testInfo.outputPath("history-page-3.png"), fullPage: true });
  // 再読み込みの後も同じページ。
  await page.reload();
  await expect.poll(() => requests.at(-1)?.searchParams.get("offset")).toBe("50");
  // 表示件数を変えると 1 ページ目へ戻る。
  await chooseSelectFieldOption(pagination.getByRole("combobox"), "10");
  await expect.poll(() => requests.at(-1)?.searchParams.get("limit")).toBe("10");
  expect(requests.at(-1)?.searchParams.get("offset")).toBe("0");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(10);
});

test("詳細は題名のリンクで ?id= の全幅の画面に開き、一覧へ戻ると条件・ページ・開いた行が戻る", { tag: "@responsive" }, async ({ page }) => {
  const requests: URL[] = [];
  const items = manyItems(40);
  await mockHistory(page, { items, requests });
  await page.goto("/history");
  await jumpToPage(page.getByTestId("history-pagination"), 2);
  await expect.poll(() => requests.at(-1)?.searchParams.get("offset")).toBe("25");
  await chooseFilter(page, "status", "succeeded");
  await jumpToPage(page.getByTestId("history-pagination"), 2);
  await expect.poll(() => requests.at(-1)?.searchParams.get("offset")).toBe("25");

  const target = items[30];
  const link = titleLink(page, String(target.question));
  // 題名は href 付きのリンク（新しいタブで開ける）。
  await expect(link).toHaveAttribute("href", `/history?id=${target.id}`);
  await link.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/history\\?id=${target.id}$`));
  await expect(page.getByRole("heading", { level: 1, name: "実行の詳細" })).toBeVisible();
  await expect(page.getByTestId("history-list-view")).toBeHidden();
  const requestCount = requests.length;

  await page.getByRole("button", { name: "実行履歴の一覧へ戻る" }).click();
  await expect(page).toHaveURL(/\/history$/);
  await expect(link).toBeVisible();
  // 一覧は作り直さず、同じ条件・同じページのまま（取り直さない）。
  expect(requests.length).toBe(requestCount);
  await expect(page.getByTestId("history-pagination")).toContainText("26–40 / 40 件");
  await expect(page.getByTestId("history-toolbar-status").or(page.getByTestId("history-toolbar").getByRole("button", { name: /^絞り込み（1）/ })).first()).toBeVisible();
  await expect(link).toHaveAttribute("aria-current", "true");

  // ブラウザの戻る・進むでも同じ。
  await page.goForward();
  await expect(page.getByRole("heading", { level: 1, name: "実行の詳細" })).toBeVisible();
  await page.goBack();
  await expect(link).toBeVisible();

  // URL を直接開ける（再読み込みの後も詳細）。
  await page.goto(`/history?id=${target.id}`);
  await expect(page.getByTestId("history-detail-summary")).toContainText(String(target.question));
  await page.getByRole("button", { name: "実行履歴の一覧へ戻る" }).click();
  await expect(page).toHaveURL(/\/history$/);
  await expect.poll(() => requests.at(-1)?.searchParams.get("offset")).toBe("25");
  expect(requests.at(-1)?.searchParams.get("status")).toBe("succeeded");
});

test("詳細は概要 → 処理の経過 → SQL と結果 → フィードバック → 技術的な詳細を 1 枚に並べ、段は Enter で開閉できる", { tag: "@responsive" }, async ({ page }, testInfo) => {
  await mockHistory(page);
  await page.goto("/history?id=history-new");
  const detail = page.locator("[data-run-detail]");
  await expect(detail).toBeVisible();
  const order = await detail.evaluate((node) =>
    Array.from(node.children).map((child) => child.getAttribute("data-testid"))
  );
  expect(order).toEqual([
    "history-detail-summary",
    "history-detail-timeline",
    "history-detail-sql",
    "history-detail-feedback",
    "history-detail-technical",
  ]);
  const summary = page.getByTestId("history-detail-summary");
  await expect(summary.locator("[data-run-status]")).toHaveAttribute("data-run-status", "succeeded");
  await expect(summary.locator('[data-run-fact="runId"] dd')).toHaveText("history-new");
  await expect(summary.locator('[data-run-fact="target"] dd')).toHaveText("経理プロファイル");
  await expect(summary.locator('[data-run-fact="startedAt"] time')).toHaveAttribute("datetime", "2026-06-22T10:00:00.000Z");

  // 処理の経過: チャットの処理の経過と同じ段の名前。失敗した生成の方式の試行は最初から開く。
  const timeline = page.getByTestId("history-detail-timeline");
  await expect(timeline.locator("[data-run-timeline-step]")).toHaveCount(6);
  const failed = timeline.locator('[data-run-timeline-step="engine-0"]');
  await expect(failed).toHaveAttribute("data-status", "failed");
  const toggle = failed.getByRole("button");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(failed).toContainText("team execution failed");
  await toggle.focus();
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await page.keyboard.press("Enter");
  await expect(toggle).toHaveAttribute("aria-expanded", "true");

  // SQL と結果: 実行した SQL とコピー、結果の要約（行数・列）。
  const sql = page.getByTestId("history-detail-sql");
  await expect(sql.getByTestId("history-detail-sql-code")).toContainText("FETCH FIRST 100 ROWS ONLY");
  await expect(sql.getByRole("button", { name: "SQL をコピー" })).toBeVisible();
  await expect(sql.locator('[data-run-fact="columns"]')).toContainText("CUSTOMER_NAME");
  await expect(page.getByTestId("history-detail-feedback")).toContainText("期待通りです");
  // 技術的な詳細は既定で閉じる。
  const technical = page.getByTestId("history-detail-technical");
  await expect(technical.getByTestId("history-detail-generated-sql")).toBeHidden();
  await technical.locator("summary").click();
  await expect(technical.getByTestId("history-detail-generated-sql")).toContainText("PAID_AT IS NULL");
  expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: testInfo.outputPath("history-detail.png"), fullPage: true });
});

test("ブロックした実行は概要に理由を出し、無い実行・見えない実行は「この実行は見つかりません」", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history?id=history-old");
  const summary = page.getByTestId("history-detail-summary");
  await expect(summary.locator("[data-run-status]")).toHaveAttribute("data-run-status", "blocked");
  await expect(page.getByTestId("history-detail-blocked")).toContainText("安全性の判定で SQL の実行を止めました");
  await expect(page.getByTestId("history-detail-timeline").locator('[data-run-timeline-step="safety_check-1"]')).toHaveAttribute("data-status", "failed");
  await expect(summary.locator('[data-run-fact="rows"] dd')).toHaveText("未実行");

  await page.goto("/history?id=missing-run");
  await expect(page.getByTestId("history-detail-not-found")).toContainText("この実行は見つかりません");
  await page.getByTestId("history-detail-not-found").getByRole("button", { name: "一覧へ戻る" }).click();
  await expect(page).toHaveURL(/\/history$/);
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
});

test("一覧の読み込みに失敗したら一覧の位置に再試行付きの失敗を出し、前の一覧があれば残す", async ({ page }) => {
  let failing = true;
  await mockHistory(page, { fail: () => failing });
  await page.goto("/history");
  const error = page.getByTestId("history-list-error");
  await expect(error).toBeVisible();
  // 取得できていない条件の結果を「0 件」と取り違えさせない。
  await expect(page.getByText("条件に一致する実行がありません")).toHaveCount(0);
  failing = false;
  await error.getByRole("button", { name: "再試行" }).click();
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
  await expect(error).toHaveCount(0);

  failing = true;
  await page.getByRole("button", { name: "最新情報を取得", exact: true }).click();
  await expect(error).toBeVisible();
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
});

for (const theme of ["light", "dark"] as const) {
  test(`実行履歴の一覧と詳細は 1280 / 1920 / 375px で横にあふれず、見出しと本文の左端がそろう: ${theme}`, { tag: "@responsive" }, async ({ page, isMobile }, testInfo) => {
    await page.addInitScript((value) => {
      localStorage.setItem("production-ready-nl2sql.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
    }, theme);
    const long = {
      ...historyItems[0],
      id: "history-long",
      question: "対象テーブル：VERY_LONG_UNBROKEN_IDENTIFIER_WITHOUT_SPACES_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ の売上を地域別・月別に集計して前年同月比も出す",
      profile_name: "プロファイル_WITH_A_VERY_LONG_UNBROKEN_IDENTIFIER_0123456789",
      executable_sql: `SELECT "VERY_LONG_UNBROKEN_FILTER_IDENTIFIER_0123456789" FROM "V_EMP_DEPT"`,
    };
    await mockHistory(page, { items: [long, ...historyItems.slice(1)], othersVisible: true });
    await page.goto("/history");
    await expect(page.locator("html")).toHaveCSS("color-scheme", theme);
    for (const width of isMobile ? [375] : [1280, 1920]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
      expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
      await expectAlignedHeader(page);
      await page.screenshot({ path: testInfo.outputPath(`history-list-${theme}-${width}.png`), fullPage: true });
      await titleLink(page, String(long.question)).click();
      await expect(page.getByTestId("history-detail-summary")).toBeVisible();
      expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: testInfo.outputPath(`history-detail-${theme}-${width}.png`), fullPage: true });
      await page.getByRole("button", { name: "実行履歴の一覧へ戻る" }).click();
    }
  });
}

test("最初の Tab で本文へスキップでき、一覧の題名のリンクへキーボードで届く", async ({ page }) => {
  await mockHistory(page);
  await page.goto("/history");
  await expect(table(page).locator("[data-run-id]")).toHaveCount(3);
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus")).toHaveText(/本文へスキップ/);
  const link: Locator = titleLink(page, "未入金の顧客を確認");
  for (let index = 0; index < 30; index += 1) {
    if (await link.evaluate((node) => node === document.activeElement)) break;
    await page.keyboard.press("Tab");
  }
  await expect(link).toBeFocused();
});
