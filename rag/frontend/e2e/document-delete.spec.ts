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
  await page.getByRole("button", { name: "policy.txt を削除" }).click();

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
