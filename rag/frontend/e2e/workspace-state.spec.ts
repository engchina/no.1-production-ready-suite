import { expect, type Page, test } from "@playwright/test";

import { mockDatabaseReady } from "./_helpers";

/**
 * #132: 編集画面の離脱ガードと、検索・チャット・一覧の作業状態の保持
 * （platform docs/ux-contracts/workspace-state.md）。desktop / mobile(375px) の両 project で実行する。
 */

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
    chat_enabled: true,
  },
  error_messages: [],
  warning_messages: [],
};

const businessView = {
  id: "bv-1",
  name: "経理ビュー",
  description: "経費の相談",
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  archived_at: null,
};

function envelope(data: unknown) {
  return { json: { data, error_messages: [], warning_messages: [] } };
}

function pageEnvelope<T>(items: T[]) {
  return envelope({ items, total: items.length, limit: 50, offset: 0, has_next: false });
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

/** サイドナビの項目を開く（375px では icon-only のレールでも名前で辿れる）。 */
async function openFromSidebar(page: Page, name: string) {
  await page.getByRole("navigation").getByRole("link", { name, exact: true }).click();
}

/** beforeunload を合成して、離脱を妨げるか（preventDefault されたか）を返す。 */
function beforeUnloadPrevented(page: Page) {
  return page.evaluate(() => {
    const event = new Event("beforeunload", { cancelable: true });
    window.dispatchEvent(event);
    return event.defaultPrevented;
  });
}

async function mockHuggingFace(page: Page) {
  await page.route("**/api/settings/huggingface", (route) =>
    route.fulfill(envelope({ endpoint: "", token_configured: false, config_source: "runtime" }))
  );
}

test.describe("未保存変更の離脱ガード", () => {
  test("未変更ならサイドナビでそのまま移動でき、beforeunload も妨げない", async ({ page }) => {
    await mockHuggingFace(page);
    await page.goto("/settings/huggingface");
    await expect(page.locator("#hf-endpoint")).toBeVisible();

    expect(await beforeUnloadPrevented(page)).toBe(false);
    await openFromSidebar(page, "OCI 認証設定");
    await expect(page).toHaveURL(/\/settings\/oci$/);
    await expect(page.getByRole("alertdialog")).toHaveCount(0);
  });

  test("変更があるとサイドナビの移動を確認し、キャンセルで留まり、破棄して移動で進む", async ({
    page,
  }) => {
    await mockHuggingFace(page);
    await page.goto("/settings/huggingface");
    await page.locator("#hf-endpoint").fill("https://hf-mirror.com");

    expect(await beforeUnloadPrevented(page)).toBe(true);

    await openFromSidebar(page, "OCI 認証設定");
    const dialog = page.getByRole("alertdialog", { name: "変更を破棄しますか" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "キャンセル" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/settings\/huggingface$/);
    await expect(page.locator("#hf-endpoint")).toHaveValue("https://hf-mirror.com");

    await openFromSidebar(page, "OCI 認証設定");
    await page
      .getByRole("alertdialog", { name: "変更を破棄しますか" })
      .getByRole("button", { name: "破棄して移動" })
      .click();
    await expect(page).toHaveURL(/\/settings\/oci$/);
  });

  test("変更があるとブラウザの戻る/進むでも確認し、キャンセルで URL と入力が残る（#138）", async ({ page }) => {
    await mockHuggingFace(page);
    await page.goto("/settings/huggingface");
    await expect(page.locator("#hf-endpoint")).toBeVisible();
    // 未変更の移動と戻るは確認しない（SPA 内の履歴を 1 つ積む）。
    await openFromSidebar(page, "OCI 認証設定");
    await expect(page).toHaveURL(/\/settings\/oci$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/settings\/huggingface$/);
    await expect(page.getByRole("alertdialog")).toHaveCount(0);

    await page.locator("#hf-endpoint").fill("https://hf-mirror.com");
    await expect.poll(() => beforeUnloadPrevented(page)).toBe(true);
    await page.goForward();
    const dialog = page.getByRole("alertdialog", { name: "変更を破棄しますか" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "キャンセル" }).click();
    await expect(page).toHaveURL(/\/settings\/huggingface$/);
    await expect(page.locator("#hf-endpoint")).toHaveValue("https://hf-mirror.com");

    await page.goForward();
    await dialog.getByRole("button", { name: "破棄して移動" }).click();
    await expect(page).toHaveURL(/\/settings\/oci$/);
  });

  test("保存に成功すると基準が更新され、確認なしで移動できる", async ({ page }) => {
    let current = { endpoint: "", token_configured: false, config_source: "runtime" };
    await page.route("**/api/settings/huggingface", async (route) => {
      if (route.request().method() === "PATCH") {
        const payload = route.request().postDataJSON() as { endpoint?: string };
        current = { ...current, endpoint: payload.endpoint ?? "" };
      }
      await route.fulfill(envelope(current));
    });
    await page.goto("/settings/huggingface");
    await page.locator("#hf-endpoint").fill("https://hf-mirror.com");
    await page.getByRole("button", { name: "保存する" }).click();
    await expect(page.getByText("保存しました")).toBeVisible();

    expect(await beforeUnloadPrevented(page)).toBe(false);
    await openFromSidebar(page, "OCI 認証設定");
    await expect(page).toHaveURL(/\/settings\/oci$/);
  });

  test("業務ビューの下書きは確認のうえ移動しても、このタブに戻ると復元される", async ({ page }) => {
    await page.route("**/api/business-views**", (route) => route.fulfill(pageEnvelope([])));
    await page.route("**/api/knowledge-bases**", (route) => route.fulfill(pageEnvelope([])));
    await page.goto("/business-views?id=new");
    const name = page.locator("#business-view-name");
    await name.fill("購買アシスタント");

    await openFromSidebar(page, "RAG 検索");
    const dialog = page.getByRole("alertdialog", { name: "保存していない変更があります" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "移動する" }).click();
    await expect(page).toHaveURL(/\/search$/);
    // エディタのパンくずにも同じ名前のリンクがあるため、検索画面へ描画し終えてからサイドナビを押す。
    await expect(page.getByRole("heading", { name: "RAG 検索", level: 1 })).toBeVisible();

    // サイドナビは一覧へ戻る（編集対象は ?id= が唯一の情報源。#147）。新規の下書きは一覧から再開する。
    await openFromSidebar(page, "業務ビュー (Business View)");
    await expect(page).toHaveURL(/\/business-views$/);
    await page.getByRole("button", { name: "下書きを開く" }).click();
    await expect(page).toHaveURL(/\/business-views\?id=new$/);
    await expect(page.locator("#business-view-name")).toHaveValue("購買アシスタント");
    await expect(page.getByText("保存していない下書きを復元しました。")).toBeVisible();
  });
});

test.describe("作業状態の保持", () => {
  test("RAG 検索の質問・業務ビュー・詳細条件はページ往復と再読込で残り、検索は再送しない", async ({
    page,
  }) => {
    await page.route("**/api/business-views**", (route) => route.fulfill(pageEnvelope([businessView])));
    await page.route("**/api/chat/**", (route) => route.fulfill(pageEnvelope([])));
    let searchRequests = 0;
    await page.route("**/api/search/answers**", (route) => route.fulfill(envelope([])));
    await page.route("**/api/search/stream", async (route) => {
      searchRequests += 1;
      await route.fulfill({ status: 500, json: { data: null, error_messages: [], warning_messages: [] } });
    });

    await page.goto("/search");
    await page.getByRole("combobox", { name: /対象の業務ビュー/ }).click();
    await page
      .getByRole("listbox", { name: /対象の業務ビュー/ })
      .getByRole("option", { name: /経理ビュー/ })
      .click();
    await page.keyboard.press("Escape");
    await page.locator("#search-query").fill("交通費の上限");
    await page.getByText("詳細条件", { exact: true }).click();
    await page.getByRole("combobox", { name: "内容種別" }).click();
    await page.getByRole("option", { name: "表", exact: true }).click();

    const expectRestored = async () => {
      await expect(page.locator("#search-query")).toHaveValue("交通費の上限");
      await expect(page.getByText(/1 件の業務ビューを対象にしています/)).toBeVisible();
      await expect(page.getByRole("combobox", { name: "内容種別" })).toContainText("表");
    };

    await openFromSidebar(page, "チャット");
    await expect(page).toHaveURL(/\/chat$/);
    await openFromSidebar(page, "RAG 検索");
    await expectRestored();

    await page.reload();
    await expectRestored();
    expect(searchRequests).toBe(0);
  });

  test("チャットの業務ビュー・入力中の下書きはページ往復と再読込で残る", async ({ page }) => {
    await page.route("**/api/business-views**", (route) => route.fulfill(pageEnvelope([businessView])));
    await page.route("**/api/chat/models", (route) => route.fulfill(envelope([])));
    await page.route("**/api/chat/conversations**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/chat/conversations") {
        await route.fulfill(
          pageEnvelope([
            {
              id: "conv-1",
              business_view_id: "bv-1",
              title: "経費の相談",
              status: "ACTIVE",
              message_count: 0,
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ])
        );
        return;
      }
      await route.fulfill(
        envelope({
          id: "conv-1",
          business_view_id: "bv-1",
          title: "経費の相談",
          status: "ACTIVE",
          message_count: 0,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
          messages: [],
        })
      );
    });
    await page.route("**/api/search/answers**", (route) => route.fulfill(envelope([])));

    await page.goto("/chat");
    await page.getByRole("combobox", { name: "業務ビュー" }).click();
    await page.getByRole("option", { name: "経理ビュー" }).click();
    await page.getByRole("list", { name: "会話" }).getByRole("button").filter({ hasText: "経費の相談" }).click();
    const composer = page.locator("#chat-composer");
    await composer.fill("出張の日当は？");

    const expectRestored = async () => {
      await expect(page.getByRole("combobox", { name: "業務ビュー" })).toContainText("経理ビュー");
      await expect(page.locator("#chat-composer")).toHaveValue("出張の日当は？");
    };

    await openFromSidebar(page, "RAG 検索");
    await expect(page).toHaveURL(/\/search$/);
    await openFromSidebar(page, "チャット");
    await expectRestored();

    await page.reload();
    await expectRestored();
  });

  test("文書インデックスの絞り込み・検索はページ往復と再読込で残り、一括選択は解除される", async ({
    page,
  }) => {
    const documentRequests: string[] = [];
    await page.route("**/api/knowledge-bases**", (route) =>
      route.fulfill(pageEnvelope([{ id: "kb-1", name: "既定", document_count: 1 }]))
    );
    await page.route("**/api/documents**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/documents") documentRequests.push(url.search);
      await route.fulfill(
        pageEnvelope([
          {
            id: "doc-1",
            file_name: "policy.txt",
            status: "ERROR",
            category_name: null,
            content_type: "text/plain",
            file_size_bytes: 10,
            content_sha256: null,
            duplicate_of_document_id: null,
            uploaded_at: "2026-01-01T00:00:00Z",
            indexed_at: null,
            knowledge_bases: [],
            source_profile: null,
          },
        ])
      );
    });
    await page.route("**/api/business-views**", (route) => route.fulfill(pageEnvelope([businessView])));

    await page.goto("/file-list");
    await page.getByRole("button", { name: "エラー", exact: true }).click();
    const search = page.getByRole("textbox", { name: "ファイル名で検索" });
    await search.fill("policy");
    await search.press("Enter");
    await page.getByRole("checkbox", { name: "この行を選択" }).check();

    const expectRestored = async () => {
      await expect(page.getByRole("button", { name: "エラー", exact: true })).toHaveAttribute(
        "aria-pressed",
        "true"
      );
      await expect(page.getByRole("textbox", { name: "ファイル名で検索" })).toHaveValue("policy");
      await expect(page.getByRole("checkbox", { name: "この行を選択" })).not.toBeChecked();
      expect(documentRequests.at(-1)).toContain("status=ERROR");
      expect(documentRequests.at(-1)).toContain("q=policy");
    };

    await openFromSidebar(page, "RAG 検索");
    await expect(page).toHaveURL(/\/search$/);
    await openFromSidebar(page, "文書インデックス");
    await expectRestored();

    await page.reload();
    await expectRestored();
  });

  test("フィードバックの URL の絞り込みは、サイドナビで戻っても復元される", async ({ page }) => {
    await page.route("**/api/business-views**", (route) => route.fulfill(pageEnvelope([businessView])));
    await page.route("**/api/feedback**", (route) =>
      route.fulfill({ status: 500, json: { data: null, error_messages: [], warning_messages: [] } })
    );
    await page.route("**/api/search/answers**", (route) => route.fulfill(envelope([])));

    await page.goto("/feedback?period=7&sort=oldest&size=50&page=1&rating=not_helpful");
    await openFromSidebar(page, "RAG 検索");
    await expect(page).toHaveURL(/\/search$/);
    await openFromSidebar(page, "フィードバック");
    await expect(page).toHaveURL(/\/feedback\?period=7&sort=oldest&size=50&page=1&rating=not_helpful$/);
  });
});
