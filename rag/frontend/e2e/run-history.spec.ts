import { expect, type Locator, type Page, test } from "./fixtures/test";
import {
  apiEnvelope,
  expectNoPageOverflow,
  mockAuthUser,
  mockDatabaseReady,
  mockLocalAuth,
  openSidebarNav,
} from "./_helpers";
import { jumpToPage, measureHorizontalOverflow } from "../../../platform/packages/ui/e2e/layout-checks";

// Issue 1890・1955（段 2）: RAG の「AI 活用」の「実行履歴」を 3 製品の共通の型（UX 契約 run-history.md）で作る。
// - 一覧: RunHistoryToolbar（検索 → 状態 → 種類 → 期間 → 実行者。FilterSelect）→ RunHistoryTable → OffsetPagination
//   （既定 25 件）。読み込み中は経過時間と表の Skeleton。条件・ページ・開いた行は作業状態に残る。
// - 状態は共通の 7 つに写し、`[data-run-status]` で確かめる（success → succeeded・blocked・error → failed）。
// - 375px は表が 2 列で、状態・日時・所要時間が題名の下の 1 行（`[data-run-history-compact-meta]`）。
// - 見え方（Issue 1889・1954）: system_admin は全員の実行、それ以外は自分が実行した実行と、自分の会話で
//   他の人（system_admin）が実行した実行。「実行者」の列と絞り込みは他の人の実行が見える利用者だけ。
// - 詳細: RunDetailLayout（概要 → 回答 → 処理の経過 → 根拠 → フィードバック → 技術的な詳細）をタブ無しの 1 枚で。

const PAGE_SIZE = 25;

interface RunRow {
  run_id: string;
  surface: "search" | "chat";
  search_answer_profile_id: string | null;
  search_answer_profile_name: string | null;
  question: string | null;
  answer_generated: boolean;
  status: "success" | "blocked" | "error";
  answer_outcome: string | null;
  verification: "verified" | "partial" | "unverified" | null;
  confidence: string | null;
  needs_human_review: boolean | null;
  citation_count: number;
  elapsed_ms: number | null;
  error_stage: string | null;
  model_id: string | null;
  conversation_id: string | null;
  message_id: string | null;
  created_at: string;
  executed_by_me: boolean;
  executor_display_name: string;
  /** 詳細だけの項目（会話の持ち主。既定は会話のある実行なら自分）。 */
  conversation_owner_display_name?: string | null;
  owned_by_me?: boolean | null;
}

function run(index: number, overrides: Partial<RunRow> = {}): RunRow {
  const chat = index % 2 === 1;
  return {
    run_id: `run-${index}`,
    surface: chat ? "chat" : "search",
    search_answer_profile_id: "bv-1",
    search_answer_profile_name: "受注業務",
    question: `受注の登録方法 ${index}`,
    answer_generated: chat,
    status: "success",
    answer_outcome: chat ? "answered" : null,
    verification: chat ? "verified" : null,
    confidence: chat ? "high" : null,
    needs_human_review: chat ? false : null,
    citation_count: 2,
    elapsed_ms: 1200 + index,
    error_stage: null,
    model_id: null,
    conversation_id: chat ? `conv-${index}` : null,
    message_id: chat ? `msg-${index}` : null,
    created_at: `2026-10-15T0${index % 10}:00:00Z`,
    executed_by_me: true,
    executor_display_name: "ローカル利用者",
    ...overrides,
  };
}

const CITATION = {
  document_id: "doc-1",
  chunk_id: "doc-1:c1",
  file_name: "受注マニュアル.pdf",
  text: "受注番号を入力し、登録ボタンを押します。",
  score: 0.91,
  metadata: {},
};

function detail(row: RunRow) {
  return {
    ...row,
    conversation_owner_display_name:
      row.conversation_owner_display_name !== undefined
        ? row.conversation_owner_display_name
        : row.conversation_id
          ? "ローカル利用者"
          : null,
    owned_by_me: row.owned_by_me !== undefined ? row.owned_by_me : row.conversation_id ? true : null,
    error_type: row.status === "error" ? "TimeoutError" : null,
    answer: row.answer_generated ? "受注番号を入力して登録します。" : null,
    answer_available: row.answer_generated,
    citations: [CITATION],
    answer_diagnostics: row.answer_generated ? { outcome: "answered", confidence: "high", execution_steps: [] } : null,
    stages: row.answer_generated
      ? [
          { stage: "answer_step:質問の理解", outcome: "success", elapsed_ms: 300 },
          { stage: "answer_step:文書検索", outcome: "success", elapsed_ms: 500 },
          { stage: "answer", outcome: "success", elapsed_ms: 1100 },
          { stage: "answer_guardrail", outcome: "success", elapsed_ms: 40 },
        ]
      : [{ stage: "retrieval", outcome: row.status === "error" ? "error" : "success", elapsed_ms: 1100 }],
    steps: row.answer_generated
      ? [
          { name: "質問の理解", status: "complete", elapsed_seconds: 0.3, llm_calls: 1 },
          { name: "文書検索", status: "complete", elapsed_seconds: 0.5, llm_calls: 0 },
        ]
      : [],
    feedback: row.answer_generated
      ? [
          {
            feedback_id: "fb-1",
            target_type: "answer",
            rating: "helpful",
            reason: null,
            document_id: null,
            created_at: "2026-10-15T01:00:00Z",
          },
        ]
      : [],
  };
}

async function mockRuns(page: Page, rows: RunRow[], { gated = false } = {}) {
  let release: () => void = () => undefined;
  const gate = gated ? new Promise<void>((resolve) => (release = resolve)) : Promise.resolve();
  const requests: URLSearchParams[] = [];
  await page.route("**/api/history/runs**", async (route) => {
    const url = new URL(route.request().url());
    const detailMatch = url.pathname.match(/^\/api\/history\/runs\/(.+)$/);
    if (detailMatch) {
      const found = rows.find((item) => item.run_id === decodeURIComponent(detailMatch[1]));
      await route.fulfill(
        found
          ? { json: apiEnvelope(detail(found)) }
          : {
              status: 404,
              json: { data: null, error_messages: ["実行が見つかりません。"], warning_messages: [] },
            }
      );
      return;
    }
    requests.push(url.searchParams);
    await gate;
    const q = url.searchParams.get("q") ?? "";
    const surface = url.searchParams.get("surface");
    const status = url.searchParams.get("status");
    const executor = url.searchParams.get("executor");
    const since = url.searchParams.get("since");
    const matched = rows.filter(
      (item) =>
        (!q || (item.question ?? "").includes(q) || item.run_id === q) &&
        (!surface || item.surface === surface) &&
        (!status || item.status === status) &&
        (!executor || (executor === "mine" ? item.executed_by_me : !item.executed_by_me)) &&
        (!since || item.created_at >= since)
    );
    const limit = Number(url.searchParams.get("limit") ?? PAGE_SIZE);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    await route.fulfill({
      json: apiEnvelope({
        items: matched.slice(offset, offset + limit),
        total: matched.length,
        limit,
        offset,
        has_next: offset + limit < matched.length,
      }),
    });
  });
  // 件数の「全 M 件」・他の人の実行の有無を数える 1 件の取得（limit=1）を除いた、一覧の取得。
  const listRequests = () => requests.filter((params) => params.get("limit") !== "1");
  return { release, requests, listRequests };
}

function isNarrow(page: Page) {
  return (page.viewportSize()?.width ?? 1440) < 768;
}

/** ツールバーの絞り込みを選ぶ（375px は「絞り込み」のシートを開いてから選び、閉じる）。 */
async function chooseFilter(page: Page, name: string, option: string) {
  const toolbar = page.getByTestId("run-history-toolbar");
  const sheet = (await toolbar.getAttribute("data-list-toolbar-filters-layout")) === "sheet";
  if (sheet) await toolbar.getByRole("button", { name: /^絞り込み/ }).click();
  await page.getByRole("combobox", { name }).click();
  await page.getByRole("option", { name: option, exact: true }).click();
  if (sheet) await page.getByRole("button", { name: "絞り込みを閉じる" }).click();
}

/** 表頭は固定され、表示行数（md 未満 5 行・md 以上 8 行）を超えた行は表の中で縦スクロールする。 */
async function expectScrollsInsideTable(page: Page, region: Locator) {
  await expect(region).toBeVisible();
  const expectedRows = isNarrow(page) ? 5 : 8;
  const metrics = await region.evaluate((element) => {
    const header = element.querySelector("thead")!;
    const rows = Array.from(element.querySelectorAll('tbody tr[data-row-kind="data"]'));
    const visible = rows.filter((row) => row.getBoundingClientRect().bottom <= element.getBoundingClientRect().bottom + 1);
    return {
      sticky: getComputedStyle(header).position,
      scrollable: element.scrollHeight > element.clientHeight + 1,
      visibleRows: visible.length,
    };
  });
  expect(metrics.sticky).toBe("sticky");
  expect(metrics.scrollable).toBe(true);
  expect(metrics.visibleRows).toBe(expectedRows);
}

test.describe("実行履歴", { tag: "@responsive" }, () => {
  test("system_admin: 読み込み中は経過時間と表の Skeleton、全員の実行と実行者の列、ページ番号で移りページは再読込でも残る", async ({
    page,
  }) => {
    await mockLocalAuth(page);
    const rows = Array.from({ length: 55 }, (_, index) =>
      run(index + 1, {
        executed_by_me: index === 0,
        executor_display_name: index === 0 ? "ローカル利用者" : index % 3 === 2 ? "" : `利用者 ${index + 1}`,
        status: index === 4 ? "blocked" : index === 6 ? "error" : "success",
      })
    );
    const { release, listRequests } = await mockRuns(page, rows, { gated: true });

    await page.goto("/history");
    const loading = page.getByTestId("run-history-loading");
    await expect(loading).toBeVisible();
    await expect(loading).toContainText("実行履歴を読み込んでいます");
    await expect(loading.getByRole("timer")).toBeVisible();
    await expect(loading.locator('[data-skeleton="table"]')).toBeVisible();
    await expectNoPageOverflow(page);
    release();

    await expect(loading).toHaveCount(0);
    await expect(page.getByTestId("run-history-scope")).toContainText("すべての利用者の実行");
    const table = page.getByRole("table", { name: "実行履歴" });
    // 状態は共通の値（色・文言に頼らない）。ブロックの文言は RAG の言い方。
    await expect(page.getByTestId("run-history-row-run-1").locator('[data-run-status="succeeded"]:visible')).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-5").locator('[data-run-status="blocked"]:visible')).toContainText(
      "安全チェックで停止"
    );
    await expect(page.getByTestId("run-history-row-run-7").locator('[data-run-status="failed"]:visible')).toBeVisible();
    // 題名は詳細へのリンク（新しいタブでも開ける）。下に検索・回答プロファイル。
    const title = page.getByTestId("run-history-row-run-1").locator('[data-run-id="run-1"]');
    await expect(title).toHaveAttribute("href", /\/history\/run-1$/);
    await expect(page.getByTestId("run-history-row-run-1")).toContainText("受注業務");

    if (isNarrow(page)) {
      // 375px: 表は題名だけの列で、状態・日時・所要時間・種類・実行者を題名の下の 1 行にまとめる。
      const meta = page.getByTestId("run-history-row-run-2").locator("[data-run-history-compact-meta]");
      await expect(meta).toBeVisible();
      await expect(meta).toContainText("利用者 2");
      await expect(meta).toContainText("RAG 検索");
      await expect(table.getByRole("columnheader", { name: "実行者" })).toBeHidden();
    } else {
      await expect(page.getByTestId("run-history-row-run-1").locator("[data-run-history-compact-meta]")).toBeHidden();
      await expect(table.getByRole("columnheader", { name: "実行者" })).toBeVisible();
      await expect(table.getByRole("columnheader", { name: "開始日時" })).toBeVisible();
      await expect(table.getByRole("columnheader", { name: "回答の対応" })).toBeVisible();
      // 自分の行は「自分」、引けない利用者は UUID を出さずに「削除済みのユーザー」。
      await expect(page.getByTestId("run-history-row-run-1")).toContainText("自分");
      await expect(page.getByTestId("run-history-row-run-2")).toContainText("利用者 2");
      await expect(page.getByTestId("run-history-row-run-3")).toContainText("（削除済みのユーザー）");
      await expect(page.getByTestId("run-history-row-run-1")).toContainText("検証済み");
      await expect(page.getByTestId("run-history-row-run-2")).toContainText("回答なし（検索のみ）");
    }
    await expectScrollsInsideTable(page, page.getByRole("region", { name: "実行履歴" }));
    expect(listRequests().at(-1)?.get("limit")).toBe(String(PAGE_SIZE));
    expect(listRequests().at(-1)?.get("since")).toBeNull();

    const pagination = page.getByTestId("run-history-pagination");
    await expect(pagination).toContainText("1–25 / 55 件");
    await jumpToPage(pagination, 2);
    await expect(pagination).toContainText("26–50 / 55 件");
    await expect(page.getByTestId("run-history-row-run-26")).toBeVisible();
    expect(listRequests().at(-1)?.get("offset")).toBe("25");
    await expectNoPageOverflow(page);

    await page.reload();
    await expect(page.getByTestId("run-history-pagination")).toContainText("26–50 / 55 件");
  });

  test("一般の利用者: 他の人が実行した行があるときだけ実行者の列と絞り込みを出し、実行者で絞れる。ナビの「AI 活用」に History のアイコンで並ぶ", async ({
    page,
  }) => {
    await mockDatabaseReady(page);
    await mockAuthUser(page, { permissions: ["menu.chat", "menu.search", "menu.history"] });
    const adminRun = run(3, {
      question: "管理者が続けた質問",
      executed_by_me: false,
      executor_display_name: "システム管理者",
    });
    const { listRequests } = await mockRuns(page, [run(1), run(2), adminRun]);

    await page.goto("/history");
    await expect(page.getByTestId("run-history-scope")).toContainText("自分の会話で他の人が実行した実行");
    await expect(page.getByTestId("run-history-row-run-3")).toContainText("システム管理者");
    // 1 ページしかないときはページングを出さない。
    await expect(page.getByTestId("run-history-pagination")).toHaveCount(0);
    await expectNoPageOverflow(page);

    // 実行者の絞り込み（既定はすべて）。選んだ値は API の executor と作業状態に残す。
    expect(listRequests().at(-1)?.get("executor")).toBeNull();
    await chooseFilter(page, "実行者", "他の人が実行");
    await expect(page.getByTestId("run-history-row-run-3")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-1")).toHaveCount(0);
    expect(listRequests().at(-1)?.get("executor")).toBe("others");
    await expect(page.getByTestId("run-history-toolbar")).toContainText("1 件（全 3 件）");
    await page.reload();
    await expect(page.getByTestId("run-history-row-run-1")).toHaveCount(0);
    await chooseFilter(page, "実行者", "自分が実行");
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-3")).toHaveCount(0);
    expect(listRequests().at(-1)?.get("executor")).toBe("mine");

    const sidebar = await openSidebarNav(page);
    const link = sidebar.getByRole("link", { name: "実行履歴" });
    await expect(link).toHaveAttribute("href", /\/history$/);
    await expect(link.locator("svg").first()).toHaveClass(/lucide-history/);
    const useItems = sidebar.getByRole("link").filter({ hasText: /^(チャット|RAG 検索|実行履歴)$/ });
    await expect(useItems).toHaveText(["チャット", "RAG 検索", "実行履歴"]);
  });

  test("一般の利用者: 自分の実行だけなら実行者の列と絞り込みを出さない", async ({ page }) => {
    await mockDatabaseReady(page);
    await mockAuthUser(page, { permissions: ["menu.chat", "menu.search", "menu.history"] });
    await mockRuns(page, [run(1), run(2)]);

    await page.goto("/history");
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
    await expect(page.getByRole("table", { name: "実行履歴" }).getByRole("columnheader", { name: "実行者" })).toHaveCount(0);
    await expect(page.locator("#run-history-executor")).toHaveCount(0);
  });

  test("絞り込み: 状態・種類・期間・検索はサーバーで絞り、条件で 0 件なら「絞り込みをクリア」、ツールバーの「条件をクリア」で既定へ戻す", async ({
    page,
  }) => {
    await mockLocalAuth(page);
    const recent = new Date(Date.now() - 60 * 60 * 1000).toISOString();
    const { listRequests } = await mockRuns(page, [
      run(1, { created_at: recent }),
      run(2, { status: "error", question: "失敗した質問", created_at: recent }),
      run(3, { created_at: "2026-09-01T00:00:00Z" }),
    ]);

    await page.goto("/history");
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
    const toolbar = page.getByTestId("run-history-toolbar");
    await expect(toolbar.getByRole("button", { name: "条件をクリア" })).toHaveCount(0);

    await chooseFilter(page, "状態", "失敗");
    await expect(page.getByTestId("run-history-row-run-2")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-1")).toHaveCount(0);
    expect(listRequests().at(-1)?.get("status")).toBe("error");
    await expect(toolbar).toContainText("1 件（全 3 件）");

    await chooseFilter(page, "状態", "すべて");
    await chooseFilter(page, "種類", "チャット");
    expect(listRequests().at(-1)?.get("surface")).toBe("chat");
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-2")).toHaveCount(0);

    // 期間は開始の時刻（ISO 8601）を since で送る。
    await chooseFilter(page, "期間", "過去 24 時間");
    const since = listRequests().at(-1)?.get("since");
    expect(since).toBeTruthy();
    const ageMs = Date.now() - Date.parse(since as string);
    expect(ageMs).toBeGreaterThan(23 * 60 * 60 * 1000);
    expect(ageMs).toBeLessThan(25 * 60 * 60 * 1000);
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-3")).toHaveCount(0);

    // 条件をすべて既定へ戻す（ツールバーの「条件をクリア」）。
    await toolbar.getByRole("button", { name: "条件をクリア" }).click();
    await expect(page.getByTestId("run-history-row-run-3")).toBeVisible();
    expect(listRequests().at(-1)?.get("surface")).toBeNull();
    expect(listRequests().at(-1)?.get("since")).toBeNull();

    // 実行 ID でも探せる。
    const search = page.getByRole("searchbox", { name: "質問・実行 ID で絞り込み" });
    await search.fill("run-2");
    await expect(page.getByTestId("run-history-row-run-2")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-1")).toHaveCount(0);

    await search.fill("存在しない");
    await expect(page.getByText("条件に一致する実行がありません")).toBeVisible();
    expect(listRequests().at(-1)?.get("q")).toBe("存在しない");
    await page.getByRole("button", { name: "絞り込みをクリア" }).click();
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test("実行がまだ無いときの空の状態", async ({ page }) => {
    await mockLocalAuth(page);
    await mockRuns(page, []);
    await page.goto("/history");
    await expect(page.getByText("実行はまだありません")).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test("詳細: 概要 → 回答 → 処理の経過 → 根拠 → フィードバック → 技術的な詳細の 1 枚。一覧へ戻ると条件・ページと開いた行が戻る", async ({
    page,
  }) => {
    await mockLocalAuth(page);
    const rows = Array.from({ length: 30 }, (_, index) => run(index + 1));
    await mockRuns(page, rows);

    await page.goto("/history");
    await chooseFilter(page, "種類", "チャット");
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
    await page.getByTestId("run-history-pagination").getByRole("combobox", { name: "表示件数" }).click();
    await page.getByRole("option", { name: "10 件" }).click();
    await jumpToPage(page.getByTestId("run-history-pagination"), 2);
    await expect(page.getByTestId("run-history-pagination")).toContainText("11–15 / 15 件");
    await page.getByTestId("run-history-row-run-21").locator('[data-run-id="run-21"]').click();
    await expect(page).toHaveURL(/\/history\/run-21$/);

    const detailRoot = page.getByTestId("run-history-detail");
    const sections = await detailRoot.evaluate((element) =>
      Array.from(element.children).map((child) => child.getAttribute("data-testid"))
    );
    expect(sections).toEqual([
      "run-history-summary",
      "run-history-answer",
      "run-history-timeline",
      "run-history-evidence",
      "run-history-feedback",
      "run-history-technical",
    ]);
    // タブは無い（根拠・処理の経過をページ内検索・読み上げで届くように）。
    await expect(page.getByRole("tablist")).toHaveCount(0);

    const summary = page.getByTestId("run-history-summary");
    await expect(summary.locator('[data-run-status="succeeded"]')).toBeVisible();
    await expect(summary).toContainText("受注の登録方法 21");
    await expect(summary).toContainText("検証済み");
    await expect(summary.locator('[data-run-fact="target"]')).toContainText("受注業務");
    await expect(summary.locator('[data-run-fact="executor"] dd')).toHaveText("自分");
    // 自分が実行した自分の会話は「会話の持ち主」を出さない。
    await expect(summary.locator('[data-run-fact="owner"]')).toHaveCount(0);
    await expect(page.getByTestId("run-history-open-chat")).toHaveAttribute(
      "href",
      /\/chat\?search_answer_profile_id=bv-1&conversation_id=conv-21$/
    );
    await expect(page.getByTestId("run-history-answer")).toContainText("受注番号を入力して登録します。");
    await expect(page.getByTestId("run-history-citations")).toContainText("受注マニュアル.pdf");

    // 処理の経過: 段と、回答フローの工程（字下げ）。所要時間は文字でも出す。
    const timeline = page.getByTestId("run-history-timeline");
    await expect(timeline.locator("[data-run-timeline-step]")).toHaveCount(4);
    await expect(timeline.locator("[data-run-timeline-step]").nth(0)).toContainText("根拠の検索と回答の生成");
    await expect(timeline.locator("[data-run-timeline-step]").nth(0)).toContainText("1.1 秒");
    await expect(timeline.locator("[data-run-timeline-step]").nth(1)).toContainText("質問の理解（AI の呼び出し 1 回）");
    await expect(timeline.locator("[data-run-timeline-step]").nth(3)).toContainText("回答の安全チェック");
    if (!isNarrow(page)) await expect(timeline.locator("[data-run-timeline-bar]").first()).toBeVisible();

    await expect(page.getByTestId("run-history-feedback-open-fb-1")).toHaveAttribute(
      "href",
      /\/feedback\?feedback=fb-1&period=all$/
    );
    // 技術的な詳細は既定で閉じる。
    const technical = page.getByTestId("run-history-technical");
    await expect(technical.getByText("conv-21")).toBeHidden();
    await technical.locator("summary").click();
    await expect(technical.getByText("conv-21")).toBeVisible();
    await expectNoPageOverflow(page);

    await page.getByRole("button", { name: "実行履歴の一覧へ戻る" }).click();
    await expect(page).toHaveURL(/\/history$/);
    await expect(page.getByTestId("run-history-pagination")).toContainText("11–15 / 15 件");
    await expect(page.getByTestId("run-history-row-run-21").locator('[data-run-id="run-21"]')).toHaveAttribute(
      "aria-current",
      "true"
    );
    await expect(page.getByTestId("run-history-row-run-1")).toHaveCount(0);
  });

  test("詳細: 回答を作らない RAG 検索は根拠だけ、失敗は理由の Banner、見えない実行は理由を出す", async ({ page }) => {
    await mockLocalAuth(page);
    await mockRuns(page, [run(2), run(4, { status: "error", error_stage: "retrieval" })]);

    await page.goto("/history/run-2");
    await expect(page.getByTestId("run-history-summary")).toContainText("回答なし（検索のみ）");
    await expect(page.getByTestId("run-history-answer")).toHaveCount(0);
    await expect(page.getByTestId("run-history-citations")).toContainText("受注マニュアル.pdf");
    // 会話の無い実行には会話への導線を出さない。
    await expect(page.getByTestId("run-history-open-chat")).toHaveCount(0);

    await page.goto("/history/run-4");
    const summary = page.getByTestId("run-history-summary");
    await expect(summary.locator('[data-run-status="failed"]')).toBeVisible();
    await expect(summary.getByRole("alert")).toContainText("この実行は途中で失敗しました");
    await expect(summary.locator('[data-run-fact="errorStage"]')).toContainText("検索");
    await expect(page.getByTestId("run-history-timeline").locator('[data-status="failed"]')).toContainText("失敗");
    await expect(page.getByTestId("run-history-evidence")).toHaveCount(0);

    await page.goto("/history/run-missing");
    await expect(page.getByText("実行が見つかりません。")).toBeVisible();
  });

  test("詳細: 持ち主の会話で system_admin が実行した実行は、持ち主に実行者と会話の持ち主を出し、会話を開ける", async ({
    page,
  }) => {
    await mockDatabaseReady(page);
    await mockAuthUser(page, { permissions: ["menu.chat", "menu.search", "menu.history"] });
    await mockRuns(page, [
      run(1, {
        executed_by_me: false,
        executor_display_name: "システム管理者",
        conversation_owner_display_name: "アリス",
        owned_by_me: true,
      }),
      // 持ち主でない会話の実行（会話の API は持ち主と system_admin に閉じている）には導線を出さない。
      run(3, { conversation_owner_display_name: "ボブ", owned_by_me: false }),
    ]);

    await page.goto("/history/run-1");
    const summary = page.getByTestId("run-history-summary");
    await expect(summary.locator('[data-run-fact="executor"] dd')).toHaveText("システム管理者");
    await expect(summary.locator('[data-run-fact="owner"] dd')).toHaveText("自分");
    await expect(page.getByTestId("run-history-open-chat")).toHaveAttribute(
      "href",
      /\/chat\?search_answer_profile_id=bv-1&conversation_id=conv-1$/
    );
    await expectNoPageOverflow(page);

    await page.goto("/history/run-3");
    await expect(summary.locator('[data-run-fact="owner"] dd')).toHaveText("ボブ");
    await expect(page.getByTestId("run-history-open-chat")).toHaveCount(0);
  });
});

test.describe("実行履歴の詳細の幅とテーマ", () => {
  for (const theme of ["light", "dark"] as const) {
    test(`1280 / 1920px で横あふれが無く、見出しと本文の左端がそろう (${theme})`, async ({ page }, testInfo) => {
      test.skip(testInfo.project.name !== "desktop", "幅を指定して確かめる（desktop だけ）");
      await page.addInitScript((value) => {
        window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
      }, theme);
      await mockLocalAuth(page);
      await mockRuns(page, [run(1)]);
      for (const width of [1280, 1920]) {
        await page.setViewportSize({ width, height: 900 });
        await page.goto("/history/run-1");
        await expect(page.getByTestId("run-history-summary")).toBeVisible();
        expect(await measureHorizontalOverflow(page)).toBeLessThanOrEqual(1);
        const titleLeft = await page.getByRole("heading", { level: 1, name: "実行の詳細" }).evaluate(
          (element) => element.getBoundingClientRect().left
        );
        const bodyLeft = await page.getByTestId("run-history-summary").evaluate((element) => element.getBoundingClientRect().left);
        expect(Math.abs(titleLeft - bodyLeft)).toBeLessThanOrEqual(1);
      }
    });
  }
});
