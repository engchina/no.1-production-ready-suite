import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
  await mockParserServiceStatuses(page);
  await mockExternalParserStatuses(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`文書解析設定は稼働状況を表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }
    await mockParserAdapters(page);

    await page.goto("/settings/parser-adapters");

    await expect(page.getByRole("heading", { name: "文書解析", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByRole("radio", { name: /^Local/ })).toHaveCount(0);
    await expect(page.getByRole("radio", { name: /Docling.*CPU.*稼働中/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Unstructured.*CPU.*縮退/ })).toBeVisible();
    // 既定の解析エンジンは Docling(#286)。Unstructured は明示選択したときだけ使う。
    await expect(page.getByRole("radio", { name: /Docling.*既定の解析エンジン/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /既定の解析エンジン/ })).toHaveCount(1);
    await expect(
      page.getByRole("radio", { name: /Unstructured.*選んだ場合だけ使用/ })
    ).toBeVisible();
    await expect(page.getByRole("radio", { name: /MinerU.*GPU.*未設定/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /Dots\.OCR.*GPU.*設定済み/ })).toBeVisible();
    // Marker / Unlimited-OCR / GLM-OCR は #270 で削除した。選択肢に出さない。
    await expect(page.getByRole("radio", { name: /Marker|Unlimited-OCR|GLM-OCR/ })).toHaveCount(0);
    await expect(
      page.getByRole("radio", { name: /OCI Generative AI \(Vision\).*OCI.*稼働中/ })
    ).toBeVisible();
    // エンジン名は本物のラジオのラベル（カード）の文字（#469）。
    const engineNames = (
      await page
        .getByRole("radio")
        .evaluateAll((elements) =>
          elements.map((element) => (element as HTMLInputElement).labels?.[0]?.textContent ?? "")
        )
    ).map((text) => text.replace(/\s+/g, " ").trim());
    expect(engineNames).toHaveLength(6);
    expect(engineNames[0]).toContain("Docling");
    expect(engineNames[1]).toContain("Unstructured");
    expect(engineNames[2]).toContain("MinerU");
    expect(engineNames[3]).toContain("Dots.OCR");
    expect(engineNames[4]).toContain("OCI Generative AI (Vision)");
    expect(engineNames[5]).toContain("OCI Document Understanding");
    await expect(page.getByText("外部 GPU 解析エンジンの接続")).toBeVisible();
    await expect(page.getByLabel("Endpoint")).toHaveCount(2);
    await expect(page.getByLabel("Model")).toHaveCount(2);
    await expect(page.getByLabel("API key", { exact: true })).toHaveCount(2);
    await expect(page.getByText("運用診断", { exact: true })).toHaveCount(0);
    await expect(page.getByText("解析方式の稼働状況")).toHaveCount(0);
    await expect(page.getByText("原本種別ごとの実行順")).toHaveCount(0);
    await expect(page.getByText("未導入", { exact: true })).toHaveCount(0);
    await expect(page.getByText("パッケージ未導入", { exact: true })).toHaveCount(0);
    await expect(
      page.getByRole("radio", { name: /OCI Document Understanding/ })
    ).toBeVisible();
    await expect(
      page.getByRole("radio", { name: /OCI Generative AI \(Vision\)/ })
    ).toBeVisible();
    await expect(page.getByText("未設定", { exact: true }).first()).toBeVisible();

    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    const navLink = (await openSidebarNav(page)).getByRole("link", { name: "文書解析" });
    await expect(navLink).toHaveAttribute("aria-current", "page");
    await navLink.focus();
    await expect(navLink).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/settings\/parser-adapters$/);
    await expectNoHorizontalOverflow(page);
  });
}

const SCREENSHOT_DIR = process.env.RAG_E2E_SCREENSHOT_DIR;

async function useTheme(page: Page, scheme: "light" | "dark") {
  await page.addInitScript((theme) => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { theme }, version: 0 })
    );
  }, scheme);
}

async function expectTheme(page: Page, scheme: "light" | "dark") {
  await expect
    .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
    .toBe(scheme === "dark");
}

/** 文書解析の GET / PATCH。PATCH の payload を記録し、保存後の値を返す。 */
async function mockParserAdaptersWithPostParse(
  page: Page,
  initial: { vision_enabled: boolean; field_extraction_enabled: boolean; navigation_summary_enabled: boolean }
) {
  const patches: Record<string, unknown>[] = [];
  let current = { ...initial };
  const envelope = () =>
    parserAdapterEnvelope({
      ...current,
      adapter_backend: "docling",
      effective_order: ["docling"],
      config_source: "runtime",
      adapters: [
        { ...disabledAdapter("docling"), enabled: true, selected: true, installed: true, status: "active" },
        disabledAdapter("unstructured"),
        disabledAdapter("mineru"),
        disabledAdapter("dots_ocr"),
      ],
    });
  await page.route("**/api/settings/parser-adapters", async (route) => {
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      patches.push(payload);
      current = { ...current, ...payload } as typeof current;
    }
    await route.fulfill({ json: envelope() });
  });
  return patches;
}

async function mockExtractionFields(
  page: Page,
  initial: { name: string; description: string; value_type: string }[] = []
) {
  const patches: unknown[] = [];
  let fields = initial;
  await page.route("**/api/settings/extraction-fields", async (route) => {
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON() as { fields: typeof fields };
      patches.push(payload);
      fields = payload.fields;
    }
    await route.fulfill({ json: { data: { fields }, error_messages: [], warning_messages: [] } });
  });
  return patches;
}

for (const scheme of ["light", "dark"] as const) {
  test(`解析後の処理は Vision・項目抽出・章節木を処理順に並べ、全体の既定を保存する (${scheme})`, async ({
    page,
  }, testInfo) => {
    // #528: Vision は解析エンジンに関係なく使えるので、全体の既定の入口をこの画面に置く。
    await useTheme(page, scheme);
    const patches = await mockParserAdaptersWithPostParse(page, {
      vision_enabled: false,
      field_extraction_enabled: false,
      navigation_summary_enabled: false,
    });
    await mockExtractionFields(page);

    await page.goto("/settings/parser-adapters");
    await expectTheme(page, scheme);

    const card = page.getByRole("heading", { name: "解析後の処理", exact: true }).locator("xpath=ancestor::*[.//ol][1]");
    await expect(card.getByRole("heading", { level: 3 })).toHaveText([
      "1. 図・画像を AI で読み取る（Vision）",
      "2. メタデータ/項目抽出",
      "3. ナビゲーション要約(章節木)",
    ]);
    await expect(card).toContainText("解析エンジンに関係なく");
    await expect(card).toContainText("既定の Vision モデル");
    await expect(card.getByRole("link", { name: "モデル設定を開く" })).toHaveAttribute("href", "/settings/model");

    const vision = page.getByRole("switch", { name: "図・画像を AI で読み取る（Vision）" });
    const fieldExtraction = page.getByRole("switch", { name: "メタデータ/項目抽出" });
    const navigation = page.getByRole("switch", { name: "ナビゲーション要約(章節木)" });
    await expect(vision).toHaveAttribute("aria-checked", "false");
    await vision.click();
    await fieldExtraction.click();
    await expect(vision).toHaveAttribute("aria-checked", "true");
    // 項目の定義が 0 件のまま有効にすると、何も抽出しないことを知らせる。
    await expect(card.getByText(/項目の定義がないため/)).toBeVisible();
    await expect(card.getByText("未保存の変更があります。")).toBeVisible();

    if (SCREENSHOT_DIR) {
      await card.screenshot({ path: `${SCREENSHOT_DIR}/parser-post-parse-${testInfo.project.name}-${scheme}.png` });
    }

    await card.getByRole("button", { name: "解析後の処理を保存" }).click();
    await expect(card.getByText("解析後の処理を保存しました。")).toBeVisible();
    // 解析エンジンの設定は送らない（「解析後の処理」だけを保存する）。
    expect(patches).toEqual([
      { vision_enabled: true, field_extraction_enabled: true, navigation_summary_enabled: false },
    ]);
    await expect(navigation).toHaveAttribute("aria-checked", "false");
    // 解析エンジンの側は未保存にならない。
    await expect(page.getByText("未保存の変更があります。")).toHaveCount(0);
    await expectNoHorizontalOverflow(page);
  });
}

test("項目抽出の項目の定義を追加・検証・保存できる", async ({ page }) => {
  await mockParserAdaptersWithPostParse(page, {
    vision_enabled: false,
    field_extraction_enabled: true,
    navigation_summary_enabled: false,
  });
  const patches = await mockExtractionFields(page, [
    { name: "請求書番号", description: "請求書の番号", value_type: "string" },
  ]);

  await page.goto("/settings/parser-adapters");

  const definitions = page.getByText("抽出する項目の定義", { exact: true });
  await expect(page.getByText("1 件", { exact: true })).toBeVisible();
  await definitions.click();
  const editor = page.getByTestId("extraction-fields-editor");
  await expect(editor.getByLabel("項目名")).toHaveValue("請求書番号");

  await editor.getByRole("button", { name: "項目を追加" }).click();
  await expect(editor.getByLabel("項目名").nth(1)).toBeFocused();
  // 項目名が空のまま保存すると、その行で止める。
  await editor.getByRole("button", { name: "項目の定義を保存" }).click();
  await expect(editor.getByText("項目名を入力してください。")).toBeVisible();
  await expect(editor.getByLabel("項目名").nth(1)).toBeFocused();
  // 大文字小文字の違いだけの重複も止める。
  await editor.getByLabel("項目名").nth(1).fill("請求書番号");
  await editor.getByRole("button", { name: "項目の定義を保存" }).click();
  await expect(editor.getByText("同じ項目名がほかの行にあります。")).toBeVisible();
  expect(patches).toEqual([]);

  await editor.getByLabel("項目名").nth(1).fill(" 合計金額 ");
  await editor.getByLabel("説明", { exact: true }).nth(1).fill("税込の合計");
  await editor.getByRole("combobox", { name: "値の型" }).nth(1).click();
  await page.getByRole("option", { name: "数値" }).click();
  await editor.getByRole("button", { name: "項目の定義を保存" }).click();
  await expect(editor.getByText("項目の定義を保存しました。")).toBeVisible();
  expect(patches).toEqual([
    {
      fields: [
        { name: "請求書番号", description: "請求書の番号", value_type: "string" },
        { name: "合計金額", description: "税込の合計", value_type: "number" },
      ],
    },
  ]);
  await expect(page.getByText("2 件", { exact: true })).toBeVisible();
  if (SCREENSHOT_DIR) {
    await editor.screenshot({ path: `${SCREENSHOT_DIR}/extraction-fields-${test.info().project.name}.png` });
  }

  // 行を削除すると未保存になり、破棄で保存値に戻る。
  await editor.getByRole("button", { name: "項目 2 を削除" }).click();
  await expect(editor.getByLabel("項目名")).toHaveCount(1);
  await editor.getByRole("button", { name: "変更を破棄" }).click();
  await expect(editor.getByLabel("項目名")).toHaveCount(2);
  await expectNoHorizontalOverflow(page);
});

test("図・画像の読み取りプロンプトは Vision の項目の中で編集できる", async ({ page }) => {
  // 読み取りの指示は全体で 1 つ。Vision の全体の既定を無効にしていても編集できる(#497 / #528)。
  await mockParserAdaptersWithPostParse(page, {
    vision_enabled: false,
    field_extraction_enabled: false,
    navigation_summary_enabled: false,
  });
  await mockExtractionFields(page);
  let saved: unknown = null;
  await page.route("**/api/settings/docrag-prompts**", async (route) => {
    if (route.request().method() === "PUT") saved = route.request().postDataJSON();
    const content = saved ? (saved as { content: string }).content : "既定の指示 {{image_metadata}}";
    await route.fulfill({
      json: {
        data: {
          prompts: [
            {
              key: "image_retrieval",
              content,
              default_content: "既定の指示 {{image_metadata}}",
              customized: Boolean(saved),
              required_placeholders: ["image_metadata"],
              updated_at: saved ? "2026-09-26T01:00:00Z" : null,
            },
          ],
          stages: [],
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/parser-adapters");

  const visionItem = page.locator("#post-parse-vision");
  await visionItem.locator("summary", { hasText: "図・画像の読み取りプロンプト" }).click();
  await expect(visionItem).toContainText("解析エンジンに関係なく");
  await expect(visionItem.getByText("既定値", { exact: true })).toBeVisible();
  await visionItem.getByLabel("プロンプト").fill("図の要点を短く {{image_metadata}}");
  await visionItem.getByRole("button", { name: "プロンプトを保存" }).click();
  await expect.poll(() => saved).toEqual({ content: "図の要点を短く {{image_metadata}}" });
  await expect(visionItem.getByText("プロンプトを保存しました。")).toBeVisible();
  await expect(visionItem.getByText(/^編集済み/)).toBeVisible();
});

test("hash 付きの URL で解析後の処理の項目へ移動する", async ({ page }) => {
  // レシピの「グローバル設定を開く」と設定の概要のリンクの移動先(#528)。読み込み後に描画される節にも届く。
  await mockParserAdaptersWithPostParse(page, {
    vision_enabled: false,
    field_extraction_enabled: false,
    navigation_summary_enabled: true,
  });
  await mockExtractionFields(page);

  await page.goto("/settings/parser-adapters#post-parse-navigation-summary");

  const target = page.locator("#post-parse-navigation-summary");
  await expect(target).toBeInViewport();
  await expect(target.getByRole("switch", { name: "ナビゲーション要約(章節木)" })).toHaveAttribute(
    "aria-checked",
    "true"
  );
});

test("文書解析設定取得に失敗したら再試行できる", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 760 });
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      status: 503,
      json: {
        data: null,
        error_messages: ["文書解析設定を取得できませんでした。"],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/parser-adapters");

  await expect(page.getByRole("alert")).toContainText(
    "文書解析設定を取得できませんでした。"
  );
  await expect(page.getByRole("button", { name: "再試行" })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});

test("文書解析設定は使用エンジンを保存できる", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
  let savedPayload: unknown = null;
  await page.route("**/api/settings/parser-adapters", async (route) => {
    if (route.request().method() === "PATCH") {
      savedPayload = route.request().postDataJSON();
      await route.fulfill({
        json: parserAdapterEnvelope({
          adapter_backend: "mineru",
          effective_order: ["mineru"],
          config_source: "runtime",
          adapters: [
            disabledAdapter("docling"),
            disabledAdapter("unstructured"),
            { ...disabledAdapter("mineru"), enabled: true, selected: true, status: "active" },
            disabledAdapter("dots_ocr"),
          ],
        }),
      });
      return;
    }
    await route.fulfill({
      json: parserAdapterEnvelope({
        adapter_backend: "local",
        effective_order: [],
        config_source: "runtime",
        adapters: [
          disabledAdapter("docling"),
          disabledAdapter("unstructured"),
          disabledAdapter("mineru"),
          disabledAdapter("dots_ocr"),
        ],
      }),
    });
  });

  await page.goto("/settings/parser-adapters");

  // local は廃止。既定の Docling で解析する旨を出し、microservice エンジン(MinerU)を選択する。
  await expect(page.getByRole("radio", { name: /^Local/ })).toHaveCount(0);
  await expect(page.getByText(/未選択時は既定の Docling で解析します/)).toBeVisible();
  const mineruBackend = page.getByRole("radio", { name: /MinerU/ });
  await mineruBackend.focus();
  await expect(mineruBackend).toBeFocused();
  // 本物のラジオは Space（または矢印キー）で選ぶ（#469）。
  await page.keyboard.press("Space");
  await expect(mineruBackend).toBeChecked();

  await expect(page.getByText("未保存の変更があります。")).toBeVisible();

  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect(page.getByText("文書解析設定を保存しました。")).toBeVisible();
  expect(savedPayload).toEqual({
    adapter_backend: "mineru",
    docling_enabled: false,
    unstructured_enabled: false,
    mineru_enabled: true,
    dots_ocr_enabled: false,
    connections: defaultConnections().map((connection) => ({
      backend: connection.backend,
      endpoint: connection.endpoint,
      ...(connection.backend === "mineru" ? {} : { model: connection.model }),
    })),
  });
  await expectNoHorizontalOverflow(page);
});

test("外部 GPU 接続は検証・秘密鍵保持・明示削除ができる", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
  const payloads: Array<Record<string, unknown>> = [];
  let connections = defaultConnections().map((connection) => ({ ...connection }));
  await page.route("**/api/settings/parser-adapters", async (route) => {
    if (route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON() as Record<string, unknown>;
      payloads.push(payload);
      const updates = payload.connections as Array<Record<string, unknown>>;
      connections = connections.map((connection) => {
        const update = updates.find((item) => item.backend === connection.backend);
        if (!update) return connection;
        return {
          ...connection,
          endpoint: String(update.endpoint ?? connection.endpoint),
          model:
            connection.backend === "mineru"
              ? null
              : String(update.model ?? connection.model ?? ""),
          api_key_configured: update.clear_api_key ? false : connection.api_key_configured,
          configured: Boolean(update.endpoint),
        };
      });
    }
    await route.fulfill({
      json: parserAdapterEnvelope({
        adapter_backend: "dots_ocr",
        effective_order: ["dots_ocr"],
        config_source: "runtime",
        connections,
        adapters: [
          disabledAdapter("docling"),
          disabledAdapter("unstructured"),
          disabledAdapter("mineru"),
          { ...disabledAdapter("dots_ocr"), enabled: true, selected: true, status: "active" },
        ],
      }),
    });
  });
  let statusChecks = 0;
  await page.route("**/api/settings/parser-adapters/dots_ocr/status", async (route) => {
    statusChecks += 1;
    await route.fulfill({
      json: {
        data: {
          backend: "dots_ocr",
          status: statusChecks === 1 ? "available" : "model_missing",
          version: null,
          warning_code: statusChecks === 1 ? null : "external_parser_model_missing",
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });

  await page.goto("/settings/parser-adapters");
  const endpoint = page.locator("#external-parser-dots_ocr-endpoint");
  const model = page.locator("#external-parser-dots_ocr-model");
  const card = endpoint.locator(
    "xpath=ancestor::*[.//button[normalize-space()='接続を確認']][1]"
  );

  await card.getByRole("button", { name: "接続を確認" }).click();
  await expect(card.getByText("接続できました。")).toBeVisible();

  await endpoint.fill("ftp://invalid.example.com");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(
    page.getByText(
      "http または https の Endpoint を入力してください。認証情報、query、fragment は URL に含められません。"
    )
  ).toBeVisible();
  await expect(endpoint).toBeFocused();
  expect(payloads).toHaveLength(0);

  await endpoint.fill("https://dots-new.example.com/v1");
  await model.fill("served-dots");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("文書解析設定を保存しました。")).toBeVisible();
  const firstConnections = payloads[0].connections as Array<Record<string, unknown>>;
  const firstDots = firstConnections.find((item) => item.backend === "dots_ocr");
  expect(firstDots).toEqual({
    backend: "dots_ocr",
    endpoint: "https://dots-new.example.com/v1",
    model: "served-dots",
  });
  await expect(card.getByText("接続できました。")).toHaveCount(0);

  await card.getByRole("button", { name: "接続を確認" }).click();
  await expect(
    page.getByText("設定した Model が接続先にありません。Model 名を確認してください。")
  ).toBeVisible();

  await card.getByLabel("保存済み API key を削除").check();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  const secondConnections = payloads[1].connections as Array<Record<string, unknown>>;
  expect(secondConnections.find((item) => item.backend === "dots_ocr")).toEqual({
    backend: "dots_ocr",
    endpoint: "https://dots-new.example.com/v1",
    model: "served-dots",
    clear_api_key: true,
  });
  await expectNoHorizontalOverflow(page);
});

async function mockParserAdapters(page: Page, extra: object = {}) {
  await page.route("**/api/settings/parser-adapters", async (route) => {
    await route.fulfill({
      json: parserAdapterEnvelope({
          ...extra,
          adapter_backend: "docling",
          effective_order: ["docling"],
          config_source: "runtime",
          adapters: [
            {
              backend: "docling",
              package_name: "docling",
              import_name: "docling",
              distribution_name: "docling",
              install_package: "docling==2.103.0",
              enabled: true,
              selected: true,
              installed: true,
              status: "active",
              version: "1.2.3",
              warning_code: null,
            },
            {
              backend: "unstructured",
              package_name: "unstructured",
              import_name: "unstructured",
              distribution_name: null,
              install_package: "unstructured[all-docs]==0.18.32",
              enabled: true,
              selected: false,
              installed: false,
              status: "ignored",
              version: null,
              warning_code: "adapter_flag_ignored_by_backend",
            },
            {
              backend: "mineru",
              package_name: "external_api",
              import_name: "external_api",
              distribution_name: "mineru_file_parse",
              install_package: "外部 MinerU API",
              enabled: false,
              selected: false,
              installed: false,
              status: "disabled",
              version: null,
              warning_code: null,
            },
            {
              backend: "dots_ocr",
              package_name: "external_api",
              import_name: "external_api",
              distribution_name: "openai_chat_completions",
              install_package: "外部 Dots.OCR API",
              enabled: false,
              selected: false,
              installed: false,
              status: "disabled",
              version: null,
              warning_code: null,
            },
          ],
      }),
    });
  });
}

function parserAdapterEnvelope(data: object) {
  const sourceRoutes = defaultSourceRoutes();
  return {
    data: {
      source_routes: sourceRoutes,
      connections: defaultConnections(),
      service_backends: [
        {
          backend: "oci_genai_vision",
          selected: false,
          configured: true,
          warning_code: null,
        },
        {
          backend: "oci_document_understanding",
          selected: false,
          configured: false,
          warning_code: "oci_document_understanding_unconfigured",
        },
      ],
      backend_source_kind_matrix: {
        evidence_source: "runtime_routes",
        required_source_kinds: ["pdf", "image", "office", "html", "email", "audio", "text", "unknown"],
        covered_source_kinds: ["pdf", "image", "office", "html", "email", "audio", "text", "unknown"],
        missing_source_kinds: [],
        backend_source_kinds: {
          docling: ["pdf", "image", "office", "html"],
          dots_ocr: ["pdf", "image"],
          local: ["audio", "text", "unknown"],
        },
        route_evidence: sourceRoutes,
      },
      ...data,
    },
    error_messages: [],
    warning_messages: [],
  };
}

function defaultSourceRoutes() {
  return [
    {
      source_kind: "pdf",
      candidate_order: ["docling", "unstructured", "mineru", "dots_ocr"],
      attempted_order: ["docling", "unstructured"],
      active_order: ["docling"],
      selected_backend: "docling",
      reason_codes: ["selected_adapter_supported_for_source", "active_adapter_available_for_source"],
      warning_codes: ["unstructured_adapter_package_missing"],
    },
    {
      source_kind: "image",
      candidate_order: ["unstructured", "docling", "dots_ocr", "mineru"],
      attempted_order: ["docling"],
      active_order: ["docling"],
      selected_backend: "docling",
      reason_codes: ["selected_adapter_supported_for_source", "active_adapter_available_for_source"],
      warning_codes: [],
    },
    {
      source_kind: "email",
      candidate_order: ["unstructured"],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["selected_adapter_unsupported_for_source"],
      warning_codes: ["docling_adapter_source_unsupported"],
    },
    {
      source_kind: "audio",
      candidate_order: [],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["audio_transcription_not_configured", "selected_adapter_unsupported_for_source"],
      warning_codes: ["unsupported_audio", "audio_transcription_not_configured"],
    },
    {
      source_kind: "text",
      candidate_order: [],
      attempted_order: [],
      active_order: [],
      selected_backend: "local",
      reason_codes: ["local_parser_preferred_for_source", "selected_adapter_unsupported_for_source"],
      warning_codes: [],
    },
  ];
}

async function mockParserServiceStatuses(page: Page) {
  const statuses: Record<string, string> = {
    "parser-docling": "running",
    "parser-unstructured": "degraded",
    "parser-oci-genai-vision": "running",
    "parser-oci-document-understanding": "unconfigured",
  };
  await page.route("**/api/services/*/status", async (route) => {
    const serviceId = decodeURIComponent(
      route.request().url().match(/services\/([^/]+)\/status/)?.[1] ?? ""
    );
    const status = statuses[serviceId];
    await route.fulfill({
      status: status ? 200 : 404,
      json: {
        data: status
          ? {
              service_id: serviceId,
              category: "parser",
              profile: serviceProfileForId(serviceId),
              label_key: "settings.services.item.parserDocling",
              execution_policy: "selected_adapter",
              configured: status !== "unconfigured",
              status,
            }
          : null,
        error_messages: status ? [] : ["指定したサービスが見つかりません。"],
        warning_messages: [],
      },
    });
  });
}

async function mockExternalParserStatuses(page: Page) {
  await page.route("**/api/settings/parser-adapters/*/status", async (route) => {
    const backend = decodeURIComponent(
      route.request().url().match(/parser-adapters\/([^/]+)\/status/)?.[1] ?? ""
    );
    await route.fulfill({
      json: {
        data: {
          backend,
          status: "available",
          version: backend === "mineru" ? "3.4.0" : "served-model",
          warning_code: null,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

function defaultConnections() {
  return [
    {
      backend: "mineru",
      protocol: "mineru_file_parse",
      endpoint: "",
      model: null,
      api_key_configured: false,
      configured: false,
    },
    {
      backend: "dots_ocr",
      protocol: "openai_chat_completions",
      endpoint: "https://dots.example.com/v1",
      model: "rednote-hilab/dots.mocr",
      api_key_configured: true,
      configured: true,
    },
  ] as const;
}

function serviceProfileForId(serviceId: string) {
  if (serviceId.includes("oci")) return "oci";
  if (serviceId.includes("asr")) {
    return "gpu";
  }
  return "cpu";
}

function disabledAdapter(backend: "docling" | "unstructured" | "mineru" | "dots_ocr") {
  const external = backend === "mineru" || backend === "dots_ocr";
  return {
    backend,
    package_name: external ? "external_api" : backend,
    import_name: external ? "external_api" : backend,
    distribution_name: null,
    install_package:
      backend === "unstructured"
        ? "unstructured[all-docs]==0.18.32"
        : backend === "mineru"
          ? "外部 MinerU API"
          : backend === "dots_ocr"
            ? "外部 Dots.OCR API"
            : "docling==2.103.0",
    enabled: false,
    selected: false,
    installed: false,
    status: "disabled",
    version: null,
    warning_code: null,
  };
}

async function expectNoHorizontalOverflow(page: Page) {
  // documentElement と main の双方を検査する共通ヘルパーへ委譲(_helpers.ts)。
  await expectNoPageOverflow(page);
}
