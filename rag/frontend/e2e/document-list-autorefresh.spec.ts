import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

type FileStatus = "UPLOADED" | "INGESTING" | "INDEXING" | "INDEXED" | "ERROR";

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

// 取込中の文書がある間は自動でポーリングし、手動リロードなしで状態バッジが更新される。
test("取込中の文書がポーリングで索引済みに自動遷移する", async ({ page }) => {
  let listCalls = 0;
  // 初回 GET は取込中、3 回目以降は索引済みを返す(backend 側の進行を模擬)。
  const statusFor = (): FileStatus => (listCalls >= 3 ? "INDEXED" : "INGESTING");

  await page.route("**/api/knowledge-bases**", async (route) => {
    await route.fulfill({
      json: {
        data: {
          items: [{ id: "kb-default", name: "既定", document_count: 1 }],
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
    const url = new URL(route.request().url());
    if (route.request().method() === "GET" && url.pathname === "/api/documents") {
      listCalls += 1;
      await route.fulfill({
        json: {
          data: {
            items: [documentSummary("doc-1", "report.pdf", statusFor())],
            total: 1,
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
    await route.fallback();
  });

  await page.goto("/file-list");

  const row = page.locator("tbody tr").filter({ hasText: "report.pdf" });
  await expect(row).toContainText("取込中");

  // クリックやリロードなしで、ポーリングにより索引済みへ更新される。
  await expect(row).toContainText("索引済み", { timeout: 15000 });
});

function documentSummary(id: string, fileName: string, status: FileStatus): DocumentSummary {
  return {
    id,
    file_name: fileName,
    status,
    category_name: "社内規程",
    content_type: "application/pdf",
    file_size_bytes: 2048,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-16T09:00:00Z",
    indexed_at: status === "INDEXED" ? "2026-06-16T09:05:00Z" : null,
    knowledge_bases: [{ id: "kb-default", name: "既定" }],
    source_profile: null,
  };
}
