import { expect, type Locator, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// Issue 265: 読み込み中・一覧の縦スクロール・ページングを NL2SQL の基準にそろえる。
// - 読み込み中は TimedLoadingState（文言と経過時間）+ 表の形の Skeleton で領域を予約する
// - 一覧は表頭を固定し、md 未満 5 行・md 以上 8 行を超えた行は表の中で縦スクロールする
// - ページングは共通の Pagination（10 件/ページ）。ページ番号は再読込でも残る
// desktop / mobile（375px）の両 project で動く。

const PAGE_SIZE = 10;

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

test("文書一覧: 読み込み中は経過時間と表の Skeleton、表の中で縦スクロールし、ページは再読込でも残る", async ({ page }) => {
  const release = await mockDocuments(page, 25);

  await page.goto("/file-list");
  const loading = page.getByTestId("file-list-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toHaveAttribute("aria-busy", "true");
  await expect(loading).toContainText("文書一覧を読み込んでいます");
  await expect(loading.getByRole("timer")).toBeVisible();
  await expect(loading.locator('[data-skeleton="table"]')).toBeVisible();
  await expectNoPageOverflow(page);
  release();

  await expect(loading).toHaveCount(0);
  const region = page.getByTestId("file-list-scroll-region");
  await expectScrollsInsideTable(page, region);

  const pagination = page.getByTestId("file-list-pagination");
  await expect(pagination).toContainText("1 - 10 / 25 件");
  await expect(pagination).toContainText("1 / 3 ページ");
  await expect(pagination.getByRole("button", { name: "前へ" })).toBeDisabled();
  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("11 - 20 / 25 件");
  await expect(page.getByRole("link", { name: "doc-11.txt" })).toBeVisible();
  await expectNoPageOverflow(page);

  // ページ番号は作業状態として残す（workspace-state.md）。
  await page.reload();
  await expect(page.getByTestId("file-list-pagination")).toContainText("11 - 20 / 25 件");
  await expect(page.getByRole("link", { name: "doc-11.txt" })).toBeVisible();
});

test("業務ビュー一覧: 読み込み中は経過時間と表の Skeleton、2 ページ目へ移れる", async ({ page }) => {
  const release = await mockBusinessViews(page, 12);

  await page.goto("/business-views");
  const loading = page.getByTestId("business-views-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toContainText("業務ビューを読み込んでいます");
  await expect(loading.getByRole("timer")).toBeVisible();
  release();

  await expect(loading).toHaveCount(0);
  await expectScrollsInsideTable(page, page.getByTestId("business-views-scroll-region"));
  const pagination = page.getByTestId("business-views-pagination");
  await expect(pagination).toContainText("1 - 10 / 12 件");
  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("11 - 12 / 12 件");
  await expect(page.getByTestId("business-view-row-bv-12")).toBeVisible();
  await expectNoPageOverflow(page);
});

test("チャットの会話一覧: 読み込み中は経過時間と行の Skeleton、サーバー側のページングで 2 ページ目へ移れる", async ({ page }) => {
  const { release, requests } = await mockConversations(page, 25);

  await page.goto("/chat?business_view_id=bv-1");
  const loading = page.getByTestId("chat-conversations-loading");
  await expect(loading).toBeVisible();
  await expect(loading).toContainText("会話を読み込んでいます");
  await expect(loading.getByRole("timer")).toBeVisible();
  await expect(loading.locator('[data-skeleton="list"]')).toBeVisible();
  release();

  await expect(loading).toHaveCount(0);
  const list = page.getByTestId("chat-conversation-list");
  await expect(list.getByRole("listitem")).toHaveCount(PAGE_SIZE);
  // 50 件で打ち切らず、1 ページ 10 件を offset / limit で取得する。
  expect(requests.at(-1)).toEqual({ limit: PAGE_SIZE, offset: 0 });

  // lg 未満は md 未満 5 行・md 以上 8 行の高さで中をスクロールし、lg 以上は会話エリアの高さに合わせる。
  const width = page.viewportSize()?.width ?? 1440;
  const metrics = await list.evaluate((element) => ({
    maxHeight: getComputedStyle(element).maxHeight,
    rem: Number.parseFloat(getComputedStyle(document.documentElement).fontSize),
    scrollable: element.scrollHeight > element.clientHeight + 1,
  }));
  if (width < 1024) {
    const expectedRem = width < 768 ? 17.5 : 28;
    expect(Math.abs(Number.parseFloat(metrics.maxHeight) - expectedRem * metrics.rem)).toBeLessThanOrEqual(1);
    expect(metrics.scrollable).toBe(true);
  } else {
    expect(metrics.maxHeight).toBe("none");
  }

  const pagination = page.getByTestId("chat-conversations-pagination");
  await expect(pagination).toContainText("1 - 10 / 25 件");
  await expect(pagination).toContainText("1 / 3 ページ");
  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("11 - 20 / 25 件");
  await expect(list.getByText("会話 11", { exact: true })).toBeVisible();
  expect(requests.at(-1)).toEqual({ limit: PAGE_SIZE, offset: PAGE_SIZE });
  await expectNoPageOverflow(page);

  // ページ番号は作業状態として残す（workspace-state.md）。
  await page.reload();
  await expect(page.getByTestId("chat-conversations-pagination")).toContainText("11 - 20 / 25 件");
});

/** 表頭は固定され、表示行数（md 未満 5 行・md 以上 8 行）を超えた行は表の中で縦スクロールする。 */
async function expectScrollsInsideTable(page: Page, region: Locator) {
  await expect(region).toBeVisible();
  const expectedRows = (page.viewportSize()?.width ?? 1440) < 768 ? 5 : 8;
  const metrics = await region.evaluate((element) => {
    const header = element.querySelector("thead")!;
    const rows = Array.from(element.querySelectorAll('tbody tr[data-row-kind="data"]'));
    const top = element.getBoundingClientRect().top;
    const visible = rows.filter((row) => row.getBoundingClientRect().bottom <= element.getBoundingClientRect().bottom + 1);
    return {
      sticky: getComputedStyle(header).position,
      scrollable: element.scrollHeight > element.clientHeight + 1,
      visibleRows: visible.length,
      headerTop: header.getBoundingClientRect().top - top,
    };
  });
  expect(metrics.sticky).toBe("sticky");
  expect(metrics.scrollable).toBe(true);
  expect(metrics.visibleRows).toBe(expectedRows);
  // 表の中をスクロールしても表頭は上端に残る。
  await region.evaluate((element) => element.scrollTo({ top: element.scrollHeight }));
  const headerOffset = await region.evaluate(
    (element) => element.querySelector("thead")!.getBoundingClientRect().top - element.getBoundingClientRect().top
  );
  expect(Math.abs(headerOffset)).toBeLessThanOrEqual(1);
}

async function mockDocuments(page: Page, count: number) {
  const documents = Array.from({ length: count }, (_, index) => ({
    id: `doc-${index + 1}`,
    file_name: `doc-${String(index + 1).padStart(2, "0")}.txt`,
    status: "INDEXED",
    category_name: null,
    content_type: "text/plain",
    file_size_bytes: 128,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-09-28T00:00:00Z",
    indexed_at: "2026-09-28T00:01:00Z",
    knowledge_bases: [],
    source_profile: null,
  }));
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: envelope({ items: [], total: 0, limit: 200, offset: 0, has_next: false }) })
  );
  await page.route("**/api/documents**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET" || url.pathname !== "/api/documents") {
      await route.fulfill({ json: envelope([]) });
      return;
    }
    await gate;
    const limit = Number(url.searchParams.get("limit") ?? PAGE_SIZE);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    await route.fulfill({
      json: envelope({
        items: documents.slice(offset, offset + limit),
        total: documents.length,
        limit,
        offset,
        has_next: offset + limit < documents.length,
      }),
    });
  });
  return release;
}

async function mockBusinessViews(page: Page, count: number) {
  const views = Array.from({ length: count }, (_, index) => ({
    id: `bv-${index + 1}`,
    name: `業務ビュー ${String(index + 1).padStart(2, "0")}`,
    description: "",
    status: "ACTIVE",
    knowledge_base_count: 1,
    created_at: "2026-09-28T00:00:00Z",
    updated_at: "2026-09-28T00:00:00Z",
    archived_at: null,
  }));
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/business-views**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET" || url.pathname !== "/api/business-views") {
      await route.fulfill({ status: 404, json: { detail: "not found" } });
      return;
    }
    await gate;
    const limit = Number(url.searchParams.get("limit") ?? PAGE_SIZE);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    await route.fulfill({
      json: envelope({
        items: views.slice(offset, offset + limit),
        total: views.length,
        limit,
        offset,
        has_next: offset + limit < views.length,
      }),
    });
  });
  return release;
}

function envelope<T>(data: T) {
  return { data, error_messages: [], warning_messages: [] };
}

async function mockConversations(page: Page, count: number) {
  const conversations = Array.from({ length: count }, (_, index) => ({
    id: `conv-${index + 1}`,
    business_view_id: "bv-1",
    title: `会話 ${String(index + 1).padStart(2, "0")}`,
    status: "ACTIVE",
    message_count: 2,
    created_at: "2026-09-28T00:00:00Z",
    updated_at: "2026-09-28T00:00:00Z",
  }));
  const requests: { limit: number; offset: number }[] = [];
  let release: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/business-views**", (route) =>
    route.fulfill({
      json: envelope({
        items: [
          {
            id: "bv-1",
            name: "経理アシスタント",
            description: "",
            status: "ACTIVE",
            knowledge_base_count: 1,
            created_at: "2026-09-28T00:00:00Z",
            updated_at: "2026-09-28T00:00:00Z",
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
        has_next: false,
      }),
    })
  );
  await page.route("**/api/chat/models", (route) => route.fulfill({ json: envelope([]) }));
  await page.route("**/api/chat/conversations**", async (route) => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET" || url.pathname !== "/api/chat/conversations") {
      await route.fulfill({ status: 404, json: { detail: "not found" } });
      return;
    }
    await gate;
    const limit = Number(url.searchParams.get("limit") ?? 50);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    requests.push({ limit, offset });
    await route.fulfill({
      json: envelope({
        items: conversations.slice(offset, offset + limit),
        total: conversations.length,
        limit,
        offset,
        has_next: offset + limit < conversations.length,
      }),
    });
  });
  return { release, requests };
}
