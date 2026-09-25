import { ConfirmProvider } from "@engchina/production-ready-ui";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { ModelSettingsPage, type ModelSettingsPayload } from "../src";
import { buildSectionSavePayload } from "../src/model/ModelSettingsPage";

const baseline: ModelSettingsPayload = {
  enterprise_ai: {
    endpoint: "https://saved.example",
    project_ocid: "ocid1.generativeaiproject.oc1..saved",
    api_key: "",
    has_api_key: true,
    clear_api_key: false,
    models: [{ model_id: "saved-llm", display_name: "Saved", vision_enabled: false }],
    default_model_id: "saved-llm",
    api_path: "/custom",
    vlm_input_mode: "files_api",
    text_payload_template: "",
    vision_payload_template: "",
    text_response_path: "/text",
    vision_response_path: "",
    timeout_seconds: 120,
    max_retries: 1,
    llm_max_output_tokens: 1600,
    vlm_max_output_tokens: 64000,
  },
  generative_ai: { embedding_model: "embed", embedding_dim: 1536, rerank_model: "rerank" },
};

describe("buildSectionSavePayload", () => {
  const draft: ModelSettingsPayload = {
    enterprise_ai: {
      ...baseline.enterprise_ai,
      endpoint: "https://draft.example",
      api_key: "sk-draft",
      models: [{ model_id: "draft-llm", display_name: "Draft", vision_enabled: true }],
      default_model_id: "draft-llm",
    },
    generative_ai: { ...baseline.generative_ai, rerank_model: "draft-rerank" },
  };

  it("保存する節の入力だけを送り、他の節と画面にない項目は保存済みの値を保つ", () => {
    const connection = buildSectionSavePayload(baseline, draft, "enterprise_connection");
    expect(connection.enterprise_ai.endpoint).toBe("https://draft.example");
    expect(connection.enterprise_ai.api_key).toBe("sk-draft");
    expect(connection.enterprise_ai.models).toEqual(baseline.enterprise_ai.models);
    expect(connection.generative_ai.rerank_model).toBe("rerank");
    expect(connection.enterprise_ai.api_path).toBe("/custom");
    expect(connection.enterprise_ai.vlm_input_mode).toBe("files_api");

    const models = buildSectionSavePayload(baseline, draft, "enterprise_models");
    expect(models.enterprise_ai.endpoint).toBe("https://saved.example");
    expect(models.enterprise_ai.default_model_id).toBe("draft-llm");

    const genai = buildSectionSavePayload(baseline, draft, "generative_ai");
    expect(genai.generative_ai.rerank_model).toBe("draft-rerank");
    expect(genai.enterprise_ai.api_key).toBe("");
  });
});

describe("ModelSettingsPage", () => {
  it("読み込み中は状態表示を出す", () => {
    const pending = () => new Promise<never>(() => undefined);
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <QueryClientProvider client={new QueryClient()}>
          <ConfirmProvider>
            <ModelSettingsPage
              api={{
                getModelSettings: pending,
                updateModelSettings: pending,
                testModelSettings: pending,
              }}
            />
          </ConfirmProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
    expect(html).toContain('data-testid="settings-model-loading"');
    expect(html).toContain("モデル設定を読み込んでいます。");
  });
});
