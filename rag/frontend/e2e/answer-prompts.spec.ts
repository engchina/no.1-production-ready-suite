import { expect, test, type Page } from "./fixtures/test";
import { expectNoPageOverflow, mockLocalAuth, openSidebarNav } from "./_helpers";

// 回答プロンプトの画面（#595）。回答を作る指示のテンプレートと、回答の各工程（読み取り専用）だけを持つ。
// 以前の system prompt の版（作成・有効化）は削除した。

test.beforeEach(async ({ page }) => {
  await mockLocalAuth(page);
});

/** TanStack Query の window focus 再取得を起こす(タブへ戻った操作に相当)。 */
async function refetchOnFocus(page: Page) {
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 760, collapse: false },
  { name: "mobile", width: 375, height: 812, collapse: true },
]) {
  test(`回答プロンプトの画面はテンプレートだけを出し、版の一覧と作成は無い (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    await page.route("**/api/settings/answer-prompts**", (route) =>
      route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{answer_context}}", false) })
    );

    await page.goto("/settings/prompts");

    await expect(page.getByRole("heading", { name: "回答プロンプト", exact: true, level: 1 })).toBeVisible();
    await expect(page.getByText("回答生成のプロンプト", { exact: true })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "版を作成" })).toHaveCount(0);
    await expect(page.getByTestId("prompt-version-list")).toHaveCount(0);
    await expect(page.getByTestId("answer-prompt-unused-note")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText("DocRAG");
    // 375px ではナビがドロワー（#367）。開いて現在地を確かめる。
    await expect((await openSidebarNav(page)).getByRole("link", { name: "回答プロンプト" })).toHaveAttribute(
      "aria-current",
      "page"
    );
    await expectNoPageOverflow(page);
  });

  test(`回答プロンプトの読み込み中は経過時間と形の Skeleton を出す (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (viewport.collapse) await collapseSidebar(page);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      await gate;
      await route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{answer_context}}", false) });
    });

    await page.goto("/settings/prompts");

    const loading = page.getByTestId("settings-prompts-loading");
    await expect(loading).toBeVisible();
    release();
    await expect(loading).toHaveCount(0);
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeVisible();
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`回答生成のプロンプトを検証・保存・既定に戻せて、各段は Markdown 原文で見られる (${viewport.name})`, { tag: "@responsive" }, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const requests: { method: string; body: unknown }[] = [];
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      const method = route.request().method();
      if (method === "GET") {
        await route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{answer_context}}", false) });
        return;
      }
      const body = method === "PUT" ? route.request().postDataJSON() : null;
      requests.push({ method, body });
      if (method === "PUT" && !String((body as { content: string }).content).includes("{{answer_context}}")) {
        await route.fulfill({
          status: 422,
          json: { data: null, error_messages: ["必須の placeholder がありません: {{answer_context}}"], warning_messages: [] },
        });
        return;
      }
      await route.fulfill({
        json:
          method === "PUT"
            ? answerPromptsEnvelope((body as { content: string }).content, true)
            : answerPromptsEnvelope("既定のテンプレート {{question}} {{answer_context}}", false),
      });
    });

    await page.goto("/settings/prompts");

    const field = page.getByLabel("テンプレート");
    const save = page.getByRole("button", { name: "プロンプトを保存" });
    await expect(page.getByText("既定値", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "既定に戻す" })).toBeDisabled();
    await field.fill("質問だけ {{question}}");
    await save.click();
    // 必須の placeholder の欠けは送る前に欄の下へ出し、欄へフォーカスを移す（#1010）。
    await expect(page.getByText("必須の placeholder がありません: {{answer_context}}")).toBeVisible();
    await expect(field).toBeFocused();

    await field.fill("独自 {{question}} {{answer_context}}");
    await save.click();
    await expect(page.getByText("プロンプトを保存しました。")).toBeVisible();
    await expect(page.getByText(/^編集済み/)).toBeVisible();

    await page.getByRole("button", { name: "既定に戻す" }).click();
    await page.getByRole("alertdialog", { name: "既定のプロンプトに戻しますか？" }).getByRole("button", { name: "既定に戻す" }).click();
    await expect(page.getByText("既定のプロンプトに戻しました。")).toBeVisible();
    await expect(field).toHaveValue("既定のテンプレート {{question}} {{answer_context}}");
    expect(requests.map((request) => request.method)).toEqual(["PUT", "DELETE"]);

    await page.getByTestId("answer-prompt-stage-audit").locator("summary").click();
    const audit = page.getByTestId("answer-prompt-stage-audit");
    const source = audit.getByRole("textbox");
    await expect(source).toHaveValue(DEFAULT_STAGES.find((stage) => stage.id === "audit")!.prompts[0].content);
    await expect(source).toHaveAttribute("readonly", "");
    await expect(source).toBeEnabled();
    await expect(source).toHaveClass(/font-mono/);
    await source.focus();
    await source.evaluate((element: HTMLTextAreaElement) => element.select());
    expect(await source.evaluate((element: HTMLTextAreaElement) =>
      element.value.slice(element.selectionStart, element.selectionEnd)
    )).toContain("## 手順");
    await expect(audit.locator("h6, ol, code, script")).toHaveCount(0);
    expect(await page.evaluate(() => "promptInjected" in window)).toBe(false);
    await audit.screenshot({ path: testInfo.outputPath("markdown-prompt.png") });
    await expectNoPageOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`各段のプロンプトは処理の順の区分の見出しの下に並び、閉じたまま使われる条件が分かる（#2054, ${viewport.name}）`, { tag: "@responsive" }, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.route("**/api/settings/answer-prompts**", (route) =>
      route.fulfill({ json: answerPromptsEnvelope("既定のテンプレート {{question}} {{answer_context}}", false, FLOW_STAGES) })
    );

    await page.goto("/settings/prompts");

    const stages = page.getByRole("region", { name: "処理の各段のプロンプト" });
    await expect(stages).toBeVisible();
    // 取込は解析と索引に分け、独立した準備の後に検索・回答・評価を並べる。
    await expect(stages.getByRole("heading", { level: 3 })).toHaveText([
      "文書解析",
      "文書分割・索引",
      "業務ガイドの準備",
      "質問の解釈",
      "検索",
      "根拠の判定",
      "回答の生成",
      "監査",
      "評価",
    ]);
    const editor = stages.getByRole("textbox", { name: "テンプレート", exact: true });
    await expect(editor).toHaveCount(1);
    await expect(stages.getByRole("region", { name: "回答の生成" }).getByRole("textbox", { name: "テンプレート", exact: true })).toBeEditable();
    expect(await stages.getByTestId("answer-prompt-stage-generation").evaluate((element) =>
      Boolean(element.compareDocumentPosition(document.getElementById("answer-prompt-vlm_answer")!) & Node.DOCUMENT_POSITION_FOLLOWING)
    )).toBe(true);
    await expect(stages.getByTestId("answer-prompt-stage-section_extraction")).toContainText("標準 RAG と CRAG の両方");
    await expect(stages.getByTestId("answer-prompt-stage-section_extraction")).toContainText("原文抽出は既定で無効");
    await expect(stages.getByTestId("answer-prompt-stage-audit")).toContainText("生成後の LLM 監査を有効にしたとき（既定は無効）");
    await expect(stages.getByTestId("answer-prompt-stage-application")).toContainText("生成後の LLM 監査が有効で");
    await expect(stages.getByTestId("answer-prompt-stage-history_rewrite")).toContainText("書き換えを有効にしたとき");
    await expect(stages.getByTestId("answer-prompt-stage-faq_answer")).toContainText("検索しない");
    await expectNoPageOverflow(page);
    await stages.getByRole("heading", { name: "文書解析", exact: true }).scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("pipeline-prompt-order.png") });
    await editor.scrollIntoViewIfNeeded();
    await page.screenshot({ path: testInfo.outputPath("generation-template-position.png") });
    // 段は区分の中に入り、開かなくても使われる条件を 1 行で示す。
    const ingestion = stages.getByRole("region", { name: "文書解析" });
    const headingLevels = ingestion.getByTestId("answer-prompt-stage-heading_levels");
    await expect(headingLevels).toContainText("Docling の解析結果で、しおりの階層を使えず");
    await expect(headingLevels).toContainText("階層が未判定の見出しの割合と見出し数が設定の条件を満たすとき");
    await expect(headingLevels).toContainText("文書ごと。再試行あり");
    await expect(ingestion.getByTestId("answer-prompt-stage-field_extraction")).toContainText(
      "本文を分けて送る部分ごと"
    );
    const picture = ingestion.getByTestId("answer-prompt-stage-picture_description");
    await expect(picture).toContainText("図・画像の説明");
    await expect(picture).toContainText("使われる条件: 「図・画像を AI で読み取る」を有効にしたとき");
    await expect(picture.getByText("図の system")).toBeHidden();
    await expect(stages.getByRole("region", { name: "回答の生成" }).getByTestId("answer-prompt-stage-generation")).toContainText(
      "使われる条件: 検索した根拠から回答を生成するとき"
    );
    await expect(stages.getByRole("region", { name: "評価" }).getByTestId("answer-prompt-stage-evaluation")).toContainText(
      "使われる条件: 品質評価と保存した回答の評価。正確性・網羅性は標準回答や必須要点などの期待があるとき、忠実性は取得した根拠があるときに測ります"
    );

    await expect(stages.getByTestId("answer-prompt-stage-evaluation")).toContainText("回答の品質評価");
    await expect(stages.getByTestId("answer-prompt-stage-evaluation_screen_judge")).toHaveCount(0);
    await stages.getByTestId("answer-prompt-stage-evaluation").locator("summary").click();
    expect(await stages.getByTestId("answer-prompt-stage-evaluation").getByRole("textbox").evaluateAll((elements) =>
      elements.map((element) => (element as HTMLTextAreaElement).value)
    )).toEqual(["範囲の指示", "網羅性・忠実性の指示", "正確性の指示"]);
    await stages.getByTestId("answer-prompt-stage-evaluation").screenshot({ path: testInfo.outputPath("evaluation-prompt-conditions.png") });

    const conditions = stages.getByRole("region", { name: "質問の解釈" })
      .getByTestId("answer-prompt-stage-support_guide_conditions");
    await expect(conditions).toContainText("質問から業務ガイドの自由入力条件を読み取る");
    await expect(conditions).toContainText("利用者が答える未確認の自由入力条件があるとき");
    await conditions.locator("summary").click();
    await expect(conditions.getByText("条件認識の system", { exact: true })).toBeVisible();
    await expect(conditions.getByRole("textbox")).toHaveValue("条件認識の system");
    await expect(conditions.getByRole("textbox")).toHaveAttribute("readonly", "");
    await conditions.screenshot({ path: testInfo.outputPath("guide-condition-prompt.png") });

    // 開くと部分の名前と内容が見える。
    await picture.locator("summary").click();
    await expect(picture.getByText("system プロンプト", { exact: true })).toBeVisible();
    await expect(picture.getByText("図の system")).toBeVisible();
    await expect(picture.getByRole("textbox", { name: "入力テンプレート", exact: true })).toHaveValue("## 現在の図の読み取り指示");
    await expect(picture.getByText("表を読むときの対象の指示", { exact: true })).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

for (const viewport of [
  { name: "desktop", width: 1280, height: 900 },
  { name: "mobile", width: 375, height: 900 },
]) {
  test(`回答生成のプロンプトは空を送る前に止め、変更を破棄で保存値へ戻せる（#1010, ${viewport.name}）`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const requests: string[] = [];
    await page.route("**/api/settings/answer-prompts**", async (route) => {
      if (route.request().method() !== "GET") requests.push(route.request().method());
      await route.fulfill({ json: answerPromptsEnvelope("保存版 {{question}} {{answer_context}}", true) });
    });

    await page.goto("/settings/prompts");
    const field = page.getByRole("textbox", { name: "テンプレート", exact: true });
    const actions = page.getByRole("group", { name: "編集内容の操作" });
    const save = actions.getByRole("button", { name: "プロンプトを保存" });
    const discard = actions.getByRole("button", { name: "変更を破棄" });
    await expect(discard).toBeDisabled();

    await field.fill("   ");
    await expect(actions).toContainText("未保存の変更があります。");
    await save.click();
    await expect(page.getByText("テンプレートを入力してください。")).toBeVisible();
    await expect(field).toBeFocused();
    expect(requests).toEqual([]);

    await discard.click();
    await expect(field).toHaveValue("保存版 {{question}} {{answer_context}}");
    await expect(page.getByText("テンプレートを入力してください。")).toHaveCount(0);
    await expect(save).toBeDisabled();
    await expect(actions.getByText(/^編集済み/)).toBeVisible();
    await expectNoPageOverflow(page);
  });
}

test("回答生成のプロンプトは編集中の内容を背景の再取得で上書きしない", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  let content = "保存版 A {{question}} {{answer_context}}";
  await page.route("**/api/settings/answer-prompts**", (route) =>
    route.fulfill({ json: answerPromptsEnvelope(content, true) })
  );

  await page.goto("/settings/prompts");
  const editor = page.getByRole("textbox", { name: "テンプレート", exact: true });
  await expect(editor).toHaveValue(content);

  // 未編集なら保存値の変更へ追従する。初回の取得（StrictMode の再購読による取り直しを含む）が
  // まだ途中だと、フォーカスの再取得はその取得（保存版 A）にまとめられるため、B が届くまで再取得を促す（#833）。
  content = "保存版 B {{question}} {{answer_context}}";
  await expect
    .poll(async () => {
      await refetchOnFocus(page);
      return editor.inputValue();
    })
    .toBe(content);

  // 編集中は、保存値が変わっても編集内容を残す。
  await editor.fill("編集中 {{question}} {{answer_context}}");
  content = "保存版 C {{question}} {{answer_context}}";
  const refetched = page.waitForResponse("**/api/settings/answer-prompts**");
  await refetchOnFocus(page);
  await refetched;
  await expect(editor).toHaveValue("編集中 {{question}} {{answer_context}}");
});

async function collapseSidebar(page: Page) {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "production-ready-rag.ui",
      JSON.stringify({ state: { sidebarCollapsed: true }, version: 0 })
    );
  });
}

type StageFixture = { id: string; phase: string; prompts: { id: string; content: string }[] };

const DEFAULT_STAGES: StageFixture[] = [
  { id: "routing", phase: "question", prompts: [{ id: "system", content: "ルーティングの system" }] },
  { id: "generation", phase: "generation", prompts: [{ id: "system", content: "生成の system" }] },
  { id: "audit", phase: "audit", prompts: [{ id: "system", content: "監査の system\n\n## 手順\n\n1. `quote` を照合する。\n2. 不足なら指摘する。\n\n<script>window.promptInjected = true</script>" }] },
];

// 9 つの区分にまたがる段（backend の PROMPT_STAGES の一部。内容は合成）。
const FLOW_STAGES: StageFixture[] = [
  {
    id: "picture_description",
    phase: "ingestion",
    prompts: [
      { id: "system", content: "図の system" },
      { id: "template", content: "## 現在の図の読み取り指示" },
      { id: "table", content: "表の指示" },
    ],
  },
  { id: "heading_levels", phase: "ingestion", prompts: [{ id: "instruction", content: "階層の指示" }] },
  { id: "field_extraction", phase: "ingestion", prompts: [{ id: "instruction", content: "項目の指示" }] },
  { id: "raptor_summary", phase: "indexing", prompts: [{ id: "system", content: "索引要約の指示" }] },
  { id: "support_guide_draft", phase: "knowledge", prompts: [{ id: "system", content: "下書きの指示" }] },
  { id: "support_guide_conditions", phase: "question", prompts: [{ id: "system", content: "条件認識の system" }] },
  { id: "history_rewrite", phase: "question", prompts: [{ id: "system", content: "書き換えの system" }] },
  { id: "routing", phase: "question", prompts: [{ id: "system", content: "ルーティングの system" }] },
  { id: "screen_linking", phase: "retrieval", prompts: [{ id: "system", content: "画面の system" }] },
  { id: "crag", phase: "grounding", prompts: [{ id: "system", content: "判定の system" }] },
  { id: "section_extraction", phase: "grounding", prompts: [{ id: "system", content: "抽出の指示" }] },
  { id: "faq_answer", phase: "generation", prompts: [{ id: "system", content: "FAQ の指示" }] },
  { id: "generation", phase: "generation", prompts: [{ id: "system", content: "生成の system" }] },
  {
    id: "audit",
    phase: "audit",
    prompts: [
      { id: "system", content: "監査の system\n\n## 手順\n\n1. `quote` を照合する。\n2. 不足なら指摘する。\n\n<script>window.promptInjected = true</script>" },
      { id: "compactRule", content: "理由の規則" },
    ],
  },
  { id: "application", phase: "audit", prompts: [{ id: "system", content: "当てはめの指示" }] },
  { id: "evaluation", phase: "evaluation", prompts: [{ id: "scope", content: "範囲の指示" }, { id: "system", content: "網羅性・忠実性の指示" }, { id: "correctness", content: "正確性の指示" }] },
];

function answerPromptsEnvelope(content: string, customized: boolean, stages: StageFixture[] = DEFAULT_STAGES) {
  return {
    data: {
      prompts: [
        {
          key: "vlm_answer",
          content,
          default_content: "既定のテンプレート {{question}} {{answer_context}}",
          customized,
          required_placeholders: ["question", "answer_context"],
          updated_at: customized ? "2026-09-26T01:00:00Z" : null,
        },
      ],
      stages,
    },
    error_messages: [],
    warning_messages: [],
  };
}


for (const width of [1280, 375]) {
  test(`回答プロンプトの取得失敗後は再試行で工程と編集欄へ戻れる (${width}px)`, { tag: "@responsive" }, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    let failed = true;
    await page.route("**/api/settings/answer-prompts**", (route) => failed
      ? route.fulfill({ status: 503, json: { data: null, error_messages: ["取得できませんでした"], warning_messages: [] } })
      : route.fulfill({ json: answerPromptsEnvelope("既定 {{question}} {{answer_context}}", false) }));
    await page.goto("/settings/prompts");
    await expect(page.getByText("プロンプトを読み込めませんでした。", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toHaveCount(0);
    failed = false;
    await page.getByRole("button", { name: "再試行", exact: true }).click();
    await expect(page.getByRole("textbox", { name: "テンプレート", exact: true })).toBeEditable();
    await expectNoPageOverflow(page);
  });
}
