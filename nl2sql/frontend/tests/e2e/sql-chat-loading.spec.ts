import {
  expectChatTargetNextToNewConversation,
  expectChatToolbarHeightUnchanged,
  measureChatToolbarHeight,
} from "../../../../platform/packages/ui/e2e/chat-toolbar-layout";
import { findLoadingStandardViolations } from "../../../../platform/packages/ui/e2e/loading-checks";
import { expect, test, type Page } from "./_helpers/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { mockChatRequirement } from "./_helpers/chat-requirement";

/**
 * チャットの前提（業務プロファイルの一覧・開いている会話の内容）の読み込み中・失敗の表示（#1153）。
 * 3 製品で同じ規則（UX 契約 messaging.md §11.7）: 前提が揃うまで会話の欄に空の状態を出さず、
 * 会話の形の Skeleton を出し、送信を止める。入力欄・生成方法・新しい会話・履歴の開閉を無効にするのは
 * 業務プロファイルの一覧の読み込み中だけで、会話の内容の読み込み中は書ける（#1188。sql-chat.spec.ts）。
 * desktop と mobile-375 の 2 project で実行する。
 */

const profile = {
  id: "sales",
  name: "売上分析",
  description: "売上の集計",
  archived: false,
  allowed_tables: ["APP.SALES"],
  allowed_views: [],
  allowed_table_count: 1,
  allowed_view_count: 0,
  version: 1,
};
const profilePage = {
  json: { data: { items: [profile], total: 1, next_cursor: null } },
};

async function setup(page: Page) {
  await mockDatabaseGateReady(page);
  // 送信の前の要件の検査（#1442）は「確認が要らない」で、聞かずに SQL を生成する。
  await mockChatRequirement(page);
  await page.route("**/api/nl2sql/profiles/*/usage-context", (route) =>
    route.fulfill({ json: { data: profile } }),
  );
  await page.route("**/api/nl2sql/chats**", (route) =>
    route.fulfill({ json: { data: { items: [], next_cursor: null, total: 0, limit: 25 } } }),
  );
}

/** 業務プロファイルの一覧の応答を、release() まで止める。 */
async function holdProfiles(page: Page) {
  let release!: () => void;
  const released = new Promise<void>((resolve) => (release = resolve));
  await page.route("**/api/nl2sql/profiles/search**", async (route) => {
    await released;
    await route.fulfill(profilePage);
  });
  return () => release();
}

async function applyColorScheme(page: Page, colorScheme: "light" | "dark") {
  await page.emulateMedia({ colorScheme });
  await page.evaluate(
    (dark) => document.documentElement.classList.toggle("dark", dark),
    colorScheme === "dark",
  );
}

for (const colorScheme of ["light", "dark"] as const) {
  test(`業務プロファイルの読み込み中は会話の欄に空の状態を出さず、Skeleton と無効の入力を出す (${colorScheme})`, { tag: "@responsive" }, async ({
    page,
  }, testInfo) => {
    await setup(page);
    const release = await holdProfiles(page);
    await page.goto("/chat");
    await applyColorScheme(page, colorScheme);

    // ツールバーの対象の欄は欄の形の Skeleton だけ。文言と経過時間は会話の欄の先頭に 1 か所だけ出す（#4649）。
    const toolbar = page.locator("[data-chat-toolbar]");
    await expect(toolbar.getByTestId("sql-chat-profiles-skeleton")).toBeVisible();
    const reservedHeight = await measureChatToolbarHeight(page);
    await expect(toolbar.getByRole("timer")).toHaveCount(0);
    const panel = page.getByTestId("sql-chat-panel");
    await expect(panel).toBeVisible();
    const profilesLoading = panel.getByTestId("sql-chat-profiles-loading");
    await expect(profilesLoading).toContainText(
      "業務プロファイルを読み込んでいます",
    );
    // 会話の欄は最初から描き、空の状態の代わりに会話の形の Skeleton を出す。
    await expect(profilesLoading.getByTestId("sql-chat-conversation-skeleton")).toBeVisible();
    await expect(
      page.getByText("質問を入力して会話を始めます", { exact: true }),
    ).toHaveCount(0);
    await expect(page.getByRole("timer")).toHaveCount(1);
    expect(await findLoadingStandardViolations(page, { expectCount: 1, skeleton: true })).toEqual([]);
    // 会話の欄を末尾へ送らず、先頭の文言と経過時間が見える（375px で隠れない。#4649）。
    await expect(profilesLoading.locator("[data-processing-label]")).toBeInViewport();
    await expect(profilesLoading.getByRole("timer")).toBeInViewport();
    // 入力欄・生成方法・送信・新しい会話・履歴の開閉は無効。
    await expect(
      page.getByRole("textbox", { name: "質問", exact: true }),
    ).toBeDisabled();
    await expect(page.locator("#sql-chat-engine")).toBeDisabled();
    await expect(page.getByTestId("sql-chat-send")).toBeDisabled();
    await expect(
      page.getByRole("button", { name: "新しい会話", exact: true }),
    ).toBeDisabled();
    await expect(page.getByTestId("sql-chat-history-toggle")).toBeDisabled();
    await page.screenshot({
      path: testInfo.outputPath(`chat-loading-${colorScheme}.png`),
    });

    // 読み込み後: 空の状態と、使える入力欄。
    release();
    await expect(page.getByTestId("sql-chat-profiles-loading")).toHaveCount(0);
    await expect(page.getByTestId("sql-chat-profiles-skeleton")).toHaveCount(0);
    await expect(page.locator("[data-chat-toolbar] #sql-chat-profile")).toBeVisible();
    await expectChatTargetNextToNewConversation(page, expect);
    await expectChatToolbarHeightUnchanged(page, expect, reservedHeight);
    await page.screenshot({ path: testInfo.outputPath(`chat-target-ready-${colorScheme}.png`) });
    await expect(page.getByTestId("sql-chat-conversation-skeleton")).toHaveCount(0);
    await expect(
      page.getByText("質問を入力して会話を始めます", { exact: true }),
    ).toBeVisible();
    const composer = page.getByRole("textbox", { name: "質問", exact: true });
    await expect(composer).toBeEnabled();
    await expect(page.locator("#sql-chat-engine")).toBeEnabled();
    await expect(
      page.getByRole("button", { name: "新しい会話", exact: true }),
    ).toBeEnabled();
    await expect(page.getByTestId("sql-chat-history-toggle")).toBeEnabled();
    await composer.fill("カテゴリ別売上");
    await expect(page.getByTestId("sql-chat-send")).toBeEnabled();
  });
}

test("読み込み中も入力欄に書いた文字は残す（作業状態）", async ({ page }) => {
  await setup(page);
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    route.fulfill(profilePage),
  );
  await page.goto("/chat");
  const composer = page.getByRole("textbox", { name: "質問", exact: true });
  await composer.fill("書きかけのクエリ");

  await page.unroute("**/api/nl2sql/profiles/search**");
  const release = await holdProfiles(page);
  await page.reload();
  await expect(page.getByTestId("sql-chat-conversation-skeleton")).toBeVisible();
  await expect(composer).toBeDisabled();
  await expect(composer).toHaveValue("書きかけのクエリ");
  release();
  await expect(composer).toBeEnabled();
  await expect(composer).toHaveValue("書きかけのクエリ");
});

test("業務プロファイルの一覧を読めなかったときは、空の状態ではなく失敗と再試行を出す", async ({
  page,
}) => {
  await setup(page);
  let fail = true;
  await page.route("**/api/nl2sql/profiles/search**", (route) =>
    fail
      ? route.fulfill({
          status: 500,
          json: { error: "データベースに接続できません。" },
        })
      : route.fulfill(profilePage),
  );
  await page.goto("/chat");
  const error = page.getByTestId("sql-chat-profiles-error");
  // 取得は TanStack Query の既定の再試行（3 回・1 + 2 + 4 秒）の後に失敗になる。
  await expect(error).toContainText("データベースに接続できません。", {
    timeout: 20_000,
  });
  // 会話の欄（空の状態・入力欄）は出さない。
  await expect(page.getByTestId("sql-chat-panel")).toHaveCount(0);
  await expect(
    page.getByText("質問を入力して会話を始めます", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("利用できる業務プロファイルがありません", { exact: true }),
  ).toHaveCount(0);

  // 3 製品で同じ領域の失敗の表示（帯・「詳細」は閉じる・「再試行」。messaging.md §11.7。#5929）。
  await expect(error).toHaveAttribute("role", "alert");
  await expect(error).toHaveAttribute("data-failure-layout", "region");
  await expect(error.locator("details")).not.toHaveAttribute("open", "");

  fail = false;
  await error.getByRole("button", { name: "再試行" }).click();
  await expect(page.locator("#sql-chat-profile")).toContainText("売上分析");
  await expect(
    page.getByText("質問を入力して会話を始めます", { exact: true }),
  ).toBeVisible();
});

// #5937: 会話の内容・会話の履歴の一覧の読み取りの失敗は、3 製品で同じ領域の失敗の表示（帯・「再試行」・閉じた「詳細」）。
const savedConversation = {
  id: "chat-1",
  title: "カテゴリ別売上",
  profile_id: "sales",
  created_at: "2026-10-02T22:00:00Z",
};

/** 会話の一覧（1 件）と会話の内容の応答。`failing()` が指す方だけ 500 にする。 */
async function mockSavedConversation(page: Page, failing: () => "list" | "detail" | null) {
  await page.route("**/api/nl2sql/profiles/search**", (route) => route.fulfill(profilePage));
  await page.route("**/api/nl2sql/chats**", (route) => {
    const list = new URL(route.request().url()).pathname === "/api/nl2sql/chats";
    if (failing() === (list ? "list" : "detail")) {
      return route.fulfill({
        status: 500,
        json: { error: list ? "会話の履歴を取得できません。" : "会話を取得できません。" },
      });
    }
    return route.fulfill({
      json: {
        data: list
          ? { items: [savedConversation], next_cursor: null, total: 1, limit: 25 }
          : { conversation: savedConversation, turns: [] },
      },
    });
  });
}

test("開いている会話の内容を読めなかったときは、空の状態ではなく失敗と再試行を出す", async ({ page }) => {
  await setup(page);
  let failing: "detail" | null = "detail";
  await mockSavedConversation(page, () => failing);
  await page.goto("/chat");
  await page.getByTestId("sql-chat-history-toggle").click();
  await page
    .getByTestId("sql-chat-history")
    .getByRole("button", { name: /^カテゴリ別売上/ })
    .click();
  // 取得は TanStack Query の既定の再試行の後に失敗になる。
  const error = page.getByTestId("sql-chat-conversation-error");
  await expect(error).toContainText("会話を取得できません。", { timeout: 20_000 });
  await expect(error).toHaveAttribute("role", "alert");
  await expect(error).toHaveAttribute("data-failure-layout", "region");
  await expect(error.locator("details")).not.toHaveAttribute("open", "");
  await expect(
    page.getByText("質問を入力して会話を始めます", { exact: true }),
  ).toHaveCount(0);

  failing = null;
  await error.getByRole("button", { name: "再試行" }).click();
  await expect(error).toHaveCount(0);
});

test("会話の履歴の一覧を読めなかったときは、0 件ではなく失敗と再試行を出す", async ({ page }) => {
  await setup(page);
  let failing: "list" | null = "list";
  await mockSavedConversation(page, () => failing);
  await page.goto("/chat");
  await page.getByTestId("sql-chat-history-toggle").click();
  const history = page.getByTestId("sql-chat-history");
  const error = history.getByRole("alert");
  await expect(error).toContainText("会話の履歴を取得できません。", { timeout: 20_000 });
  await expect(error).toHaveAttribute("data-failure-layout", "region");
  await expect(error.locator("details")).not.toHaveAttribute("open", "");
  await expect(history.getByText("まだ会話がありません")).toHaveCount(0);

  failing = null;
  await error.getByRole("button", { name: "再試行" }).click();
  await expect(history.getByRole("button", { name: /^カテゴリ別売上/ })).toBeVisible();
  await expect(error).toHaveCount(0);
});
