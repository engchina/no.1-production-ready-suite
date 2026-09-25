import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

type FileStatus = "UPLOADED" | "INGESTING" | "INDEXED" | "ERROR";

interface DocumentSummary {
  id: string;
  file_name: string;
  status: FileStatus;
  category_name: string | null;
  content_type: string | null;
  file_size_bytes: number | null;
  content_sha256: string | null;
  duplicate_of_document_id: string | null;
  uploaded_at: string;
  indexed_at: string | null;
  knowledge_bases: { id: string; name: string }[];
  source_profile: null;
}

const authStatus = {
  data: {
    mode: "local",
    auth_required: false,
    authenticated: true,
    user: null,
    expires_at: null,
  },
  error_messages: [],
  warning_messages: [],
};

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

test("文書インデックスからアップロード済みドキュメントを削除できる", async ({ page }) => {
  const documents: DocumentSummary[] = [
    documentSummary("doc-1", "policy.txt", "UPLOADED"),
    documentSummary("doc-2", "guide.txt", "INDEXED"),
  ];
  let deletedId: string | null = null;
  await mockDocumentIndexApi(page, documents, (id) => {
    deletedId = id;
    const index = documents.findIndex((document) => document.id === id);
    if (index >= 0) documents.splice(index, 1);
  });

  await page.goto("/file-list");

  await expect(page.getByRole("heading", { name: "文書インデックス" })).toBeVisible();
  await expect(page.getByRole("link", { name: "policy.txt" })).toBeVisible();
  // 行の操作は RowActionMenu 1 個にまとまっている（#131 / buttons.md §5.1）。
  await page.getByRole("button", { name: "policy.txt の操作" }).click();
  await page.getByRole("menuitem", { name: "policy.txt を削除" }).click();

  const dialog = page.getByRole("alertdialog", { name: "このドキュメントを削除しますか？" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("policy.txt");
  await dialog.getByRole("button", { name: "削除" }).click();

  await expect(page.getByText("「policy.txt」を削除しました。").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "policy.txt" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "guide.txt" })).toBeVisible();
  expect(deletedId).toBe("doc-1");
  await expectNoHorizontalOverflow(page);
});

// #131: 行の操作は RowActionMenu 1 個にまとめる（buttons.md §5.1）。desktop / mobile の両 project で動く。
test("行の操作メニューは Enter で開き、Esc で閉じてトリガーへフォーカスを戻す", async ({ page }) => {
  const documents: DocumentSummary[] = [
    documentSummary("doc-1", "policy.txt", "UPLOADED"),
    documentSummary("doc-2", "guide.txt", "INDEXED"),
  ];
  await mockDocumentIndexApi(page, documents, () => {});

  await page.goto("/file-list");

  const row = page.locator("tbody tr").filter({ hasText: "policy.txt" });
  // 行内のボタンはメニューのトリガー 1 個だけ（文字ボタンを並べない）。
  await expect(row.getByRole("button")).toHaveCount(1);
  const trigger = row.getByRole("button", { name: "policy.txt の操作" });
  await expect(trigger).toHaveAttribute("aria-haspopup", "menu");
  await expect(trigger).toHaveAttribute("aria-expanded", "false");

  await trigger.focus();
  await page.keyboard.press("Enter");
  const menu = page.getByRole("menu");
  await expect(menu).toBeVisible();
  await expect(trigger).toHaveAttribute("aria-expanded", "true");
  // 取込と削除が 1 つのメニューに入り、先頭の項目へフォーカスが移る。
  await expect(menu.getByRole("menuitem")).toHaveText(["ファイル準備を実行", "削除"]);
  await expect(menu.getByRole("menuitem", { name: "ファイル準備を実行" })).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(menu.getByRole("menuitem", { name: "policy.txt を削除" })).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute("aria-expanded", "false");

  // 索引済みの行は取込の操作を出さず、削除だけを持つ。
  await page
    .locator("tbody tr")
    .filter({ hasText: "guide.txt" })
    .getByRole("button", { name: "guide.txt の操作" })
    .click();
  await expect(page.getByRole("menu").getByRole("menuitem")).toHaveText(["削除"]);
  await page.keyboard.press("Escape");
  await expectNoHorizontalOverflow(page);
});

test("行のメニューからの削除は確認ダイアログを通り、キャンセルでは削除しない", async ({ page }) => {
  const documents: DocumentSummary[] = [documentSummary("doc-1", "policy.txt", "UPLOADED")];
  let deleteCalls = 0;
  await mockDocumentIndexApi(page, documents, (id) => {
    deleteCalls += 1;
    const index = documents.findIndex((document) => document.id === id);
    if (index >= 0) documents.splice(index, 1);
  });

  await page.goto("/file-list");

  const trigger = page.getByRole("button", { name: "policy.txt の操作" });
  await trigger.click();
  await page.getByRole("menuitem", { name: "policy.txt を削除" }).click();
  const dialog = page.getByRole("alertdialog", { name: "このドキュメントを削除しますか？" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("link", { name: "policy.txt" })).toBeVisible();
  expect(deleteCalls).toBe(0);

  // 一括選択の間は行の操作を止め、一括操作のバーへ集める。
  await page.locator("tbody tr").filter({ hasText: "policy.txt" }).getByRole("checkbox").check();
  await expect(trigger).toBeDisabled();
  await page.getByRole("button", { name: "選択解除" }).click();
  await expect(trigger).toBeEnabled();

  await trigger.click();
  await page.getByRole("menuitem", { name: "policy.txt を削除" }).click();
  await page
    .getByRole("alertdialog", { name: "このドキュメントを削除しますか？" })
    .getByRole("button", { name: "削除" })
    .click();
  await expect(page.getByText("「policy.txt」を削除しました。").first()).toBeVisible();
  expect(deleteCalls).toBe(1);
});

test("選択したドキュメントを一括削除できる", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const documents: DocumentSummary[] = [
    documentSummary("doc-1", "policy.txt", "UPLOADED"),
    documentSummary("doc-2", "guide.txt", "INDEXED"),
    documentSummary("doc-3", "notes.txt", "ERROR"),
  ];
  const deletedIds: string[] = [];
  await mockDocumentIndexApi(page, documents, (id) => {
    deletedIds.push(id);
    const index = documents.findIndex((document) => document.id === id);
    if (index >= 0) documents.splice(index, 1);
  });

  await page.goto("/file-list");

  await page.locator("tbody tr").filter({ hasText: "policy.txt" }).getByRole("checkbox").check();
  await page.locator("tbody tr").filter({ hasText: "guide.txt" }).getByRole("checkbox").check();
  await expect(page.getByText("2 件選択中")).toBeVisible();
  await page.getByRole("button", { name: "一括削除 (2)" }).click();

  const dialog = page.getByRole("alertdialog", { name: "選択した 2 件を削除しますか？" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("投入ジョブと segment");
  await dialog.getByRole("button", { name: "一括削除" }).click();

  await expect(page.getByText("2 件のドキュメントを削除しました。").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "policy.txt" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "guide.txt" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "notes.txt" })).toBeVisible();
  expect(deletedIds).toEqual(["doc-1", "doc-2"]);
  await expectNoHorizontalOverflow(page);
});

async function mockDocumentIndexApi(
  page: Page,
  documents: DocumentSummary[],
  onDelete: (id: string) => void
) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: [{ id: "kb-default", name: "既定", document_count: documents.length }],
          total: 1,
          limit: 100,
          offset: 0,
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.route("**/api/documents**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/").filter(Boolean);

    if (request.method() === "GET" && url.pathname === "/api/documents") {
      await route.fulfill({
        json: {
          data: {
            items: documents,
            total: documents.length,
            limit: 20,
            offset: 0,
            has_next: false,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "DELETE" && parts[0] === "api" && parts[1] === "documents") {
      const id = parts[2];
      const document = documents.find((item) => item.id === id);
      if (!document) {
        await route.fulfill({
          status: 404,
          json: {
            data: null,
            error_messages: ["ドキュメントが見つかりません。"],
            warning_messages: [],
          },
        });
        return;
      }
      onDelete(id);
      await route.fulfill({
        json: {
          data: {
            id,
            file_name: document.file_name,
            object_storage_path: `local://uploaded/${document.file_name}`,
            object_deleted: true,
            artifact_deleted_count: 0,
            artifact_delete_failed_count: 0,
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    await route.fallback();
  });
}

function documentSummary(id: string, fileName: string, status: FileStatus): DocumentSummary {
  return {
    id,
    file_name: fileName,
    status,
    category_name: "社内規程",
    content_type: "text/plain",
    file_size_bytes: 1024,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-16T09:00:00Z",
    indexed_at: status === "INDEXED" ? "2026-06-16T09:05:00Z" : null,
    knowledge_bases: [{ id: "kb-default", name: "既定" }],
    source_profile: null,
  };
}

async function expectNoHorizontalOverflow(page: Page) {
  const hasOverflow = await page.evaluate(() => {
    const element = document.scrollingElement ?? document.documentElement;
    return element.scrollWidth > element.clientWidth + 1;
  });
  expect(hasOverflow).toBe(false);
}
