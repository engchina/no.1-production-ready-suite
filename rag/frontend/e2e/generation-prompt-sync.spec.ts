import { expect, test, type Page } from "@playwright/test";
import { expectNoPageOverflow, mockLocalAuth } from "./_helpers";

// 回答スタイル / 回答プロンプト画面の、背景の再取得と保存値の同期(#276)。

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

const PROFILES = [
  "grounded_concise",
  "detailed_cited",
  "strict_extractive",
  "structured_json",
  "bilingual_ja_en",
  "inline_cited",
  "custom",
];

function generationEnvelope(profile: string, revision: number) {
  return {
    data: {
      profile,
      structured_output: profile === "structured_json",
      profiles: PROFILES.map((name) => ({
        name,
        origin: "x",
        recommended_for: ["general"],
        selected: name === profile,
        structured_output: name === "structured_json",
        contract_mode: name === "custom" ? "custom" : "groundedness",
        repair_enabled: false,
      })),
      config_source: "oracle",
      revision,
      updated_at: "2026-09-28T00:00:00Z",
      active_prompt_version_id: "prompt-v1",
      custom_prompt_configured: true,
    },
    error_messages: [],
    warning_messages: [],
  };
}

/** TanStack Query の window focus 再取得を起こす(タブへ戻った操作に相当)。 */
async function refetchOnFocus(page: Page) {
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`未編集の回答スタイルは背景の再取得で保存値へ追従し、未保存扱いにしない (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    let current = generationEnvelope("grounded_concise", 3);
    await page.route("**/api/settings/generation", (route) => route.fulfill({ json: current }));

    await page.goto("/settings/generation");
    await expect(page.getByRole("radio", { name: /根拠重視・簡潔/ })).toBeChecked();

    // 別のタブ・利用者が詳細・出典明示へ変更した。
    current = generationEnvelope("detailed_cited", 4);
    await refetchOnFocus(page);

    await expect(page.getByRole("radio", { name: /詳細・出典明示/ })).toBeChecked();
    await expect(page.getByText("未保存の変更があります。")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
    await expectNoPageOverflow(page);
  });
}

test("選択中の回答スタイルは背景の再取得で上書きせず、選び始めた revision で保存する", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let current = generationEnvelope("grounded_concise", 3);
  let saved: unknown = null;
  await page.route("**/api/settings/generation", async (route) => {
    if (route.request().method() === "PATCH") {
      saved = route.request().postDataJSON();
      await route.fulfill({
        status: 409,
        json: {
          data: null,
          error_messages: ["回答スタイル設定は別の操作で更新されています。"],
          warning_messages: [],
        },
      });
      return;
    }
    await route.fulfill({ json: current });
  });

  await page.goto("/settings/generation");
  await page.getByRole("radio", { name: /厳密抽出/ }).check();
  current = generationEnvelope("detailed_cited", 4);
  await refetchOnFocus(page);

  // 利用者の選択は残る。
  await expect(page.getByRole("radio", { name: /厳密抽出/ })).toBeChecked();
  await expect(page.getByText("未保存の変更があります。")).toBeVisible();
  await page.getByRole("button", { name: "保存", exact: true }).click();

  // 選び始めた revision 3 を送るため、他の更新(revision 4)との競合として 409 になる。
  expect(saved).toEqual({ profile: "strict_extractive", expected_revision: 3 });
  await expect(page.getByText(/最新の設定を読み込みました/)).toBeVisible();
  await expect(page.getByRole("radio", { name: /詳細・出典明示/ })).toBeChecked();
  await expect(page.getByText("未保存の変更があります。")).toHaveCount(0);
});

function docragPromptsEnvelope(content: string) {
  return {
    data: {
      prompts: [
        {
          key: "vlm_answer",
          content,
          default_content: "既定 {{question}} {{images}}",
          customized: true,
          required_placeholders: ["question", "images"],
          updated_at: "2026-09-28T00:00:00Z",
        },
      ],
      stages: [],
    },
    error_messages: [],
    warning_messages: [],
  };
}

test("DocRAG の回答生成テンプレートは編集中の内容を背景の再取得で上書きしない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.route("**/api/settings/prompts", (route) =>
    route.fulfill({
      json: { data: { active_version_id: null, versions: [], settings_revision: 1 }, error_messages: [], warning_messages: [] },
    })
  );
  let content = "保存版 A {{question}} {{images}}";
  await page.route("**/api/settings/docrag-prompts**", (route) =>
    route.fulfill({ json: docragPromptsEnvelope(content) })
  );

  await page.goto("/settings/prompts");
  const editor = page.getByRole("textbox", { name: "テンプレート", exact: true });
  await expect(editor).toHaveValue(content);

  // 未編集なら保存値の変更へ追従する。
  content = "保存版 B {{question}} {{images}}";
  await refetchOnFocus(page);
  await expect(editor).toHaveValue(content);

  // 編集中は、保存値が変わっても編集内容を残す。
  await editor.fill("編集中 {{question}} {{images}}");
  content = "保存版 C {{question}} {{images}}";
  const refetched = page.waitForResponse("**/api/settings/docrag-prompts**");
  await refetchOnFocus(page);
  await refetched;
  await expect(editor).toHaveValue("編集中 {{question}} {{images}}");
});
