import { expect, test, type Page, type TestInfo } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth, dbUser } from "./_helpers";

/**
 * 一覧の読み取りの失敗を、0 件の空の状態として出さない（#4217）。
 * timeout・接続不可・本当に空・前回の一覧ありの再取得の失敗を、desktop と 375px で確かめる。
 */

type Failure = { status: number; code: string; message: string };

const TIMEOUT: Failure = {
  status: 503,
  code: "database_read_timeout",
  message: "データの読み取りがタイムアウトしました。データは失われていません。",
};
const UNAVAILABLE: Failure = {
  status: 503,
  code: "database_unavailable",
  message: "データベースに接続できませんでした。データは失われていません。",
};

const SUMMARY = {
  id: "kb-1",
  name: "ナレッジベース A",
  description: "",
  status: "ACTIVE",
  default_search_mode: "hybrid",
  document_count: 1,
  indexed_document_count: 1,
  error_document_count: 0,
  searchable_chunk_count: 1,
  retrieval_config: {},
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

function okPage(items: unknown[]) {
  return {
    data: { items, total: items.length, limit: 25, offset: 0, has_next: false },
    error_messages: [],
    warning_messages: [],
  };
}

function failureBody(failure: Failure) {
  return { data: null, error_messages: [failure.message], warning_messages: [], error_code: failure.code };
}

/** 一覧の API を差し替える。`respond` が null なら 1 件の一覧を返す。 */
async function routeList(page: Page, respond: () => Failure | null, items: unknown[] = [SUMMARY]) {
  await page.route("**/api/knowledge-bases**", (route) => {
    // 件数専用APIは数えない（一覧の読み取りの回数だけを確かめる）。
    if (isTotalRequest(route.request().url())) return route.fulfill({ json: { data: items.length, error_messages: [], warning_messages: [] } });
    const failure = respond();
    return failure
      ? route.fulfill({ status: failure.status, json: failureBody(failure) })
      : route.fulfill({ json: okPage(items) });
  });
}

function isTotalRequest(url: string) {
  const parsed = new URL(url);
  return parsed.pathname === "/api/knowledge-bases/count" || parsed.searchParams.get("limit") === "1";
}

/** 自動の再試行（5 秒・15 秒の間隔で 2 回）を進める。 */
async function runAutoRetries(page: Page, requests: () => number) {
  await expect.poll(requests).toBe(1);
  await page.clock.runFor(5_100);
  await expect.poll(requests).toBe(2);
  await page.clock.runFor(15_100);
  await expect.poll(requests).toBe(3);
}

async function screenshot(page: Page, testInfo: TestInfo, name: string) {
  await page.screenshot({ path: testInfo.outputPath(`${name}.png`), fullPage: true });
}

async function expectNoEmptyState(page: Page) {
  await expect(page.getByText("ナレッジベースがありません")).toHaveCount(0);
  await expect(page.getByText("最初のナレッジベースを作成")).toHaveCount(0);
  await expect(page.getByTestId("knowledge-base-list-toolbar").getByText(/全 \d+ 件/)).toHaveCount(0);
}

test.describe("一覧の読み取りの失敗", { tag: "@responsive" }, () => {
  test("timeout: 空の状態と件数を出さず、事実を伝え、作成を止め、再試行へフォーカスする", async ({ page }, testInfo) => {
    await mockLocalAuth(page);
    await page.clock.install();
    let requests = 0;
    let failing = true;
    await routeList(page, () => {
      requests += 1;
      return failing ? TIMEOUT : null;
    });

    await page.goto("/knowledge-bases");

    // 自動の再試行の間は経過時間つきの読み込み中で、空の状態も失敗も出さない。作成も止める。
    await expect.poll(() => requests).toBe(1);
    await expect(page.getByTestId("knowledge-bases-loading")).toBeVisible();
    await expect(page.getByTestId("knowledge-bases-loading").getByRole("timer")).toBeVisible();
    await expect(page.getByRole("button", { name: "新規作成" })).toBeDisabled();
    await expectNoEmptyState(page);

    await runAutoRetries(page, () => requests);

    const failure = page.getByTestId("knowledge-bases-load-failure");
    const alert = failure.getByRole("alert");
    await expect(alert).toContainText("読み取りがタイムアウトしました");
    await expect(alert).toContainText("データは失われていません");
    await expect(alert).not.toContainText("接続できません");
    // データベースの問題と断定する導線・DB ゲートを出さない。
    await expect(page.getByRole("link", { name: /データベース設定を開く/ })).toHaveCount(0);
    await expect(failure.getByRole("button", { name: "再試行" })).toBeFocused();
    await expect(page.getByRole("button", { name: "新規作成" })).toBeDisabled();
    await expectNoEmptyState(page);
    await expectNoPageOverflow(page);
    await screenshot(page, testInfo, "timeout");

    // 再試行で読めれば一覧を出し、作成を戻す。
    failing = false;
    await failure.getByRole("button", { name: "再試行" }).click();
    await expect(page.getByTestId("knowledge-base-row-kb-1")).toBeVisible();
    await expect(page.getByRole("button", { name: "新規作成" })).toBeEnabled();
    await expect(page.getByTestId("knowledge-bases-load-failure")).toHaveCount(0);
    expect(requests).toBe(4);
  });

  test("接続不可: 管理者にはデータベース設定へ案内する", async ({ page }, testInfo) => {
    await mockLocalAuth(page);
    await page.clock.install();
    let requests = 0;
    await routeList(page, () => {
      requests += 1;
      return UNAVAILABLE;
    });

    await page.goto("/knowledge-bases");
    await runAutoRetries(page, () => requests);

    const alert = page.getByTestId("knowledge-bases-load-failure").getByRole("alert");
    await expect(alert).toContainText("データベースに接続できません");
    await expect(alert).toContainText("データは失われていません");
    await expect(alert.getByRole("link", { name: /データベース設定を開く/ })).toHaveAttribute(
      "href",
      "/settings/database#adb-management",
    );
    await expect(page.getByRole("button", { name: "新規作成" })).toBeDisabled();
    await expectNoEmptyState(page);
    await expectNoPageOverflow(page);
    await screenshot(page, testInfo, "unavailable");
  });

  test("接続不可: 設定を開けない利用者には導線を出さず、管理者への連絡を案内する", async ({ page }) => {
    await mockDatabaseReady(page);
    await page.route("**/api/auth/me", (route) =>
      route.fulfill({
        json: {
          data: dbUser({ permissions: ["menu.knowledge_bases"] }),
          error_messages: [],
          warning_messages: [],
        },
      }),
    );
    await page.clock.install();
    let requests = 0;
    await routeList(page, () => {
      requests += 1;
      return UNAVAILABLE;
    });

    await page.goto("/knowledge-bases");
    await runAutoRetries(page, () => requests);

    const alert = page.getByTestId("knowledge-bases-load-failure").getByRole("alert");
    await expect(alert).toContainText("システム管理者に連絡してください");
    await expect(page.getByRole("link", { name: /データベース設定を開く/ })).toHaveCount(0);
  });

  test("本当に空: 0 件と作成の案内を出す", async ({ page }, testInfo) => {
    await mockLocalAuth(page);
    await routeList(page, () => null, []);

    await page.goto("/knowledge-bases");

    await expect(page.getByText("ナレッジベースがありません")).toBeVisible();
    await expect(page.getByTestId("knowledge-bases-load-failure")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "新規作成" })).toBeEnabled();
    await expectNoPageOverflow(page);
    await screenshot(page, testInfo, "empty");
  });

  test("前回の一覧あり: 一覧を残し、いつの内容かを示す", async ({ page }, testInfo) => {
    await mockLocalAuth(page);
    await page.clock.install();
    let requests = 0;
    let failing = false;
    await routeList(page, () => {
      requests += 1;
      return failing ? TIMEOUT : null;
    });

    await page.goto("/knowledge-bases");
    const row = page.getByTestId("knowledge-base-row-kb-1");
    await expect(row).toBeVisible();

    // 画面に戻ったときの取り直し（window focus）で失敗させる。
    failing = true;
    const before = requests;
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await page.clock.runFor(1_000);
    await expect.poll(() => requests).toBeGreaterThan(before);
    await page.clock.runFor(5_100);
    await expect.poll(() => requests).toBe(before + 2);
    await page.clock.runFor(15_100);
    await expect.poll(() => requests).toBe(before + 3);

    const notice = page.getByTestId("knowledge-bases-stale");
    await expect(notice).toContainText("時点の内容を表示しています");
    await expect(notice).toContainText("読み取りがタイムアウトしました");
    await expect(notice).toContainText("データは失われていません");
    await expect(row).toBeVisible();
    await expect(page.getByTestId("knowledge-bases-load-failure")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "新規作成" })).toBeDisabled();
    await expectNoPageOverflow(page);
    await screenshot(page, testInfo, "stale");

    // 手動の再試行で戻す。
    failing = false;
    await notice.getByRole("button", { name: "再試行" }).click();
    await expect(notice).toHaveCount(0);
    await expect(row).toBeVisible();
    await expect(page.getByRole("button", { name: "新規作成" })).toBeEnabled();
  });

  test("文書・検索・回答プロファイルの一覧も同じ形で失敗を出す", async ({ page }) => {
    await mockLocalAuth(page);
    await page.clock.install();
    let documentRequests = 0;
    let profileRequests = 0;
    await routeList(page, () => null);
    await page.route("**/api/documents**", (route) => {
      documentRequests += 1;
      return route.fulfill({ status: TIMEOUT.status, json: failureBody(TIMEOUT) });
    });
    await page.route("**/api/search-answer-profiles**", (route) => {
      if (isTotalRequest(route.request().url())) return route.fulfill({ json: okPage([]) });
      profileRequests += 1;
      return route.fulfill({ status: TIMEOUT.status, json: failureBody(TIMEOUT) });
    });

    await page.goto("/file-list");
    await runAutoRetries(page, () => documentRequests);
    await expect(page.getByTestId("file-list-load-failure").getByRole("alert")).toContainText(
      "読み取りがタイムアウトしました",
    );
    await expect(page.getByText("該当するドキュメントがありません。")).toHaveCount(0);
    await expectNoPageOverflow(page);

    await page.goto("/search-answer-profiles");
    await runAutoRetries(page, () => profileRequests);
    await expect(page.getByTestId("search-answer-profiles-load-failure").getByRole("alert")).toContainText(
      "読み取りがタイムアウトしました",
    );
    await expect(page.getByRole("button", { name: "新規作成" })).toBeDisabled();
    await expectNoPageOverflow(page);
  });
});
