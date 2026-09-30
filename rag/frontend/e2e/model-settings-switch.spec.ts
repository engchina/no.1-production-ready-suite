import { expect, test, type Page } from "@playwright/test";
import { expectMainScrollEndsAtContent, expectNoPageOverflow, mockLocalAuth } from "./_helpers";

const SECONDARY_CONNECTION = {
  connection_id: "secondary",
  display_name: "シカゴ",
  endpoint: "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1",
  project_ocid: "ocid1.generativeaiproject.oc1.us-chicago-1.secondary",
  api_key: "",
  has_api_key: true,
  clear_api_key: false,
};

function createModelSettings({ secondary = false }: { secondary?: boolean } = {}) {
  return {
    settings: {
      enterprise_ai: {
        connections: [
          {
            connection_id: "primary",
            display_name: "",
            endpoint: "",
            project_ocid: "ocid1.generativeaiproject.oc1.us-chicago-1.example",
            api_key: "",
            has_api_key: true,
            clear_api_key: false,
          },
          ...(secondary ? [SECONDARY_CONNECTION] : []),
        ],
        models: [
          {
            model_id: "enterprise-llm",
            display_name: "標準 LLM",
            vision_enabled: false,
            connection_id: "primary",
          },
          {
            model_id: "enterprise-vision",
            display_name: "Vision LLM",
            vision_enabled: true,
            connection_id: secondary ? "secondary" : "primary",
          },
        ],
        default_text_model_id: "enterprise-llm",
        default_vision_model_id: "enterprise-vision",
        api_path: "/responses",
        vlm_input_mode: "files_api",
        text_payload_template: '{"input":{"messages":"${messages}","params":"${parameters}"}}',
        vision_payload_template: '{"input":{"document":"${data_base64}"}}',
        text_response_path: "",
        vision_response_path: "",
        timeout_seconds: 60,
        max_retries: 3,
        llm_max_output_tokens: 1200,
        vlm_max_output_tokens: 65536,
      },
      generative_ai: {
        embedding_model: "cohere.embed-v4.0",
        embedding_dim: 1536,
        rerank_model: "cohere.rerank-v4.0-fast",
      },
    },
    model_settings_file: "model-settings.json",
    source: "runtime",
    secret_source: "environment",
    legacy_secret_detected: false,
  };
}

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 720, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`モデル設定は Enterprise AI の複数 LLM と既定のモデル 2 つを表示する (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapseSidebar) {
      await page.addInitScript(() => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
        );
      });
    }

    await mockModelSettings(page);
    await page.goto("/settings/model");

    await expect(page.getByPlaceholder(
      "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1"
    )).toBeVisible();
    await expect(page.getByLabel("Project OCID")).toHaveValue(
      "ocid1.generativeaiproject.oc1.us-chicago-1.example"
    );
    await expect(page.getByRole("textbox", { name: "API key" })).toBeVisible();
    await expect(page.getByText("保存済み", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("モデル ID 1")).toHaveValue("enterprise-llm");
    await expect(page.getByLabel("表示名 1")).toHaveValue("標準 LLM");
    await expect(page.getByLabel("モデル ID 2")).toHaveValue("enterprise-vision");
    // 登録モデルの一覧は登録と画像入力（Vision）対応の指定だけ。既定の選択は一覧の下（#499）。
    await expect(page.getByRole("radio", { name: /既定/ })).toHaveCount(0);
    await expect(page.getByRole("switch", { name: "画像入力（Vision）に対応 1" })).toHaveAttribute(
      "aria-checked",
      "false"
    );
    await expect(page.getByRole("switch", { name: "画像入力（Vision）に対応 2" })).toHaveAttribute(
      "aria-checked",
      "true"
    );
    await expect(page.getByRole("heading", { name: "既定のモデル" })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "既定の Vision モデル" })).toContainText(
      "Vision LLM"
    );
    await expect(page.getByRole("combobox", { name: "既定のテキストモデル" })).toContainText(
      "標準 LLM"
    );
    // 共有画面（NL2SQL と同じ。#103）は詳細項目・構成状態・プレビューを表示しない。
    await expect(page.getByLabel("API パス")).toHaveCount(0);
    await expect(page.getByRole("combobox", { name: "VLM 入力方式" })).toHaveCount(0);
    await expect(page.getByLabel("最大リトライ回数")).toHaveCount(0);
    await expect(page.getByLabel("回答生成 payload template")).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "構成状態" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: ".env プレビュー" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "JSON プレビュー" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "運用メモ" })).toHaveCount(0);
    await expect(
      page.getByText("OpenAI-compatible gateway の Bearer 認証で使います。")
    ).toBeVisible();
    await expect(page.getByPlaceholder("業務 RAG 標準").first()).toBeVisible();

    await expectControlContentToBeVerticallyCentered(
      page.getByRole("switch", { name: "画像入力（Vision）に対応 2" }),
      "span[aria-hidden='true']"
    );
    const enterpriseSave = page.getByRole("button", { name: "OCI Enterprise AI: 保存" });
    const modelsSave = page.getByRole("button", { name: "登録モデル: 保存" });
    const genaiSave = page.getByRole("button", { name: "OCI Generative AI: 保存" });
    const enterpriseTestButton = page.getByRole("button", { name: "enterprise-llm をテスト" });
    const embeddingTestButton = page.getByRole("button", {
      name: "cohere.embed-v4.0 をテスト",
    });
    const rerankTestButton = page.getByRole("button", {
      name: "cohere.rerank-v4.0-fast をテスト",
    });
    await expectActionInsideCard(page, "OCI Enterprise AI", enterpriseSave);
    await expectActionInsideCard(page, "登録モデル", modelsSave);
    await expectActionInsideCard(page, "登録モデル", enterpriseTestButton);
    await expectActionInsideCard(page, "OCI Generative AI", genaiSave);
    await expectActionInsideCard(page, "OCI Generative AI", embeddingTestButton);
    await expectActionInsideCard(page, "OCI Generative AI", rerankTestButton);

    for (const button of [
      enterpriseSave,
      modelsSave,
      genaiSave,
      enterpriseTestButton,
      embeddingTestButton,
      rerankTestButton,
      page.getByRole("button", { name: "追加", exact: true }),
      page.getByRole("button", { name: "モデルを削除 1" }),
    ]) {
      await expectControlContentToBeVerticallyCentered(button, "svg");
    }
    await expectNoPageOverflow(page);
    await expectMainScrollEndsAtContent(page);
  });
}

test("モデル設定は節ごとに保存し、画面にない項目は保存済みの値を送る", async ({ page }) => {
  let savedPayload: unknown;
  await mockModelSettings(page, (payload) => {
    savedPayload = payload;
  });
  await page.goto("/settings/model");

  await page.getByLabel("モデル ID 2").fill("enterprise-vision-v2");
  await page.getByPlaceholder(
    "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1"
  ).fill("https://unsaved.example");
  await page.getByRole("button", { name: "登録モデル: 保存" }).click();

  await expect(page.getByText("登録モデルを保存しました。").first()).toBeVisible();
  expect(savedPayload).toMatchObject({
    enterprise_ai: {
      // 別の節（Enterprise AI 接続）の未保存の入力は送らない。
      connections: [{ connection_id: "primary", endpoint: "" }],
      api_path: "/responses",
      vlm_input_mode: "files_api",
      text_payload_template: '{"input":{"messages":"${messages}","params":"${parameters}"}}',
      models: [{ model_id: "enterprise-llm" }, { model_id: "enterprise-vision-v2" }],
      // 既定に選んだモデルの ID を書き換えると、既定も追従する（#499）。
      default_text_model_id: "enterprise-llm",
      default_vision_model_id: "enterprise-vision-v2",
    },
  });
  // 保存していない節の入力は画面に残る。
  await expect(
    page.getByPlaceholder(
      "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1"
    )
  ).toHaveValue("https://unsaved.example");
});

for (const scheme of ["light", "dark"] as const) {
  test(`既定のモデルは Vision 対応のモデルだけを選べ、不正な選択は保存前にフィールドで止める (${scheme})`, async ({
    page,
  }) => {
    await page.addInitScript((theme) => {
      window.localStorage.setItem(
        "production-ready-rag.ui",
        JSON.stringify({ state: { theme }, version: 0 })
      );
    }, scheme);
    const patches: unknown[] = [];
    await mockModelSettings(page, (payload) => patches.push(payload));
    await page.goto("/settings/model");
    await expect
      .poll(() => page.evaluate(() => document.documentElement.classList.contains("dark")))
      .toBe(scheme === "dark");

    const vision = page.getByRole("combobox", { name: "既定の Vision モデル" });
    const text = page.getByRole("combobox", { name: "既定のテキストモデル" });
    await expect(vision).toContainText("Vision LLM");

    // 選択肢: Vision は Vision 対応のモデルだけ、テキストは未選択 + 全モデル。
    await vision.click();
    const visionOptions = page.getByRole("listbox", { name: "既定の Vision モデル" }).getByRole("option");
    await expect(visionOptions).toHaveCount(1);
    await expect(visionOptions.first()).toContainText("Vision LLM");
    await page.keyboard.press("Escape");
    await text.click();
    const textListbox = page.getByRole("listbox", { name: "既定のテキストモデル" });
    await expect(textListbox.getByRole("option")).toHaveCount(3);
    await textListbox.getByRole("option", { name: "既定の Vision モデルを使う" }).click();
    await expect(text).toContainText("既定の Vision モデルを使う");

    // 選んでいたモデルの Vision 対応を外すと、保存前にフィールドのエラーを出す。
    await page.getByRole("switch", { name: "画像入力（Vision）に対応 2" }).click();
    await expect(vision).toHaveAttribute("aria-invalid", "true");
    await expect(
      page.getByText("画像入力（Vision）に対応したモデルがありません。", { exact: false })
    ).toBeVisible();

    // 保存は送信せず、最初の不正な欄へフォーカスする。
    await page.getByRole("button", { name: "登録モデル: 保存" }).click();
    await expect(vision).toBeFocused();
    expect(patches).toHaveLength(0);

    // Vision 対応を戻して保存すると、既定のモデル 2 つを送る。
    await page.getByRole("switch", { name: "画像入力（Vision）に対応 2" }).click();
    await expect(vision).toHaveAttribute("aria-invalid", "false");
    await page.getByRole("button", { name: "登録モデル: 保存" }).click();
    await expect(page.getByText("登録モデルを保存しました。").first()).toBeVisible();
    expect(patches[0]).toMatchObject({
      enterprise_ai: { default_text_model_id: "", default_vision_model_id: "enterprise-vision" },
    });
    await expectNoPageOverflow(page);
  });
}

test("既定の Vision モデルに選んだモデルを一覧から削除すると、選び直しを案内する", async ({ page }) => {
  const patches: unknown[] = [];
  await mockModelSettings(page, (payload) => patches.push(payload));
  await page.goto("/settings/model");

  await page.getByRole("button", { name: "モデルを削除 2" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "削除" }).click();

  const vision = page.getByRole("combobox", { name: "既定の Vision モデル" });
  await expect(vision).toContainText("enterprise-vision");
  await expect(vision).toHaveAttribute("aria-invalid", "true");
  await expect(
    page.getByText("画像入力（Vision）に対応したモデルがありません。", { exact: false })
  ).toBeVisible();
  await page.getByRole("button", { name: "登録モデル: 保存" }).click();
  await expect(vision).toBeFocused();
  expect(patches).toHaveLength(0);
});

for (const scheme of ["light", "dark"] as const) {
  // desktop（1440px）と mobile（375px）は playwright.config の project で回す。
  test(`接続を 2 件にし、登録モデルごとに接続を選んで保存する (${scheme})`, async ({
    page,
  }, testInfo) => {
    const viewportWidth = page.viewportSize()?.width ?? 1440;
    await page.addInitScript(
      ({ theme, collapsed }) => {
        window.localStorage.setItem(
          "production-ready-rag.ui",
          JSON.stringify({ state: { theme, sidebarCollapsed: collapsed }, version: 0 })
        );
      },
      { theme: scheme, collapsed: viewportWidth < 768 }
    );
    const patches: Array<{ enterprise_ai: Record<string, unknown> }> = [];
    await mockModelSettings(page, (payload) =>
      patches.push(payload as { enterprise_ai: Record<string, unknown> })
    );
    await page.goto("/settings/model");

    await expect(page.getByRole("heading", { name: "接続 1（既定）" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "接続 2" })).toHaveCount(0);
    const modelConnection1 = page.getByRole("combobox", { name: "モデル 1 の接続" });
    await expect(modelConnection1).toContainText("接続 1");

    // 接続 2 を追加する。Endpoint URL が空のまま保存すると、欄のエラーで止める。
    await page.getByRole("button", { name: "接続を追加" }).click();
    const secondary = page.getByTestId("enterprise-connection-secondary");
    await expect(secondary.getByRole("heading", { name: "接続 2" })).toBeVisible();
    await expect(page.getByRole("button", { name: "接続を追加" })).toHaveCount(0);
    await expect(secondary.getByLabel("表示名")).toBeFocused();
    const secondaryEndpoint = page.locator("#enterprise-secondary-endpoint");
    await expect(secondaryEndpoint).toHaveAttribute("aria-required", "true");
    await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();
    await expect(secondaryEndpoint).toBeFocused();
    await expect(secondaryEndpoint).toHaveAttribute("aria-invalid", "true");
    expect(patches).toHaveLength(0);

    await secondary.getByLabel("表示名").fill("シカゴ");
    await secondaryEndpoint.fill("https://secondary.example/openai/v1");
    await page.locator("#enterprise-secondary-api-key").fill("sk-secondary-input");

    // 保存前の接続を選んだモデルは、登録モデルの保存を止めて先に接続の保存を案内する。
    await modelConnection1.click();
    await page.getByRole("listbox", { name: "モデル 1 の接続" }).getByRole("option", { name: /シカゴ/ }).click();
    await expect(modelConnection1).toContainText("シカゴ");
    await expect(modelConnection1).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("シカゴ はまだ保存されていません。", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "登録モデル: 保存" }).click();
    await expect(modelConnection1).toBeFocused();
    expect(patches).toHaveLength(0);

    await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();
    await expect(page.getByText("OCI Enterprise AI 接続設定を保存しました。").first()).toBeVisible();
    expect(patches[0]?.enterprise_ai.connections).toMatchObject([
      { connection_id: "primary" },
      {
        connection_id: "secondary",
        display_name: "シカゴ",
        endpoint: "https://secondary.example/openai/v1",
        api_key: "sk-secondary-input",
      },
    ]);
    await expect(modelConnection1).toHaveAttribute("aria-invalid", "false");

    await page.getByRole("button", { name: "登録モデル: 保存" }).click();
    await expect(page.getByText("登録モデルを保存しました。").first()).toBeVisible();
    expect(patches[1]?.enterprise_ai.models).toMatchObject([
      { model_id: "enterprise-llm", connection_id: "secondary" },
      { model_id: "enterprise-vision", connection_id: "primary" },
    ]);

    await expectNoPageOverflow(page);
    await page.getByRole("heading", { name: "接続 1（既定）" }).scrollIntoViewIfNeeded();
    await page.screenshot({
      path: testInfo.outputPath(`connections-${scheme}-${testInfo.project.name}.png`),
    });
    await page.getByTestId("enterprise-connection-secondary").screenshot({
      path: testInfo.outputPath(`connection-2-${scheme}-${testInfo.project.name}.png`),
    });
    await page.locator("#enterprise-model-catalog").screenshot({
      path: testInfo.outputPath(`model-catalog-${scheme}-${testInfo.project.name}.png`),
    });
  });
}

test("接続 2 を使うモデルがあるとき、削除は確認して接続 1 に移す", async ({ page }) => {
  const patches: Array<{ enterprise_ai: Record<string, unknown> }> = [];
  await mockModelSettings(
    page,
    (payload) => patches.push(payload as { enterprise_ai: Record<string, unknown> }),
    { secondary: true }
  );
  await page.goto("/settings/model");

  const modelConnection2 = page.getByRole("combobox", { name: "モデル 2 の接続" });
  await expect(modelConnection2).toContainText("シカゴ");
  const remove = page.getByRole("button", { name: "接続 2: 接続を削除" });

  // やめると何も変わらない。
  await remove.click();
  const dialog = page.getByRole("alertdialog");
  await expect(dialog).toContainText("シカゴ を使っている登録モデルがあります（Vision LLM）");
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page.getByTestId("enterprise-connection-secondary")).toBeVisible();

  await remove.click();
  await page.getByRole("alertdialog").getByRole("button", { name: "接続 1 に移して削除" }).click();
  await expect(page.getByTestId("enterprise-connection-secondary")).toHaveCount(0);
  await expect(modelConnection2).toContainText("接続 1");
  await expect(page.getByRole("button", { name: "接続を追加" })).toBeVisible();

  await page.getByRole("button", { name: "OCI Enterprise AI: 保存" }).click();
  await expect(page.getByText("OCI Enterprise AI 接続設定を保存しました。").first()).toBeVisible();
  expect(patches[0]?.enterprise_ai.connections).toHaveLength(1);
  expect(patches[0]?.enterprise_ai.models).toMatchObject([
    { connection_id: "primary" },
    { connection_id: "primary" },
  ]);
});

test("モデル設定はモデルごとのテスト成功と失敗を行内に表示する", async ({ page }) => {
  await mockModelSettings(page);
  await page.goto("/settings/model");

  await page.getByRole("button", { name: "enterprise-llm をテスト" }).click();
  await expect(
    page.getByText("Enterprise AI の回答生成モデル「enterprise-llm」から応答を取得しました。")
  ).toBeVisible();
  await expect(page.getByText("surface")).toBeVisible();
  await expect(page.getByText("llm", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "cohere.embed-v4.0 をテスト" }).click();
  await expect(
    page.getByText("Embedding モデル「cohere.embed-v4.0」のテストに失敗しました。")
  ).toBeVisible();
  await expect(page.getByText("確認ポイント")).toBeVisible();
  await expect(page.getByText("OCI config、設定名、region")).toBeVisible();

  await expect(page.getByText("ServiceError")).toBeVisible();
  // 共有の結果パネルは生のエラー文を表示しない（NL2SQL と同じ。#103）。
  await expect(page.getByText("401 Unauthorized: invalid model")).toHaveCount(0);
});

async function mockModelSettings(
  page: Page,
  onPatch?: (payload: unknown) => void,
  { secondary = false }: { secondary?: boolean } = {}
) {
  // 保存した内容を次の取得で返す（接続の保存の後に登録モデルを保存する流れ。#533）。
  let current = createModelSettings({ secondary });
  await page.route("**/api/settings/model", async (route) => {
    const request = route.request();
    if (request.method() === "PATCH") {
      const payload = request.postDataJSON();
      onPatch?.(payload);
      current = { ...current, settings: payload };
    }
    const data = current;
    await route.fulfill({
      json: {
        data,
        error_messages: [],
        warning_messages: [],
      },
    });
  });
  await page.route("**/api/settings/model/test", async (route) => {
    const payload = route.request().postDataJSON();
    const failed = payload.target_type === "embedding";
    await route.fulfill({
      json: {
        data: {
          status: failed ? "failed" : "success",
          target_type: payload.target_type,
          model_id: payload.model_id,
          message: failed
            ? `Embedding モデル「${payload.model_id}」のテストに失敗しました。`
            : `Enterprise AI の回答生成モデル「${payload.model_id}」から応答を取得しました。`,
          troubleshooting: failed
            ? [
                "OCI config、設定名、region、compartment OCID をサーバー側の実行環境から参照できるか確認してください。",
              ]
            : [],
          raw_error: failed ? "401 Unauthorized: invalid model" : null,
          error_type: failed ? "ServiceError" : null,
          elapsed_ms: 42,
          checked_at: "2026-06-14T00:00:00Z",
          details: failed ? {} : { surface: "llm", response_chars: 8 },
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

async function expectActionInsideCard(
  page: Page,
  heading: string,
  button: ReturnType<Page["getByRole"]>
) {
  const card = page
    .getByRole("heading", { name: heading })
    .locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]"
    );
  const cardBox = await card.boundingBox();
  const buttonBox = await button.boundingBox();

  expect(cardBox).not.toBeNull();
  expect(buttonBox).not.toBeNull();
  expect(buttonBox!.x + buttonBox!.width).toBeLessThanOrEqual(
    cardBox!.x + cardBox!.width + 1
  );
  expect(buttonBox!.y + buttonBox!.height).toBeLessThanOrEqual(
    cardBox!.y + cardBox!.height + 1
  );
}

async function expectControlContentToBeVerticallyCentered(
  control: ReturnType<Page["getByRole"]>,
  childSelector: string
) {
  const controlBox = await control.boundingBox();
  const childBox = await control.locator(childSelector).first().boundingBox();

  expect(controlBox).not.toBeNull();
  expect(childBox).not.toBeNull();

  const controlCenter = controlBox!.y + controlBox!.height / 2;
  const childCenter = childBox!.y + childBox!.height / 2;
  expect(Math.abs(controlCenter - childCenter)).toBeLessThanOrEqual(0.75);
}
