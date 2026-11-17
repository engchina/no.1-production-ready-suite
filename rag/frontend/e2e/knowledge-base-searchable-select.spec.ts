import { openFileListFilters, closeFileListFilters } from "./_helpers";
import { expect, type Locator, type Page, test } from "./fixtures/test";

import {
  apiEnvelope,
  expectedControlHeight,
  expectNoPageOverflow,
  expectSearchablePanelFits,
  mockDatabaseReady,
  mockLocalAuth,
  openSearchableMultiSelect,
  searchableMultiSelectTrigger,
} from "./_helpers";

// ナレッジベースが数百件でも、検索して選べる（#578）。
// - 200 件以下は全件を 1 回で読み、画面側で絞り込む。
// - 201 件以上はサーバー側の検索（GET /api/knowledge-bases?q=）に切り替え、50 件ずつ続きを読む（全件を読まない）。
const MANY = 300;
const PAGE_SIZE = 50;

interface KnowledgeBaseRequest {
  q: string | null;
  limit: number;
  offset: number;
  ids: string[];
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await mockUploadPage(page);
  await mockSearchAnswerProfiles(page);
});

test("300 件: 候補はサーバー側で検索し、「さらに表示」で続きを読む（全件を読まない）", async ({ page }) => {
  const requests = await mockKnowledgeBases(page, MANY);
  await page.goto("/search-answer-profiles?id=new");

  // 既定はボタンだけで、検索欄と一覧は開いた popover の中にある（ページを押し下げない。#5267）。
  const trigger = searchableMultiSelectTrigger(page, "参照するナレッジベース");
  const listbox = page.getByRole("listbox", { name: "参照するナレッジベース" });
  await expect(trigger).toBeVisible();
  await expect(trigger).toContainText("未選択");
  await expect(listbox).toHaveCount(0);

  const combobox = await openSearchableMultiSelect(page, "参照するナレッジベース");
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option")).toHaveCount(PAGE_SIZE);
  await expect(page.getByText(`${PAGE_SIZE} / ${MANY} 件`).first()).toBeVisible();

  // 一覧は高さが決まっていて中でスクロールする。
  expect(await listbox.evaluate((node) => node.scrollHeight > node.clientHeight + 1)).toBe(true);

  await page.getByRole("button", { name: "さらに表示" }).click();
  await expect(listbox.getByRole("option")).toHaveCount(PAGE_SIZE * 2);
  await expect(page.getByText(`${PAGE_SIZE * 2} / ${MANY} 件`).first()).toBeVisible();

  // 検索語はサーバーへ送る（画面側の絞り込みではない）。読んでいないページの KB も検索で選べる。
  await combobox.fill("-275");
  await expect(listbox.getByRole("option")).toHaveCount(1);
  await expect(listbox.getByRole("option", { name: /ナレッジベース-275/ })).toBeVisible();
  expect(requests.some((request) => request.q === "-275" && request.offset === 0)).toBe(true);

  await combobox.fill("存在しない名前");
  await expect(page.getByText("「存在しない名前」に一致するナレッジベースがありません。")).toBeVisible();

  // 全件をページ送りで読み切らない（先頭の 200 件の確認と、開いた 2 ページだけ）。
  const unfiltered = requests.filter((request) => request.q === null && request.ids.length === 0);
  expect(Math.max(...unfiltered.map((request) => request.offset))).toBeLessThan(PAGE_SIZE * 2);
  await expectNoPageOverflow(page);
});

test("200 件以下は全件を 1 回で読み、検索語をサーバーへ送らずに画面側で絞り込む", async ({ page }) => {
  const requests = await mockKnowledgeBases(page, 120);
  await page.goto("/search-answer-profiles?id=new");

  const listbox = page.getByRole("listbox", { name: "参照するナレッジベース" });
  const combobox = await openSearchableMultiSelect(page, "参照するナレッジベース");
  await expect(listbox.getByRole("option")).toHaveCount(120);
  await expect(page.getByRole("button", { name: "さらに表示" })).toHaveCount(0);
  // 既定の KB が先頭、その後は名前の順（#2753）。文書の最も多い KB の「最多」は並びに依らず付ける。
  await expect(listbox.getByRole("option").first()).toContainText("DEFAULT");
  await expect(listbox.getByRole("option").nth(1)).toContainText("ナレッジベース-002");
  await expect(listbox.getByRole("option").last()).toContainText("ナレッジベース-120");
  await expect(listbox.getByRole("option").last()).toContainText("最多");

  // 「-110」〜「-119」の 10 件
  await combobox.fill("-11");
  await expect(listbox.getByRole("option")).toHaveCount(10);
  await expect(page.getByText("10 / 120 件").first()).toBeVisible();
  expect(requests.some((request) => request.q !== null)).toBe(false);
});

test("検索で選んだ KB は、検索語を消して候補のページに無くなっても chip に名前が残る", async ({ page }) => {
  await mockKnowledgeBases(page, MANY);
  await page.goto("/search-answer-profiles?id=new");

  const listbox = page.getByRole("listbox", { name: "参照するナレッジベース" });
  const combobox = await openSearchableMultiSelect(page, "参照するナレッジベース");
  await combobox.fill("-288");
  await listbox.getByRole("option", { name: /ナレッジベース-288/ }).click();
  await combobox.fill("");

  // 先頭のページ（50 件）に無い KB でも、ID で引いた名前を chip とボタンの要約に出す。
  const chips = page.getByRole("list", { name: "選択中のナレッジベース" });
  await expect(chips.getByLabel("ナレッジベース-288 を選択から外す")).toBeAttached();
  await expect(searchableMultiSelectTrigger(page, "参照するナレッジベース")).toContainText("ナレッジベース-288");
  await expect(page.getByText("1 件選択中").first()).toBeVisible();

  // 表示中をすべて選択・クリアは読み込んだ候補に対して働く（検索語を消した結果を待つ）。
  await expect(listbox.getByRole("option")).toHaveCount(PAGE_SIZE);
  await page.getByRole("button", { name: "表示中をすべて選択" }).click();
  await expect(page.getByText(`${PAGE_SIZE + 1} 件選択中`).first()).toBeVisible();
  // ボタンの要約は 1 行（名前 2 件と「他 N 件」）で、高さは変わらない。
  await expect(searchableMultiSelectTrigger(page, "参照するナレッジベース")).toContainText(`他 ${PAGE_SIZE - 1} 件`);
  // chip は popover の下にあるので、閉じてから外す（popover は上の層で chip を押さない）。
  await page.keyboard.press("Escape");
  await chips.getByLabel("ナレッジベース-007 を選択から外す").click();
  await expect(page.getByText(`${PAGE_SIZE} 件選択中`).first()).toBeVisible();
  await openSearchableMultiSelect(page, "参照するナレッジベース");
  await page.getByRole("button", { name: "クリア", exact: true }).click();
  // プロファイルの参照 KB は必須なので、未選択の案内を出す。
  await expect(page.getByText("参照するナレッジベースを 1 件以上選んでください。")).toBeVisible();
});

test("選んでも開いたままで、「完了」で閉じてボタンにフォーカスが戻る。開いてもボタンと chip は動かない", async ({ page }) => {
  await mockKnowledgeBases(page, MANY);
  await page.goto("/search-answer-profiles?id=new");

  const trigger = searchableMultiSelectTrigger(page, "参照するナレッジベース");
  const listbox = page.getByRole("listbox", { name: "参照するナレッジベース" });
  await openSearchableMultiSelect(page, "参照するナレッジベース");
  await listbox.getByRole("option", { name: /ナレッジベース-002/ }).click();
  await listbox.getByRole("option", { name: /ナレッジベース-003/ }).click();
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option", { selected: true })).toHaveCount(2);

  await page.getByRole("button", { name: "完了" }).click();
  await expect(listbox).toHaveCount(0);
  await expect(trigger).toHaveAttribute("aria-expanded", "false");
  await expect(trigger).toBeFocused();
  await expect(trigger).toContainText("ナレッジベース-002、ナレッジベース-003");
  const chip = page.getByLabel("ナレッジベース-002 を選択から外す");
  await expect(chip).toBeVisible();
  await expect(page.getByLabel("ナレッジベース-003 を選択から外す")).toBeVisible();

  // ボタンでも開閉でき、開いてもボタンの大きさ・位置と下の chip の位置は変わらない（#5267）。
  const triggerBox = await trigger.boundingBox();
  const chipBox = await chip.boundingBox();
  await trigger.click();
  await expect(listbox).toBeVisible();
  expect(await trigger.boundingBox()).toEqual(triggerBox);
  expect(await chip.boundingBox()).toEqual(chipBox);
  await trigger.click();
  await expect(listbox).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("開閉のボタンは欄の高さ（md 36px、タッチ端末 44px）で、選んだ数が増えても 1 行の要約のまま高さが変わらない", { tag: "@responsive" }, async ({ page }) => {
  await mockKnowledgeBases(page, MANY);
  await page.goto("/search-answer-profiles?id=new");

  const trigger = searchableMultiSelectTrigger(page, "参照するナレッジベース");
  const expected = await expectedControlHeight(page, "md");
  const heightOf = async (locator: Locator) => Math.round((await locator.boundingBox())?.height ?? 0);
  expect(await heightOf(trigger)).toBe(expected);

  await openSearchableMultiSelect(page, "参照するナレッジベース");
  await page.getByRole("button", { name: "表示中をすべて選択" }).click();
  await page.getByRole("button", { name: "完了" }).click();
  await expect(trigger).toContainText(/他 \d+ 件/);
  expect(await heightOf(trigger)).toBe(expected);
  // 名前は 1 行で省略し、「他 N 件」は欄の中に見える。
  const summary = trigger.locator("[data-searchable-summary]");
  expect(await summary.evaluate((node) => node.getBoundingClientRect().right <= node.parentElement!.getBoundingClientRect().right)).toBe(true);
});

test("キーボードだけで選び、Esc で閉じ、Tab で popover の外へ出ると閉じる", async ({ page }) => {
  await mockKnowledgeBases(page, MANY);
  await page.goto("/search-answer-profiles?id=new");

  const trigger = searchableMultiSelectTrigger(page, "参照するナレッジベース");
  const listbox = page.getByRole("listbox", { name: "参照するナレッジベース" });

  // ボタンの Enter で開き、検索欄へフォーカスが移る。↓ と Enter で選ぶ。選んでも開いたまま。
  await trigger.focus();
  await page.keyboard.press("Enter");
  const combobox = page.locator("[data-searchable-select-popover]").getByRole("combobox", { name: "ナレッジベースを検索" });
  await expect(combobox).toBeFocused();
  await combobox.press("ArrowDown");
  await combobox.press("Enter");
  await expect(listbox.getByRole("option", { selected: true })).toHaveCount(1);
  await expect(listbox).toBeVisible();

  // 検索して Enter で選ぶ（強調は検索結果の先頭）。
  await combobox.pressSequentially("-199");
  await expect(listbox.getByRole("option")).toHaveCount(1);
  await combobox.press("Enter");
  await expect(page.getByLabel("ナレッジベース-199 を選択から外す")).toBeAttached();

  // Esc で閉じてボタンへ戻る。検索語は次に開いたとき残らない。
  await combobox.press("Escape");
  await expect(listbox).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.press("ArrowDown");
  await expect(combobox).toBeFocused();
  await expect(combobox).toHaveValue("");

  // Tab で popover の中の操作（完了）へ進んでも閉じない。そこで Esc を押すと閉じてボタンへ戻る。
  const done = page.getByRole("button", { name: "完了" });
  await tabUntilFocused(page, done, listbox);
  await page.keyboard.press("Escape");
  await expect(listbox).toHaveCount(0);
  await expect(trigger).toBeFocused();

  // 最後の操作（完了）から Tab で外へ出ると閉じ、ボタンの次（chip の「外す」）へ進む。
  await trigger.press("ArrowDown");
  await tabUntilFocused(page, done, listbox);
  await page.keyboard.press("Tab");
  await expect(listbox).toHaveCount(0);
  await expect(trigger).not.toBeFocused();
  await expect(page.locator("[data-searchable-multi-select]").getByRole("button").nth(1)).toBeFocused();

  // 検索欄からの Shift+Tab は閉じてボタンへ戻る。
  await trigger.press("ArrowDown");
  await expect(combobox).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(listbox).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("文書インデックスの絞り込み: ボタンから検索して選び、長い名前も切らずに出す（300 件）", async ({ page }) => {
  const requests = await mockKnowledgeBases(page, MANY);
  const documentRequests = await mockDocuments(page);
  await page.goto("/file-list");

  await openFileListFilters(page);
  const trigger = page.getByRole("button", { name: /^ナレッジベース/ });
  await expect(trigger).toHaveAccessibleName("ナレッジベース 利用できるすべてのナレッジベース");
  // キーボードで開く: Enter でボタンを押すと検索欄へフォーカスが移る。
  await trigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "ナレッジベース" });
  const search = dialog.getByRole("combobox", { name: "ナレッジベースを検索" });
  await expect(search).toBeFocused();
  await expect(dialog.getByRole("option")).toHaveCount(PAGE_SIZE + 1);
  await expect(dialog.getByText(`${PAGE_SIZE + 1} / ${MANY} 件`)).toBeVisible();

  await search.pressSequentially("長い名前");
  await expect(dialog.getByRole("option")).toHaveCount(1);
  await page.keyboard.press("Enter");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAccessibleName(`ナレッジベース ${LONG_NAME}`);
  await expect.poll(() => documentRequests.at(-1)).toContain("knowledge_base_id=kb-250");
  expect(requests.some((request) => request.q === "長い名前")).toBe(true);
  // 名前は切らずに折り返す（省略記号にしない）。
  const valueText = trigger.locator("span").first();
  expect(await valueText.evaluate((node) => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
  await expectNoPageOverflow(page);

  // 作業状態の復元: 候補の先頭ページに無い KB も ID で引いて名前を出す。
  await page.reload();
  await openFileListFilters(page);
  await expect(page.getByRole("button", { name: /^ナレッジベース/ })).toHaveAccessibleName(
    `ナレッジベース ${LONG_NAME}`
  );

  // スクロールで続きを読む。
  await page.getByRole("button", { name: /^ナレッジベース/ }).click();
  // 位置が決まる（検索欄へフォーカスが移る）のを待ってからスクロールする。
  await expect(dialog.getByRole("combobox", { name: "ナレッジベースを検索" })).toBeFocused();
  const list = dialog.getByRole("listbox", { name: "ナレッジベース" });
  expect(await list.evaluate((node) => node.scrollHeight > node.clientHeight + 1)).toBe(true);
  await list.evaluate((node) => node.scrollTo({ top: node.scrollHeight }));
  await expect(dialog.getByRole("option")).toHaveCount(PAGE_SIZE * 2 + 1);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("文書インデックスの状態は選択の欄 1 つで絞り込む（15 種の chip を折り返さない）", { tag: "@responsive" }, async ({ page }) => {
  await mockKnowledgeBases(page, 3);
  const documentRequests = await mockDocuments(page);
  await page.goto("/file-list");

  await openFileListFilters(page);
  const status = page.getByRole("combobox", { name: "状態" });
  // ボタンの中に「状態: すべて」（FilterSelect。#1933）。値は data-value で確かめる。
  await expect(status).toContainText("状態:すべて");
  await expect(status).toHaveAttribute("data-value", "ALL");
  await status.click();
  // 文書の状態 10 種と取込の順番待ちの 4 段に「すべて」（#2677）。
  await expect(page.getByRole("listbox", { name: "状態" }).getByRole("option")).toHaveCount(15);
  await page.getByRole("option", { name: "索引済み" }).click();
  await expect.poll(() => documentRequests.at(-1)).toContain("status=INDEXED");
  await expect(page.getByRole("button", { name: "索引済み", exact: true })).toHaveCount(0);
  await closeFileListFilters(page);
  await expectNoPageOverflow(page);
});

// #2091・#5267: 開いた popover はボタンの幅（20〜40rem）に合わせ、375px は画面の幅から左右の 1rem を引いた幅。
// 下部の「N 件選択中」と操作（表示中をすべて選択・クリア・完了）は 28rem 以上なら 1 行、狭いときは件数の文と操作の組の
// 2 段まで（操作の組は分けない）。画面からはみ出さない。
const PANEL_SCREENS = [
  { name: "アップロードの登録先", path: "/upload", label: "所属させるナレッジベース" },
  { name: "検索・回答プロファイルの作成", path: "/search-answer-profiles?id=new", label: "参照するナレッジベース" },
] as const;

for (const screen of PANEL_SCREENS) {
  test(`${screen.name}: 開いた一覧の下部の「N 件選択中」と操作が 1 行に並び、画面からはみ出さない`, { tag: "@responsive" }, async ({
    page,
  }) => {
    await mockKnowledgeBases(page, 120);
    await mockUploadPage(page);
    await mockSearchAnswerProfiles(page);
    await page.goto(screen.path);

    await openSearchableMultiSelect(page, screen.label);
    await expect(page.getByRole("listbox", { name: screen.label, exact: true })).toBeVisible();
    const footer = page.locator("[data-searchable-panel-footer]");
    await expect(footer).toContainText(/\d+ 件選択中/);
    await expectSearchablePanelFits(page);

    // 選んで件数の文が変わっても崩れない。
    const before = await footer.locator("span").first().textContent();
    await page.getByRole("option", { name: /ナレッジベース-120/ }).click();
    await expect(footer.locator("span").first()).not.toHaveText(before ?? "");
    await expectSearchablePanelFits(page);
    await expectNoPageOverflow(page);
  });
}

for (const theme of ["light", "dark"] as const) {
  test(`置き換えた画面で開いた選択が崩れない（${theme}）`, async ({ page }, testInfo) => {
    await useTheme(page, theme);
    await mockKnowledgeBases(page, MANY);
    await mockDocuments(page);
    await mockUploadPage(page);
    await mockSearchAnswerProfiles(page);

    const shoot = async (name: string) => {
      await expectNoPageOverflow(page);
      await page.screenshot({ path: testInfo.outputPath(`${name}-${theme}.png`) });
    };

    // 文書インデックスの絞り込み（単一選択）
    await page.goto("/file-list");
    await openFileListFilters(page);
    await page.getByRole("button", { name: /^ナレッジベース/ }).click();
    await expect(page.getByRole("dialog", { name: "ナレッジベース" })).toBeVisible();
    await shoot("file-list");
    await page.keyboard.press("Escape");

    // アップロードの登録先
    await page.goto("/upload");
    const uploadCombo = await openSearchableMultiSelect(page, "所属させるナレッジベース");
    await uploadCombo.fill("-120");
    await page.getByRole("option", { name: /ナレッジベース-120/ }).click();
    await expect(page.getByText("1 件のナレッジベースへ登録します。")).toBeVisible();
    await shoot("upload");

    // 検索・回答プロファイルの参照 KB
    await page.goto("/search-answer-profiles?id=new");
    const searchAnswerProfileCombo = await openSearchableMultiSelect(page, "参照するナレッジベース");
    await searchAnswerProfileCombo.fill("-042");
    await page.getByRole("option", { name: /ナレッジベース-042/ }).click();
    await expect(page.getByLabel("ナレッジベース-042 を選択から外す")).toBeAttached();
    await shoot("search-answer-profile");
  });
}

/** 一覧を開いたまま Tab を押し、対象にフォーカスが来るまで進める（途中で閉じないことも確かめる）。 */
async function tabUntilFocused(page: Page, target: Locator, listbox: Locator) {
  for (let step = 0; step < 16; step += 1) {
    const focused = await target
      .evaluate((node) => node === document.activeElement, undefined, { timeout: 1_000 })
      .catch(() => false);
    if (focused) return;
    await page.keyboard.press("Tab");
    await expect(listbox).toBeVisible();
  }
  await expect(target).toBeFocused();
}

/** アプリの外観の設定（localStorage）でテーマを切り替える。 */
async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { theme: value }, version: 0 })
    );
  }, theme);
}

const LONG_NAME =
  "経理・財務・内部統制・監査対応の規程と手順書をまとめた、全社共通の長い名前のナレッジベース";

function knowledgeBase(index: number) {
  const label = String(index).padStart(3, "0");
  return {
    id: `kb-${index}`,
    name: index === 1 ? "DEFAULT" : index === 250 ? LONG_NAME : `ナレッジベース-${label}`,
    description: index === 250 ? "経理・財務の全社規程" : null,
    status: "ACTIVE",
    default_search_mode: "hybrid",
    document_count: index - 1,
    indexed_document_count: index - 1,
    error_document_count: 0,
    searchable_chunk_count: (index - 1) * 2,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
    archived_at: null,
  };
}

async function mockKnowledgeBases(page: Page, count: number): Promise<KnowledgeBaseRequest[]> {
  const requests: KnowledgeBaseRequest[] = [];
  const all = Array.from({ length: count }, (_, index) => knowledgeBase(index + 1));
  await page.route("**/api/knowledge-bases**", async (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams.get("q");
    const ids = url.searchParams.getAll("ids");
    const limit = Number(url.searchParams.get("limit") ?? 50);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    requests.push({ q, limit, offset, ids });
    const matched = all.filter(
      (item) =>
        (ids.length === 0 || ids.includes(item.id)) &&
        (!q || item.name.includes(q.trim()) || (item.description ?? "").includes(q.trim()))
    );
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
  return requests;
}

async function mockDocuments(page: Page): Promise<string[]> {
  const requests: string[] = [];
  await page.route("**/api/documents**", async (route) => {
    const url = new URL(route.request().url());
    // 絞り込み中の件数の「全 M 件」を数える 1 件の取得（limit=1。#1891）は一覧の取得ではないので数えない。
    if (url.pathname === "/api/documents" && url.searchParams.get("limit") !== "1") requests.push(url.search);
    await route.fulfill({
      json: apiEnvelope({ items: [], total: 0, limit: 10, offset: 0, has_next: false }),
    });
  });
  return requests;
}

async function mockUploadPage(page: Page) {
  await page.route("**/api/settings/upload-storage", (route) =>
    route.fulfill({
      json: apiEnvelope({
        backend: "local",
        local_storage_dir: "/tmp/rag-uploads",
        object_storage_namespace: null,
        object_storage_bucket: null,
        object_storage_region: null,
        readiness: "ready",
        source: "runtime",
        max_upload_bytes: 209715200,
      }),
    })
  );
}

async function mockSearchAnswerProfiles(page: Page) {
  await page.route("**/api/search-answer-profiles**", (route) =>
    route.fulfill({ json: apiEnvelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) })
  );
}
