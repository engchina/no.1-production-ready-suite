import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth } from "./_helpers";

const ROW_DEFAULTS = {
  preprocess_profile: null,
  parser_adapter_backend: null,
  chunking_strategy: null,
  unavailable_preprocess_profiles: [],
  unavailable_parser_backends: [],
  inherited_preprocess_profile: "passthrough",
  builtin: { preprocess_profile: null, parser_adapter_backend: null, chunking_strategy: null },
  builtin_unavailable_reason: null,
};

async function chooseSelectFieldOption(trigger: ReturnType<Page["getByTestId"]>, name: string) {
  await trigger.click();
  await trigger.page().getByRole("option", { name, exact: true }).click();
}

const FULL_PARSERS = ["unstructured"];

function settingsBody(overrides: Record<string, Record<string, unknown>> = {}) {
  const rows = [
    {
      file_type: "pdf",
      extensions: [".pdf"],
      preprocess_options: ["passthrough"],
      parser_options: { passthrough: ["docling", "unstructured", "dots_ocr"] },
    },
    {
      file_type: "word",
      extensions: [".docx", ".doc"],
      preprocess_options: ["passthrough", "office_to_pdf", "word_to_jsonl"],
      parser_options: {
        passthrough: FULL_PARSERS,
        office_to_pdf: ["docling", "unstructured"],
        word_to_jsonl: ["structured_records_jsonl"],
      },
      unavailable_parser_backends: ["dots_ocr"],
    },
    {
      file_type: "powerpoint",
      extensions: [".pptx", ".ppt"],
      preprocess_options: ["passthrough", "office_to_pdf"],
      parser_options: { passthrough: FULL_PARSERS, office_to_pdf: ["docling", "unstructured"] },
    },
    {
      file_type: "excel",
      extensions: [".xlsx", ".xls"],
      preprocess_options: ["passthrough"],
      unavailable_preprocess_profiles: ["excel_to_jsonl"],
      parser_options: { passthrough: FULL_PARSERS, excel_to_jsonl: ["structured_records_jsonl"] },
    },
    {
      file_type: "csv",
      extensions: [".csv"],
      preprocess_options: ["passthrough", "csv_to_jsonl"],
      parser_options: { passthrough: FULL_PARSERS, csv_to_jsonl: ["structured_records_jsonl"] },
    },
    {
      file_type: "text",
      extensions: [".txt"],
      preprocess_options: ["passthrough"],
      parser_options: { passthrough: FULL_PARSERS },
    },
    {
      file_type: "markdown",
      extensions: [".md", ".markdown"],
      preprocess_options: ["passthrough"],
      parser_options: { passthrough: FULL_PARSERS },
    },
    {
      file_type: "json",
      extensions: [".json"],
      preprocess_options: ["passthrough"],
      parser_options: { passthrough: ["unstructured", "xml_tree_json"] },
    },
    {
      file_type: "html",
      extensions: [".html", ".htm"],
      preprocess_options: ["passthrough"],
      parser_options: { passthrough: FULL_PARSERS },
    },
    {
      file_type: "image",
      extensions: [".jpg", ".jpeg", ".png"],
      preprocess_options: ["passthrough"],
      parser_options: { passthrough: ["docling", "unstructured", "dots_ocr"] },
    },
  ].map((row) => ({ ...ROW_DEFAULTS, ...row, ...(overrides[row.file_type] ?? {}) }));
  return {
    rows,
    chunking_options: [
      "structure_aware",
      "recursive_character",
      "small_to_big",
      "markdown_heading",
      "page_level",
      "fixed_size",
      "fixed_delimiter",
    ],
    global_defaults: {
      preprocess_profile: "passthrough",
      parser_adapter_backend: "docling",
      chunking_strategy: "small_to_big",
    },
    config_source: "runtime",
  };
}

async function mockSettings(page: Page, body: unknown = settingsBody()) {
  await page.route("**/api/settings/file-type-defaults", (route) =>
    route.fulfill({ json: { data: body, error_messages: [], warning_messages: [] } })
  );
}

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "375px", width: 375, height: 812 },
]) {
  test(`種類ごとの行に 3 つの選択欄を出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockSettings(page);

    await page.goto("/settings/file-type-defaults");

    await expect(page.getByRole("heading", { name: "形式別の既定" }).first()).toBeVisible();
    for (const fileType of ["pdf", "word", "powerpoint", "excel", "csv", "text", "markdown", "json", "html", "image"]) {
      await expect(page.getByTestId(`file-type-row-${fileType}`)).toBeVisible();
    }
    // 未設定のセルは「全体の既定に従う」。
    await expect(page.getByTestId("file-type-pdf-parser")).toContainText("全体の既定に従う");
    await expect(page.getByTestId("file-type-word-preprocess")).toContainText("全体の既定に従う");
    await expect(page.getByTestId("file-type-row-markdown")).toContainText(".markdown");
    await expectNoPageOverflow(page);
  });
}

test("対応しない選択肢は出さず、ファイル準備に応じて解析エンジンを絞る", async ({ page }) => {
  await mockSettings(page);
  await page.goto("/settings/file-type-defaults");

  await page.getByTestId("file-type-excel-preprocess").click();
  await expect(page.getByRole("option", { name: "Office→PDF" })).toHaveCount(0);
  await page.keyboard.press("Escape");

  await page.getByTestId("file-type-word-parser").click();
  await expect(page.getByRole("option", { name: "Docling" })).toHaveCount(0);
  await expect(page.getByRole("option", { name: "Unstructured" })).toHaveCount(1);
  await page.keyboard.press("Escape");

  await chooseSelectFieldOption(page.getByTestId("file-type-word-preprocess"), "Office→PDF");
  await page.getByTestId("file-type-word-parser").click();
  await expect(page.getByRole("option", { name: "Docling" })).toHaveCount(1);
});

test("構造化レコードの準備を選ぶと解析エンジンと固定分割を入れて保存する", async ({ page }) => {
  let patchBody: unknown;
  await page.route("**/api/settings/file-type-defaults", async (route) => {
    if (route.request().method() === "PATCH") {
      patchBody = route.request().postDataJSON();
      await route.fulfill({
        json: {
          data: settingsBody({
            csv: {
              preprocess_profile: "csv_to_jsonl",
              parser_adapter_backend: "structured_records_jsonl",
              chunking_strategy: "fixed_delimiter",
            },
          }),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: { data: settingsBody(), error_messages: [], warning_messages: [] } });
  });
  await page.goto("/settings/file-type-defaults");

  await chooseSelectFieldOption(page.getByTestId("file-type-csv-preprocess"), "CSV→JSONL");
  await expect(page.getByTestId("file-type-csv-parser")).toContainText("構造化レコード JSONL");
  await expect(page.getByTestId("file-type-csv-chunking")).toContainText("固定分割符");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("形式別の既定を保存しました。")).toBeVisible();
  expect(patchBody).toEqual({
    entries: {
      csv: {
        preprocess_profile: "csv_to_jsonl",
        parser_adapter_backend: "structured_records_jsonl",
        chunking_strategy: "fixed_delimiter",
      },
    },
  });
});

test("保存の 422 は何を直すかの文言を行の下に出す", async ({ page }) => {
  await page.route("**/api/settings/file-type-defaults", async (route) => {
    if (route.request().method() === "PATCH") {
      await route.fulfill({
        status: 422,
        json: {
          data: null,
          error_messages: ["Excel は「Office→PDF」で変換されません。ファイル準備で「Excel→JSONL」を選ぶか、全体の既定に従ってください。"],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: { data: settingsBody(), error_messages: [], warning_messages: [] } });
  });
  await page.goto("/settings/file-type-defaults");

  await chooseSelectFieldOption(page.getByTestId("file-type-pdf-chunking"), "ページ単位");
  await page.getByRole("button", { name: "保存" }).click();

  await expect(page.getByText("Excel は「Office→PDF」で変換されません。", { exact: false })).toBeVisible();
});

test("既定に戻すは組み込みの値を画面に入れ、保存で確定する", async ({ page }) => {
  const builtin = {
    preprocess_profile: "office_to_pdf",
    parser_adapter_backend: "docling",
    chunking_strategy: "page_level",
  };
  let patchBody: unknown;
  await page.route("**/api/settings/file-type-defaults", async (route) => {
    if (route.request().method() === "PATCH") patchBody = route.request().postDataJSON();
    await route.fulfill({
      json: {
        // powerpoint は保存値（全体の既定に従う）がある。
        data: settingsBody({ powerpoint: { builtin } }),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.goto("/settings/file-type-defaults");
  await expect(page.getByTestId("file-type-powerpoint-parser")).toContainText("全体の既定に従う");

  await page.getByRole("button", { name: "既定に戻す" }).click();

  await expect(page.getByTestId("file-type-powerpoint-preprocess")).toContainText("Office→PDF");
  await expect(page.getByTestId("file-type-powerpoint-parser")).toContainText("Docling");
  await expect(page.getByTestId("file-type-powerpoint-chunking")).toContainText("ページ単位");
  expect(patchBody).toBeUndefined();

  await page.getByRole("button", { name: "保存" }).click();
  // 組み込みの既定と同じ行は保存値にしない（保存値が無ければ組み込みの既定を使う）。
  await expect.poll(() => patchBody).toEqual({ entries: {} });
});

test("組み込みの既定を使えない行は理由を出す", async ({ page }) => {
  const reason = "ファイル準備「Excel→JSONL」が無効（RAG_PREPROCESS_ENABLED）のため、組み込みの既定を使わず全体の既定に従います。";
  await mockSettings(page, settingsBody({ excel: { builtin_unavailable_reason: reason } }));
  await page.goto("/settings/file-type-defaults");

  await expect(page.getByText(reason)).toBeVisible();
  await expect(page.getByTestId("file-type-excel-parser")).toContainText("全体の既定に従う");
});

test("使えない選択肢があるときは設定への導線を出す", async ({ page }) => {
  await mockSettings(page);
  await page.goto("/settings/file-type-defaults");

  await expect(
    page.getByTestId("file-type-row-excel").getByRole("link", { name: "ファイル準備のサービスを使えるようにする" })
  ).toHaveAttribute("href", "/settings/services");
  await expect(
    page.getByTestId("file-type-row-word").getByRole("link", { name: "解析エンジンの接続を設定する" })
  ).toHaveAttribute("href", "/settings/parser-adapters");
});
