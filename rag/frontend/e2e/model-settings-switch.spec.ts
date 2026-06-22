import { expect, test, type Page } from "@playwright/test";

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

function createModelSettings() {
  return {
    settings: {
      enterprise_ai: {
        endpoint: "",
        project_ocid: "ocid1.generativeaiproject.oc1.us-chicago-1.example",
        api_key: "",
        has_api_key: true,
        clear_api_key: false,
        models: [
          {
            model_id: "enterprise-llm",
            display_name: "標準 LLM",
            vision_enabled: false,
          },
          {
            model_id: "enterprise-vision",
            display_name: "Vision LLM",
            vision_enabled: true,
          },
        ],
        default_model_id: "enterprise-llm",
        api_path: "/responses",
        vlm_input_mode: "files_api",
        text_payload_template: '{"input":{"messages":"${messages}","params":"${parameters}"}}',
        vision_payload_template: '{"input":{"document":"${data_base64}"}}',
        text_response_path: "",
        vision_response_path: "",
        timeout_seconds: 60,
        max_retries: 3,
      },
      generative_ai: {
        embedding_model: "cohere.embed-v4.0",
        embedding_dim: 1536,
        rerank_model: "cohere.rerank-v4.0-fast",
      },
    },
    checks: {
      enterprise_ai: "ok",
      generative_ai: "ok",
      embedding_dim: "ok",
    },
    model_settings_file: "model-settings.json",
    source: "runtime",
  };
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

for (const viewport of [
  { name: "desktop", width: 1280, height: 720, collapseSidebar: false },
  { name: "mobile", width: 375, height: 812, collapseSidebar: true },
]) {
  test(`モデル設定は Enterprise AI の複数 LLM と既定モデルを表示する (${viewport.name})`, async ({ page }) => {
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
    await expect(page.getByRole("radio", { name: "既定 1" })).toBeChecked();
    await expect(page.getByRole("switch", { name: "Vision 1" })).toHaveAttribute(
      "aria-checked",
      "false"
    );
    await expect(page.getByRole("switch", { name: "Vision 2" })).toHaveAttribute(
      "aria-checked",
      "true"
    );
    await expect(page.getByLabel("API パス")).toHaveValue("/responses");
    await expect(page.getByRole("combobox", { name: "VLM 入力方式" })).toContainText(
      "Files API"
    );
    await expect(page.getByLabel("最大リトライ回数")).toHaveValue("3");
    await expect(page.getByText("カスタム gateway payload")).toHaveCount(0);
    await expect(page.getByLabel("回答生成 payload template")).toHaveCount(0);
    await expect(page.getByLabel("Vision/OCR payload template")).toHaveCount(0);
    await expect(
      page.getByText("OpenAI-compatible gateway の Bearer 認証で使います。")
    ).toBeVisible();
    await expect(page.getByText("LLM モデル ID")).toHaveCount(0);
    await expect(page.getByText("VLM モデル ID")).toHaveCount(0);

    await expectControlContentToBeVerticallyCentered(
      page.getByRole("switch", { name: "Vision 2" }),
      "span[aria-hidden='true']"
    );
    const saveButton = page.getByRole("button", { name: "モデル設定: 保存" });
    const enterpriseTestButton = page.getByRole("button", { name: "enterprise-llm をテスト" });
    const embeddingTestButton = page.getByRole("button", {
      name: "cohere.embed-v4.0 をテスト",
    });
    const rerankTestButton = page.getByRole("button", {
      name: "cohere.rerank-v4.0-fast をテスト",
    });

    await expect(page.getByRole("heading", { name: "構成状態" })).toHaveCount(1);
    await expect(page.getByRole("button", { name: "モデル設定: 構成チェック" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "モデル設定: 元に戻す" })).toHaveCount(0);
    await expect(page.getByRole("heading", { name: ".env プレビュー" })).toBeVisible();
    await expect(page.getByLabel(".env プレビュー")).toContainText(
      "MODEL_SETTINGS_FILE=model-settings.json"
    );
    await expect(page.getByRole("heading", { name: "JSON プレビュー" })).toBeVisible();
    await expect(page.getByLabel("JSON プレビュー")).toContainText('"version": 1');
    await expect(page.getByLabel("JSON プレビュー")).toContainText(
      '"api_key": "<保存済み secret>"'
    );
    await expect(page.getByLabel("JSON プレビュー")).toContainText(
      '"vlm_input_mode": "files_api"'
    );
    await expect(page.getByRole("heading", { name: "運用メモ" })).toBeVisible();
    await expectActionInsideCard(page, "OCI Generative AI", saveButton);
    await expectActionInsideCard(page, "OCI Enterprise AI", enterpriseTestButton);
    await expectActionInsideCard(page, "OCI Generative AI", embeddingTestButton);
    await expectActionInsideCard(page, "OCI Generative AI", rerankTestButton);

    for (const button of [
      saveButton,
      enterpriseTestButton,
      embeddingTestButton,
      rerankTestButton,
      page.getByRole("button", { name: "追加" }),
      page.getByRole("button", { name: "モデルを削除 1" }),
    ]) {
      await expectControlContentToBeVerticallyCentered(
        button,
        "svg"
      );
    }
  });
}

test("モデル設定は未充足の構成でも運用メモに注意を出して保存できる", async ({ page }) => {
  let savedPayload: unknown;
  await mockModelSettings(page, (payload) => {
    savedPayload = payload;
  });
  await page.goto("/settings/model");

  await page.getByRole("combobox", { name: "VLM 入力方式" }).click();
  await page.getByRole("option", { name: /Inline image/ }).click();
  await page.getByLabel("モデル ID 1").fill("");
  await page.getByLabel("モデル ID 2").fill("");
  const saveButton = page.getByRole("button", { name: "モデル設定: 保存" });
  await expect(saveButton).toBeEnabled();

  const memo = operationMemoCard(page);
  await expect(
    memo.getByText("Enterprise AI のモデル ID を 1 件以上入力してください。")
  ).toBeVisible();
  await expect(
    page.getByRole("alert").getByText("Enterprise AI のモデル ID を 1 件以上入力してください。")
  ).toHaveCount(0);

  await saveButton.click();

  await expect(page.getByText("モデル設定を保存しました。")).toBeVisible();
  expect(savedPayload).toMatchObject({
    enterprise_ai: {
      vlm_input_mode: "inline_image",
      models: [
        { model_id: "" },
        { model_id: "" },
      ],
    },
  });
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

  await page.getByText("実際のエラー詳細").click();
  await expect(page.getByText("エラー種別: ServiceError")).toBeVisible();
  await expect(page.getByText("401 Unauthorized: invalid model")).toBeVisible();
});

async function mockModelSettings(page: Page, onPatch?: (payload: unknown) => void) {
  await page.route("**/api/settings/model", async (route) => {
    const request = route.request();
    let data = createModelSettings();
    if (request.method() === "PATCH") {
      const payload = request.postDataJSON();
      onPatch?.(payload);
      data = {
        ...data,
        settings: payload,
        checks: {
          enterprise_ai: "missing",
          generative_ai: "ok",
          embedding_dim: "ok",
        },
      };
    }
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

function operationMemoCard(page: Page) {
  return page
    .getByRole("heading", { name: "運用メモ" })
    .locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' rounded-lg ')][1]"
    );
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
