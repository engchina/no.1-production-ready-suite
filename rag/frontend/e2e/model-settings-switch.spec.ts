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
    await expect(page.getByLabel("最大リトライ回数")).toHaveValue("3");
    await expect(page.getByText("カスタム gateway payload")).toBeVisible();
    await expect(page.getByText("設定あり", { exact: true })).toBeVisible();
    await expect(page.getByLabel("回答生成 payload template")).toBeHidden();
    await expect(page.getByLabel("Vision/OCR payload template")).toBeHidden();
    await expect(
      page.getByText("OpenAI-compatible gateway の Bearer 認証で使います。")
    ).toBeVisible();
    await expect(page.getByText("LLM モデル ID")).toHaveCount(0);
    await expect(page.getByText("VLM モデル ID")).toHaveCount(0);

    await page.getByText("カスタム gateway payload").click();
    await expect(page.getByLabel("回答生成 payload template")).toBeVisible();
    await expect(page.getByLabel("Vision/OCR payload template")).toBeVisible();
    await expect(page.getByText("OCI 公式の必須項目ではありません。")).toBeVisible();
  });
}

async function mockModelSettings(page: Page) {
  await page.route("**/api/settings/model", async (route) => {
    await route.fulfill({
      json: {
        data: createModelSettings(),
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}
