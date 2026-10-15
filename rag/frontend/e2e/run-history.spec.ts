import { expect, type Locator, type Page, test } from "./fixtures/test";
import {
  apiEnvelope,
  expectNoPageOverflow,
  mockAuthUser,
  mockDatabaseReady,
  mockLocalAuth,
  openSidebarNav,
} from "./_helpers";

// Issue 1890: RAG の「AI 活用」の「実行履歴」（チャット・RAG 検索の実行の一覧と詳細）。
// - 一覧は NL2SQL の基準（読み込み中は経過時間と表の Skeleton、表の中の縦スクロール、10 件のページング、
//   ページは再読込でも残る）。検索・種類・状態はサーバー側で絞り込む。
// - 見え方（Issue 1889・1954）: system_admin は全員の実行、それ以外は自分が実行した実行と、自分の会話で
//   他の人（system_admin）が実行した実行。「実行者」の列は全員に出し、実行した人が自分でない行には
//   「〈名前〉 が実行」を添える。ツールバーの「実行者」（すべて / 自分が実行 / 他の人が実行）で絞れる。
// - 詳細は全幅（Agent の実行履歴と同じ型）。結果（回答・根拠）・処理の経過・フィードバックのタブ。

const PAGE_SIZE = 10;

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
    error_type: null,
    answer: row.answer_generated ? "受注番号を入力して登録します。" : null,
    answer_available: row.answer_generated,
    citations: [CITATION],
    answer_diagnostics: row.answer_generated ? { outcome: "answered", confidence: "high", execution_steps: [] } : null,
    stages: [
      { stage: row.answer_generated ? "answer" : "retrieval", outcome: "success", elapsed_ms: 1100 },
      ...(row.answer_generated ? [{ stage: "answer_guardrail", outcome: "success", elapsed_ms: 40 }] : []),
    ],
    steps: [
      { name: "質問の理解", status: "completed", elapsed_seconds: 0.3, llm_calls: 1 },
      { name: "文書検索", status: "completed", elapsed_seconds: 0.5, llm_calls: 0 },
    ],
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
    const matched = rows.filter(
      (item) =>
        (!q || (item.question ?? "").includes(q)) &&
        (!surface || item.surface === surface) &&
        (!status || item.status === status) &&
        (!executor || (executor === "mine" ? item.executed_by_me : !item.executed_by_me))
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
  return { release, requests };
}

/** 表頭は固定され、表示行数（md 未満 5 行・md 以上 8 行）を超えた行は表の中で縦スクロールする。 */
async function expectScrollsInsideTable(page: Page, region: Locator) {
  await expect(region).toBeVisible();
  const expectedRows = (page.viewportSize()?.width ?? 1440) < 768 ? 5 : 8;
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
  test("system_admin: 読み込み中は経過時間と表の Skeleton、全員の実行と実行者の列、2 ページ目へ移りページは再読込でも残る", async ({
    page,
  }) => {
    await mockLocalAuth(page);
    const rows = Array.from({ length: 25 }, (_, index) =>
      run(index + 1, {
        executed_by_me: index === 0,
        executor_display_name: index === 0 ? "ローカル利用者" : index % 3 === 2 ? "" : `利用者 ${index + 1}`,
      })
    );
    const { release, requests } = await mockRuns(page, rows, { gated: true });

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
    await expect(table.getByRole("columnheader", { name: "実行者" })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: "利用者" })).toHaveCount(0);
    await expect(page.getByTestId("run-history-row-run-1")).toContainText("ローカル利用者");
    // 自分が実行した行には「〈名前〉 が実行」を出さず、他の人の行には出す。
    await expect(page.getByTestId("run-history-executed-by-run-1")).toHaveCount(0);
    await expect(page.getByTestId("run-history-executed-by-run-2")).toHaveText("利用者 2 が実行");
    // 引けない利用者は UUID を出さずに「削除済みのユーザー」。
    await expect(page.getByTestId("run-history-row-run-3")).toContainText("（削除済みのユーザー）");
    await expect(page.getByTestId("run-history-row-run-1")).toContainText("チャット / 受注業務");
    await expect(page.getByTestId("run-history-row-run-1")).toContainText("検証済み");
    await expect(page.getByTestId("run-history-row-run-2")).toContainText("回答なし（検索のみ）");
    await expectScrollsInsideTable(page, page.getByTestId("run-history-scroll-region"));
    expect(requests.at(-1)?.get("limit")).toBe(String(PAGE_SIZE));

    const pagination = page.getByTestId("run-history-pagination");
    await expect(pagination).toContainText("1–10 / 25 件");
    await pagination.getByRole("button", { name: "次へ" }).click();
    await expect(pagination).toContainText("11–20 / 25 件");
    await expect(page.getByTestId("run-history-row-run-11")).toBeVisible();
    expect(requests.at(-1)?.get("offset")).toBe("10");
    await expectNoPageOverflow(page);

    await page.reload();
    await expect(page.getByTestId("run-history-pagination")).toContainText("11–20 / 25 件");
  });

  test("一般の利用者: 自分の実行と、自分の会話で system_admin が実行した実行を実行者つきで出し、実行者で絞れる。ナビの「AI 活用」に History のアイコンで並ぶ", async ({
    page,
  }) => {
    await mockDatabaseReady(page);
    await mockAuthUser(page, { permissions: ["menu.chat", "menu.search", "menu.history"] });
    const adminRun = run(3, {
      question: "管理者が続けた質問",
      executed_by_me: false,
      executor_display_name: "システム管理者",
    });
    const { requests } = await mockRuns(page, [run(1), run(2), adminRun]);

    await page.goto("/history");
    await expect(page.getByTestId("run-history-scope")).toContainText("自分の会話で他の人が実行した実行");
    const table = page.getByRole("table", { name: "実行履歴" });
    await expect(table.getByRole("columnheader", { name: "質問" })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: "実行者" })).toBeVisible();
    // 持ち主の会話で system_admin が実行した行は、アイコンと文で示す（色だけに頼らない）。
    const executedBy = page.getByTestId("run-history-executed-by-run-3");
    await expect(executedBy).toHaveText("システム管理者 が実行");
    await expect(executedBy.locator("svg.lucide-user-round")).toBeVisible();
    await expect(page.getByTestId("run-history-executed-by-run-1")).toHaveCount(0);
    // 1 ページしかないときはページングを出さない。
    await expect(page.getByTestId("run-history-pagination")).toHaveCount(0);
    await expectNoPageOverflow(page);

    // 実行者の絞り込み（既定はすべて）。選んだ値は API の executor と作業状態に残す。
    const listRequests = () => requests.filter((params) => params.get("limit") !== "1");
    expect(listRequests().at(-1)?.get("executor")).toBeNull();
    const executorFilter = page.getByRole("combobox", { name: "実行者" });
    await expect(executorFilter).toContainText("すべて");
    await executorFilter.click();
    await page.getByRole("option", { name: "他の人が実行" }).click();
    await expect(page.getByTestId("run-history-row-run-3")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-1")).toHaveCount(0);
    expect(listRequests().at(-1)?.get("executor")).toBe("others");
    await expect(page.getByTestId("run-history-toolbar")).toContainText("1 件（全 3 件）");
    await page.reload();
    await expect(page.getByRole("combobox", { name: "実行者" })).toContainText("他の人が実行");
    await page.getByRole("combobox", { name: "実行者" }).click();
    await page.getByRole("option", { name: "自分が実行" }).click();
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

  test("空の状態と、絞り込みに一致しないときの「絞り込みをクリア」", async ({ page }) => {
    await mockLocalAuth(page);
    const { requests } = await mockRuns(page, [run(1), run(2, { status: "error", question: "失敗した質問" })]);

    await page.goto("/history");
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();

    await page.getByRole("button", { name: "失敗" }).click();
    await expect(page.getByTestId("run-history-row-run-2")).toBeVisible();
    await expect(page.getByTestId("run-history-row-run-1")).toHaveCount(0);
    // 絞り込み中は件数の「全 M 件」を数える 1 件の取得（limit=1・条件なし）も出るので、一覧の取得だけを見る（#1891）。
    const listRequests = () => requests.filter((params) => params.get("limit") !== "1");
    expect(listRequests().at(-1)?.get("status")).toBe("error");
    await expect(page.getByTestId("run-history-toolbar")).toContainText("1 件（全 2 件）");

    await page.getByRole("searchbox", { name: "質問で絞り込み" }).fill("存在しない");
    await expect(page.getByText("条件に一致する実行がありません")).toBeVisible();
    expect(listRequests().at(-1)?.get("q")).toBe("存在しない");
    await page.getByRole("button", { name: "絞り込みをクリア" }).click();
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
  });

  test("実行がまだ無いときの空の状態", async ({ page }) => {
    await mockLocalAuth(page);
    await mockRuns(page, []);
    await page.goto("/history");
    await expect(page.getByText("実行はまだありません")).toBeVisible();
    await expectNoPageOverflow(page);
  });

  test("詳細: 回答・根拠・回答の検証、処理の経過、フィードバック、会話への導線。一覧へ戻ると絞り込みを保つ", async ({
    page,
  }) => {
    await mockLocalAuth(page);
    await mockRuns(page, [run(1), run(2)]);

    await page.goto("/history");
    await page.getByTestId("run-history-open-run-1").click();
    await expect(page).toHaveURL(/\/history\/run-1$/);

    const summary = page.getByTestId("run-history-summary");
    await expect(summary).toContainText("受注の登録方法 1");
    await expect(summary).toContainText("検証済み");
    await expect(summary).toContainText("受注業務");
    await expect(page.getByTestId("run-history-open-chat")).toHaveAttribute(
      "href",
      /\/chat\?search_answer_profile_id=bv-1&conversation_id=conv-1$/
    );
    await expect(page.getByText("受注番号を入力して登録します。")).toBeVisible();
    await expect(page.getByTestId("run-history-citations")).toContainText("受注マニュアル.pdf");
    await expectNoPageOverflow(page);

    const tabs = page.getByRole("tablist", { name: "実行の詳細の表示" });
    await tabs.getByRole("tab", { name: "処理の経過" }).click();
    await expect(page.getByTestId("run-history-stage")).toHaveCount(2);
    await expect(page.getByTestId("run-history-stage").first()).toContainText("1.1 秒");
    await expect(page.getByTestId("run-history-step")).toHaveCount(2);

    await tabs.getByRole("tab", { name: "フィードバック（1）" }).click();
    await expect(page.getByTestId("run-history-feedback-open-fb-1")).toHaveAttribute(
      "href",
      /\/feedback\?feedback=fb-1&period=all$/
    );

    await page.getByRole("button", { name: "実行履歴の一覧へ戻る" }).click();
    await expect(page).toHaveURL(/\/history$/);
    await expect(page.getByTestId("run-history-row-run-1")).toBeVisible();
  });

  test("詳細: 回答を作らない RAG 検索は根拠だけ、見えない実行は理由を出す", async ({ page }) => {
    await mockLocalAuth(page);
    await mockRuns(page, [run(2)]);

    await page.goto("/history/run-2");
    await expect(page.getByText("この実行は回答を作っていません（RAG 検索の検索のみ）。")).toBeVisible();
    await expect(page.getByTestId("run-history-citations")).toContainText("受注マニュアル.pdf");
    // 会話の無い実行には会話への導線を出さない。
    await expect(page.getByTestId("run-history-open-chat")).toHaveCount(0);

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
    await expect(page.getByTestId("run-history-detail-executor")).toHaveText("システム管理者");
    await expect(page.getByTestId("run-history-detail-conversation-owner")).toHaveText("アリス");
    await expect(page.getByTestId("run-history-open-chat")).toHaveAttribute(
      "href",
      /\/chat\?search_answer_profile_id=bv-1&conversation_id=conv-1$/
    );
    await expectNoPageOverflow(page);

    await page.goto("/history/run-3");
    await expect(page.getByTestId("run-history-detail-conversation-owner")).toHaveText("ボブ");
    await expect(page.getByTestId("run-history-open-chat")).toHaveCount(0);
  });
});
