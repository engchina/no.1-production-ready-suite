import structuredSource from "./fixtures/structured-source-reader.json";
import { fulfillDocumentInspection } from "./document-inspection-mocks";
import { expect, type Page, test } from "./fixtures/test";
import { mockDatabaseReady, mockLocalAuth, expectNoPageOverflow } from "./_helpers";

function ok(json: unknown) {
  return { data: json, error_messages: [], warning_messages: [] };
}

// 表示できない形式の案内は、Office の原本・処理後の JSONL を含めて共通の 1 文（#2679）。
const UNSUPPORTED_NOTICE = "このファイル形式はプレビューに対応していません。";
// 以前の Office 独自の案内（再処理の手順）。もう出さない。
const OFFICE_REPROCESS_NOTICE = /Office 原本はブラウザー/;

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
    content_type: "application/pdf",
    file_name: "report.pdf",
    profile: "office_to_pdf",
    converted: true,
  },
};

async function mockWorkspace(
  page: Page,
  preprocessArtifact: typeof documentDetail.preprocess_artifact | null =
    documentDetail.preprocess_artifact
): Promise<void> {
  await page.route("**/api/knowledge-bases**", (route) =>
    fulfillDocumentInspection(route, {
      json: ok({ items: [{ id: "kb-1", name: "社内規程" }], total: 1, limit: 100, offset: 0, has_next: false }),
    })
  );
  await page.route("**/api/documents/doc-1/recipes", (route) =>
    fulfillDocumentInspection(route, {
      json: ok([
        {
          recipe_id: "recipe-1",
          document_id: "doc-1",
          slot_no: 1,
          status: "INDEXED",
          failed_phase: null,
          processing_config: {},
          effective_processing_config: {},
          preprocess_artifact: preprocessArtifact,
          active_extraction_recipe_id: "er-recipe-1-r1",
          active_chunk_set_id: "chunk-set-recipe-1",
          chunk_count: 2,
          vector_count: 2,
          config_revision: 1,
          materialized_revision: 1,
          searchable: true,
          needs_reprocessing: false,
          error_message: null,
          steps: [
            { phase: "PREPROCESS", status: "SUCCEEDED", started_at: null, finished_at: null, error_message: null },
            { phase: "EXTRACT", status: "SUCCEEDED", started_at: null, finished_at: null, error_message: null },
            { phase: "CHUNK", status: "SUCCEEDED", started_at: null, finished_at: null, error_message: null },
            { phase: "INDEX", status: "SUCCEEDED", started_at: null, finished_at: null, error_message: null },
          ],
          created_at: "2026-06-15T00:00:00Z",
          updated_at: "2026-06-15T00:00:20Z",
          started_at: null,
          finished_at: "2026-06-15T00:00:20Z",
        },
      ]),
    })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/{chunks,chunk-window}**", (route) =>
    fulfillDocumentInspection(route, { json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/ingestion-jobs**", (route) => fulfillDocumentInspection(route, { json: ok([]) }));
  await page.route("**/api/documents/doc-1/chunk-sets", (route) => fulfillDocumentInspection(route, { json: ok([]) }));
  await page.route("**/api/documents/doc-1/ingestion-segments", (route) =>
    fulfillDocumentInspection(route, { json: ok([]) })
  );
  await page.route("**/api/documents/doc-1/knowledge-bases", (route) =>
    fulfillDocumentInspection(route, { json: ok([{ id: "kb-1", name: "社内規程" }]) })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/{extraction-export,extraction-view}**", (route) =>
    fulfillDocumentInspection(route, {
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
    fulfillDocumentInspection(route, { status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1/recipes/recipe-1/content**", (route) =>
    fulfillDocumentInspection(route, { status: 204, body: "" })
  );
  await page.route("**/api/documents/doc-1", (route) =>
    fulfillDocumentInspection(route, { json: ok(documentDetail) })
  );
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

test("Office 原本でも変換済 PDF をその場で表示する(unsupported にしない)", async ({ page }) => {
  await mockWorkspace(page);

  await page.goto("/documents/doc-1");

  // 既定(原本ビュー)でも prepared PDF の iframe を描画する。
  await expect(page.locator('iframe[src*="variant=prepared"]')).toBeVisible();
  await expect(page.getByText(UNSUPPORTED_NOTICE)).toHaveCount(0);
});

test("処理後 PDF が無い Office 原本は共通の案内だけを表示し、ダウンロードを重複させない", async ({
  page,
}) => {
  await mockWorkspace(page, null);

  await page.goto("/documents/doc-1");

  const previewPanel = page
    .getByRole("heading", { name: "原本プレビュー" })
    .locator("xpath=ancestor::section[1]");
  await expect(previewPanel.getByRole("tab", { name: "処理後" })).toBeDisabled();
  await expect(previewPanel.getByText(UNSUPPORTED_NOTICE, { exact: true })).toBeVisible();
  await expect(previewPanel.getByText(OFFICE_REPROCESS_NOTICE)).toHaveCount(0);
  await expect(
    previewPanel.getByRole("link", { name: "ダウンロード", exact: true })
  ).toHaveAttribute("href", /\/api\/documents\/doc-1\/recipes\/recipe-1\/content\?disposition=attachment$/);
  await expect(
    previewPanel.getByRole("link", { name: "ファイルをダウンロード", exact: true })
  ).toHaveCount(0);
});

test("変換なし(passthrough)の処理後は無効化し、専用メッセージを出す", async ({ page }) => {
  await mockWorkspace(page, {
    object_storage_path: documentDetail.object_storage_path,
    content_type: documentDetail.content_type,
    file_name: documentDetail.file_name,
    profile: "passthrough",
    converted: false,
  });

  await page.goto("/documents/doc-1");

  const previewPanel = page
    .getByRole("heading", { name: "原本プレビュー" })
    .locator("xpath=ancestor::section[1]");
  const afterTab = previewPanel.getByRole("tab", { name: "処理後" });
  await expect(afterTab).toBeDisabled();
  await expect(afterTab).toHaveAttribute(
    "title",
    "ファイル準備は変換されていないため、処理後ファイルはありません。"
  );
});

test("Office の非 PDF artifact を PDF iframe で開かない", async ({ page }) => {
  await mockWorkspace(page, {
    ...documentDetail.preprocess_artifact,
    content_type: "application/json",
    file_name: "report.json",
  });

  await page.goto("/documents/doc-1");

  await expect(page.locator('iframe[src*="variant=prepared"]')).toHaveCount(0);
  await expect(page.getByText(UNSUPPORTED_NOTICE, { exact: true })).toBeVisible();
  await expect(page.getByText(OFFICE_REPROCESS_NOTICE)).toHaveCount(0);
});

for(const source of ["csv","xlsx","docx"] as const) {
  test(`JSONLの構造化原文で選択した記録を強調する (${source})`,{tag:"@responsive"},async({page},testInfo)=>{
    await mockWorkspace(page,{object_storage_path:"prepared/synthetic.jsonl",content_type:"application/vnd.production-ready.structured-records+jsonl",file_name:"synthetic.jsonl",profile:source === "xlsx" ? "excel_to_jsonl" : source === "docx" ? "word_to_jsonl" : "csv_to_jsonl",converted:true});
    const blocks=structuredSource[source].window.blocks;
    const anchor=structuredSource[source].element_id;
    const selected=blocks.find(block=>block.id===anchor)!;
    await page.route("**/recipes/recipe-1/extraction-view?**",route=>route.fulfill({json:ok({payload:{raw_text:blocks.map(b=>b.text).join("\n"),elements:blocks.map((block,order)=>({element_id:block.id,kind:block.kind,text:block.text,order,source_parser:"structured_records_jsonl",metadata:{}}))},offset:0,total:blocks.length,limit:25,complete:true})}));
    await page.route("**/recipes/recipe-1/chunk-window?**",route=>route.fulfill({json:ok({items:[{document_id:"doc-1",chunk_id:"structured-chunk",chunk_index:0,text:selected.text,element_ids:[anchor],metadata:{}}],offset:0,total:1,limit:25,complete:true})}));
    await page.route("**/api/documents/doc-1",route=>route.fulfill({json:ok({...documentDetail,file_name:`synthetic.${source}`})}));
    const requests:URLSearchParams[]=[];
    await page.route("**/recipes/recipe-1/reader?**",route=>{requests.push(new URL(route.request().url()).searchParams);return route.fulfill({json:ok({...structuredSource[source].window,revision:"er-recipe-1-r1"})});});
    await page.goto("/documents/doc-1");
    const reader=page.getByTestId("document-preview-pane").getByTestId("evidence-reader");
    await page.getByTestId("document-preview-pane").scrollIntoViewIfNeeded();
    await page.getByTestId("document-preview-pane").screenshot({path:testInfo.outputPath(`structured-source-initial-${source}.png`),animations:"disabled"});

    await expect(reader.locator("mark").first()).toBeVisible();
    expect(requests[0].get("expected_revision")).toBe("er-recipe-1-r1");
    await expect(reader.getByRole("table").last()).toBeVisible();
    const inspector=page.getByTestId("document-inspector-pane");
    await inspector.getByRole("tab",{name:"構造化要素",exact:true}).click();
    const element=inspector.getByRole("button").filter({hasText:selected.text.split("\n")[0]}).last();
    await element.focus();await page.keyboard.press("Enter");
    await expect.poll(()=>requests.some(query=>query.get("element_id")===anchor)).toBe(true);
    await inspector.getByRole("tab",{name:/Chunk \/ Citation/}).click();
    await page.getByTestId("document-chunk-list").getByRole("button").filter({hasText:selected.text.split("\n")[0]}).click();
    await expect(reader.locator("mark").first()).toBeVisible();

    await page.getByTestId("document-preview-pane").screenshot({path:testInfo.outputPath(`structured-source-${source}.png`),animations:"disabled"});
    await page.getByTestId("document-preview-pane").getByRole("tab",{name:"処理後",exact:true}).click();
    await expect(reader).toBeVisible();
    await expect(page.getByText(UNSUPPORTED_NOTICE,{exact:true})).toHaveCount(0);
    await expectNoPageOverflow(page);
    if (testInfo.project.name === "desktop") {
      for (const [theme, width] of [["light", 1920], ["dark", 1280]] as const) {
        await page.setViewportSize({width, height:1080});
        await page.evaluate(dark => document.documentElement.classList.toggle("dark", dark), theme === "dark");
        await expectNoPageOverflow(page);
        await page.getByTestId("document-preview-pane").screenshot({path:testInfo.outputPath(`structured-source-${source}-${theme}.png`),animations:"disabled"});
      }
    }
  });
}
