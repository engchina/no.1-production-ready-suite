import { expect, type Locator, type Page, test } from "@playwright/test";

import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// 1 ページ（50 件）を超える KB。先頭のページだけで打ち切らないことを確かめる（#302）。
const KB_COUNT = 120;
const PAGE_SIZE = 50;

interface KnowledgeBaseRequest {
  q: string | null;
  offset: number;
  ids: string[];
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

test("候補はサーバー側で検索し、「さらに表示」で次のページを取る", async ({ page }) => {
  const requests = await mockManyKnowledgeBases(page);
  await page.goto("/evaluation");

  // 既定はトリガーのみで、リストは畳まれている(ページを押し下げない)。
  const combobox = page.getByRole("combobox", { name: "知識ベース" });
  await expect(combobox).toBeVisible();
  await expect(page.getByRole("listbox", { name: "知識ベース" })).toHaveCount(0);

  await combobox.click();
  const listbox = page.getByRole("listbox", { name: "知識ベース" });
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option").first()).toContainText("DEFAULT");
  await expect(listbox.getByRole("option")).toHaveCount(PAGE_SIZE);
  await expect(page.getByText(`${PAGE_SIZE} / ${KB_COUNT} 件`)).toBeVisible();

  // リストは高さ固定でスクロール可能。
  const scrollable = await page.evaluate(() => {
    const list = document.querySelector('[role="listbox"][aria-label="知識ベース"]');
    if (!list) return false;
    return list.scrollHeight > list.clientHeight + 1;
  });
  expect(scrollable).toBe(true);

  // 次のページを取り、候補に足す。
  await page.getByRole("button", { name: "さらに表示" }).click();
  await expect(listbox.getByRole("option")).toHaveCount(PAGE_SIZE * 2);
  await expect(page.getByText(`${PAGE_SIZE * 2} / ${KB_COUNT} 件`)).toBeVisible();
  expect(requests.some((request) => request.offset === PAGE_SIZE && request.q === null)).toBe(true);
  await page.getByRole("button", { name: "さらに表示" }).click();
  await expect(listbox.getByRole("option")).toHaveCount(KB_COUNT);
  await expect(page.getByRole("button", { name: "さらに表示" })).toHaveCount(0);

  // 検索語はサーバーへ送る（画面側の絞り込みではない）。101 件目以降の KB も検索で選べる。
  await combobox.fill("-115");
  await expect(listbox.getByRole("option")).toHaveCount(1);
  await expect(listbox.getByRole("option", { name: /ナレッジベース-115/ })).toBeVisible();
  expect(requests.some((request) => request.q === "-115" && request.offset === 0)).toBe(true);

  // 一致しない検索語は一覧内で伝える。
  await combobox.fill("存在しない名前");
  await expect(page.getByText("「存在しない名前」に一致する知識ベースがありません。")).toBeVisible();

  await expectNoPageOverflow(page);
});

test("検索で選んだ KB は、検索語を消して候補のページに無くなってもチップに名前が残る", async ({
  page,
}) => {
  await mockManyKnowledgeBases(page);
  await page.goto("/evaluation");

  const combobox = page.getByRole("combobox", { name: "知識ベース" });
  await combobox.click();
  await combobox.fill("-118");
  await page.getByRole("option", { name: /ナレッジベース-118/ }).click();
  await combobox.fill("");

  // 先頭のページ（50 件）に無い KB でも、ID で引いた名前をチップに出す。
  await expect(page.getByLabel("ナレッジベース-118 を選択から外す")).toBeVisible();
  await expect(page.getByText("1 件選択中").first()).toBeVisible();

  // 表示中をすべて選択・クリアは読み込んだ候補に対して働く（検索語を消した結果を待つ）。
  await expect(page.getByRole("listbox", { name: "知識ベース" }).getByRole("option")).toHaveCount(
    PAGE_SIZE
  );
  await page.getByRole("button", { name: "表示中をすべて選択" }).click();
  await expect(page.getByText(`${PAGE_SIZE + 1} 件選択中`).first()).toBeVisible();
  await page.getByLabel("ナレッジベース-07 を選択から外す").click();
  await expect(page.getByText(`${PAGE_SIZE} 件選択中`).first()).toBeVisible();
  await page.getByRole("button", { name: "クリア" }).click();
  await expect(page.getByText("利用できるすべての知識ベースを対象にします。")).toBeVisible();
});

test("選んでも一覧は開いたままで、「完了」で閉じて入力欄にフォーカスが戻る（#316）", async ({
  page,
}) => {
  await mockManyKnowledgeBases(page);
  await page.goto("/evaluation");

  const combobox = page.getByRole("combobox", { name: "知識ベース" });
  const listbox = page.getByRole("listbox", { name: "知識ベース" });
  await combobox.click();
  await expect(listbox).toBeVisible();

  // 続けて複数を選べる（選ぶたびに閉じない）。
  await listbox.getByRole("option", { name: /ナレッジベース-02/ }).click();
  await expect(listbox).toBeVisible();
  await listbox.getByRole("option", { name: /ナレッジベース-03/ }).click();
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option", { selected: true })).toHaveCount(2);

  await page.getByRole("button", { name: "完了" }).click();
  await expect(listbox).toHaveCount(0);
  await expect(combobox).toHaveAttribute("aria-expanded", "false");
  await expect(combobox).toBeFocused();
  // 閉じた後も、選んだ結果はチップと件数で分かる。
  await expect(page.getByLabel("ナレッジベース-02 を選択から外す")).toBeVisible();
  await expect(page.getByLabel("ナレッジベース-03 を選択から外す")).toBeVisible();
  await expect(page.getByText("2 件選択中")).toBeVisible();

  // 入力欄の右の開閉ボタン（chevron）でも今までどおり開閉できる。
  const toggle = page.getByRole("button", { name: "知識ベースの一覧を開閉" });
  await toggle.click();
  await expect(listbox).toBeVisible();
  await toggle.click();
  await expect(listbox).toHaveCount(0);

  await expectNoPageOverflow(page);
});

test("キーボードで選び、Esc で閉じて入力欄に戻り、Tab で外へ出ると閉じる（#316）", async ({
  page,
}) => {
  await mockManyKnowledgeBases(page);
  await page.goto("/evaluation");

  const combobox = page.getByRole("combobox", { name: "知識ベース" });
  const listbox = page.getByRole("listbox", { name: "知識ベース" });
  const doneButton = page.getByRole("button", { name: "完了" });

  // 矢印と Enter で選ぶ。選んでも開いたまま。
  await combobox.focus();
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option")).toHaveCount(PAGE_SIZE);
  await combobox.press("ArrowDown");
  await combobox.press("Enter");
  await expect(listbox.getByRole("option", { selected: true })).toHaveCount(1);
  await expect(listbox).toBeVisible();

  // Esc で閉じ、フォーカスは入力欄に残る（入力欄へ戻したことで開き直さない）。
  await combobox.press("Escape");
  await expect(listbox).toHaveCount(0);
  await expect(combobox).toBeFocused();
  await expect(combobox).toHaveAttribute("aria-expanded", "false");

  // ↓ で開き直し、Tab で一覧の中の操作（さらに表示・完了など）へ進んでも閉じない。
  await combobox.press("ArrowDown");
  await expect(listbox).toBeVisible();
  await tabUntilFocused(page, doneButton);
  await expect(listbox).toBeVisible();

  // 一覧の中の操作にフォーカスがあるときも、Esc で閉じて入力欄へ戻る。
  await page.keyboard.press("Escape");
  await expect(listbox).toHaveCount(0);
  await expect(combobox).toBeFocused();

  // 最後の操作（完了）から Tab で外へ出ると閉じる。
  await combobox.press("ArrowDown");
  await tabUntilFocused(page, doneButton);
  await page.keyboard.press("Tab");
  await expect(listbox).toHaveCount(0);
  await expect(combobox).not.toBeFocused();
  await expect(page.getByLabel(/を選択から外す$/)).toHaveCount(1);

  // Shift+Tab で入力欄より前（チップの外）へ出たときも閉じる。
  await combobox.focus();
  await expect(listbox).toBeVisible();
  await page.keyboard.press("Shift+Tab");
  await expect(page.getByLabel(/を選択から外す$/)).toBeFocused();
  await expect(listbox).toBeVisible();
  await page.keyboard.press("Shift+Tab");
  await expect(listbox).toHaveCount(0);
});

/** 一覧を開いたまま Tab を押し、対象にフォーカスが来るまで進める（途中で閉じないことも確かめる）。 */
async function tabUntilFocused(page: Page, target: Locator) {
  const listbox = page.getByRole("listbox", { name: "知識ベース" });
  for (let step = 0; step < 12; step += 1) {
    const focused = await target
      .evaluate((node) => node === document.activeElement, undefined, { timeout: 1_000 })
      .catch(() => false);
    if (focused) return;
    await page.keyboard.press("Tab");
    await expect(listbox).toBeVisible();
  }
  await expect(target).toBeFocused();
}

async function mockManyKnowledgeBases(page: Page): Promise<KnowledgeBaseRequest[]> {
  const requests: KnowledgeBaseRequest[] = [];
  const all = Array.from({ length: KB_COUNT }, (_, index) => {
    const label = String(index + 1).padStart(2, "0");
    return {
      id: `kb-${label}`,
      name: index === 0 ? "DEFAULT" : `ナレッジベース-${label}`,
      description: null,
      status: "ACTIVE",
      default_search_mode: "hybrid",
      document_count: index,
      indexed_document_count: index,
      error_document_count: 0,
      searchable_chunk_count: index * 2,
      created_at: "2026-06-15T00:00:00Z",
      updated_at: "2026-06-15T00:00:00Z",
      archived_at: null,
    };
  });

  await page.route("**/api/knowledge-bases**", async (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get("q");
    const ids = url.searchParams.getAll("ids");
    const limit = Number(url.searchParams.get("limit") ?? 50);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    requests.push({ q, offset, ids });
    const filtered = all.filter(
      (item) =>
        (ids.length === 0 || ids.includes(item.id)) && (!q || item.name.includes(q.trim()))
    );
    await route.fulfill({
      json: {
        data: {
          items: filtered.slice(offset, offset + limit),
          total: filtered.length,
          limit,
          offset,
          has_next: offset + limit < filtered.length,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  return requests;
}
