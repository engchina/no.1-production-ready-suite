import { expect, type Page, test } from "@playwright/test";
import { mockDatabaseReady } from "./_helpers";

const authStatus = {
  data: { mode: "local", auth_required: false, authenticated: true, user: null, expires_at: null },
  error_messages: [],
  warning_messages: [],
};

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

// Office 原本 + 変換済 PDF(preprocess_artifact)あり。
const documentDetail = {
  id: "doc-1",
  file_name: "report.docx",
  status: "INDEXED",
  category_name: null,
  content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  file_size_bytes: 2048,
  content_sha256: "abc",
  duplicate_of_document_id: null,
  uploaded_at: "2026-06-15T00:00:00Z",
  indexed_at: "2026-06-15T00:05:00Z",
  knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
  object_storage_path: "indexed/doc-1.docx",
  extraction: {},
  error_message: null,
  preprocess_artifact: {
    object_storage_path: "prepared/doc-1.pdf",
    file_name: "report.pdf",
    profile: "office_to_pdf",
    converted: true,
  },
};

async function mockWorkspace(page: Page): Promise<void> {
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
        effective_preprocess_profile: "office_to_pdf",
        effective_chunking_strategy: "page_level",
        effective_parser_adapter_backend: "docling",
        observed_chunking_strategy: "page_level",
        observed_parser_backend: "docling",
        chunking_drift: false,
        parser_drift: false,
        config_drift: false,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/ingestion-jobs**", (route) => route.fulfill({ json: ok([]) }));
  await page.route("**/api/documents/doc-1/chunks", (route) => route.fulfill({ json: ok([]) }));
  await page.route("**/api/documents/doc-1/chunk-sets", (route) => route.fulfill({ json: ok([]) }));
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
        file_name: "report.docx",
        format: "markdown",
        content_type: "text/markdown",
        content: "",
        payload: {},
        chunks: [],
        parser_backend: "docling",
        parser_profile: "docx",
        page_count: 0,
        element_count: 0,
        table_count: 0,
        asset_count: 0,
      }),
    })
  );
  await page.route("**/api/documents/doc-1/content**", (route) =>
    route.fulfill({ status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1", (route) => route.fulfill({ json: ok(documentDetail) }));
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: authStatus }));
});

test("Office 原本でも変換済 PDF をその場で表示する(unsupported にしない)", async ({ page }) => {
  await mockWorkspace(page);

  await page.goto("/documents/doc-1");

  // 既定(原本ビュー)でも prepared PDF の iframe を描画する。
  await expect(page.locator('iframe[src*="variant=prepared"]')).toBeVisible();
  // 「Office ファイルは原本を直接表示せず…」の unsupported 文言は出さない。
  await expect(page.getByText("Office ファイルは原本を直接表示せず", { exact: false })).toHaveCount(0);
});
