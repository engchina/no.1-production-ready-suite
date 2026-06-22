import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

const documentDetail = {
  id: "doc-1",
  file_name: "policy.pdf",
  status: "INDEXED",
  category_name: null,
  content_type: "application/pdf",
  file_size_bytes: 1024,
  content_sha256: "abc",
  duplicate_of_document_id: null,
  uploaded_at: "2026-06-15T00:00:00Z",
  indexed_at: "2026-06-15T00:05:00Z",
  knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
  object_storage_path: "indexed/doc-1.pdf",
  extraction: {},
  error_message: null,
};

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

async function mockWorkspace(page: Page, drift: boolean, parserDrift = false) {
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({
      json: ok({ items: [{ id: "kb-1", name: "社内規程" }], total: 1, limit: 100, offset: 0, has_next: false }),
    })
  );
  await page.route("**/api/documents/doc-1/ingestion-config", (route) =>
    route.fulfill({
      json: ok({
        document_id: "doc-1",
        is_indexed: true,
        owning_knowledge_base: { id: "kb-1", name: "社内規程" },
        effective_chunking_strategy: "page_level",
        effective_parser_adapter_backend: parserDrift ? "mineru" : "docling",
        observed_chunking_strategy: drift ? "structure_aware" : "page_level",
        observed_parser_backend: parserDrift ? "enterprise_ai_pdf_layout" : "local",
        chunking_drift: drift,
        parser_drift: parserDrift,
        config_drift: drift || parserDrift,
      }),
    })
  );
  let enqueued = false;
  await page.route("**/api/documents/doc-1/ingestion-jobs**", (route) => {
    if (route.request().method() === "POST") {
      enqueued = true;
      route.fulfill({
        json: ok({
          id: "job-1",
          document_id: "doc-1",
          status: "QUEUED",
          parser_profile: "pdf",
          quality_warnings: [],
          skip_reason: null,
          error_message: null,
          attempt_count: 0,
          max_attempts: 3,
          queued_at: "2026-06-15T00:10:00Z",
          started_at: null,
          finished_at: null,
        }),
      });
      return;
    }
    route.fulfill({ json: ok([]) });
  });
  await page.route("**/api/documents/doc-1/chunks", (route) => route.fulfill({ json: ok([]) }));
  await page.route("**/api/documents/doc-1/ingestion-segments", (route) =>
    route.fulfill({ json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/knowledge-bases", (route) =>
    route.fulfill({ json: ok([{ id: "kb-1", name: "社内規程" }]) })
  );
  await page.route("**/api/documents/doc-1/extraction-export**", (route) =>
    route.fulfill({
      json: ok({
        document_id: "doc-1",
        file_name: "policy.pdf",
        format: "markdown",
        content_type: "text/markdown",
        content: "",
        payload: {},
        chunks: [],
        parser_backend: "local",
        parser_profile: "pdf",
        page_count: 0,
        element_count: 0,
        table_count: 0,
        asset_count: 0,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/content", (route) =>
    route.fulfill({ status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1", (route) => route.fulfill({ json: ok(documentDetail) }));
  return () => enqueued;
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("取込設定ドリフト時にバナーを表示し、再取込を実行できる", async ({ page }) => {
  const wasEnqueued = await mockWorkspace(page, true);

  await page.goto("/documents/doc-1");

  const banner = page.getByRole("status").filter({ hasText: "取込設定が更新されています" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("page_level");

  await banner.getByRole("button", { name: "現在の設定で再取込" }).click();
  await expect(page.getByText("再取込を開始しました。")).toBeVisible();
  expect(wasEnqueued()).toBe(true);
});

test("文書解析ドリフト時に MinerU への差分を表示する", async ({ page }) => {
  await mockWorkspace(page, false, true);

  await page.goto("/documents/doc-1");

  const banner = page.getByRole("status").filter({ hasText: "取込設定が更新されています" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText("文書解析");
  await expect(banner).toContainText("PDF レイアウト解析");
  await expect(banner).toContainText("MinerU");
});

test("ドリフトが無ければバナーを表示しない", async ({ page }) => {
  await mockWorkspace(page, false);

  await page.goto("/documents/doc-1");

  await expect(page.getByRole("heading", { name: "原本プレビュー" })).toBeVisible();
  await expect(page.getByText("取込設定が更新されています")).toHaveCount(0);
});
