import type { Locator, Page } from "@playwright/test";

import { MOCK_NOW, expect, test, type MockApi } from "./fixtures/mock-api";

// #1866: スキルの並びは backend（`GET /api/skills`）の順を正本にする。組み込みのスキルを
// 「業務 RAG 調査 → 構造化データ照会 → 資料とデータの横断調査」の順で先頭に固定し、その下に
// 組み込みでないスキルを表示名の順で並べる。画面（スキルの一覧・業務 Agent の編集の選択）は並べ替えない。

const AGENT_ID = "agent-1866";

const BUILTIN_IDS = [
  "business-rag-research",
  "structured-data-query",
  "rag-and-structured-data",
];
const BUILTIN_NAMES = [
  "業務 RAG 調査",
  "構造化データ照会",
  "資料とデータの横断調査",
];

/** backend が返す順（組み込み → 組み込みでないスキルの表示名の順）で、実行時のスキルを足す。 */
function seedRuntimeSkills(mockApi: MockApi) {
  for (const [id, name] of [
    // ID の文字の順なら「業務 RAG 調査」と「資料とデータの横断調査」の間に入る。
    ["github_repo_research", "GitHub リポジトリ調査（テスト）"],
    ["accounting_1866", "経理の手順"],
  ]) {
    mockApi.state.skills.push({
      id,
      name,
      description: `${name}の説明`,
      instructions: "",
      mcp_requirements: [],
      references: [],
      enabled: true,
      tags: [],
      source: "runtime",
      created_at: MOCK_NOW,
      updated_at: MOCK_NOW,
    });
  }
}

function seedAgent(mockApi: MockApi) {
  mockApi.state.agents.push({
    id: AGENT_ID,
    name: "並びの確認の Agent",
    description: "",
    instructions: "指示",
    skill_ids: [],
    model_id: "",
    migration_required: false,
    tool_names: [],
    enabled: true,
    source: "runtime",
    versioned: true,
    versions: [],
    published_version: null,
    unpublished_changes: true,
    created_at: MOCK_NOW,
    updated_at: MOCK_NOW,
  });
}

/** スキルの一覧の行の ID（表示の順）。 */
async function listedSkillIds(page: Page) {
  return page
    .locator('tr[data-testid^="skill-row-"]')
    .evaluateAll((rows) =>
      rows.map((row) =>
        row.getAttribute("data-testid")?.replace("skill-row-", ""),
      ),
    );
}

/** ListPicker の候補の名前（表示の順）。 */
async function optionNames(picker: Locator) {
  return picker.locator('[role="option"] [id$="-label"]').allTextContents();
}

test.describe("スキルの並び", { tag: "@responsive" }, () => {
  test("スキルの一覧は組み込みを決まった順で先頭に、その下に組み込みでないスキルを並べる", async ({
    page,
    mockApi,
  }) => {
    seedRuntimeSkills(mockApi);
    await page.goto("/skills");

    await expect(page.locator('tr[data-testid^="skill-row-"]')).toHaveCount(5);
    expect(await listedSkillIds(page)).toEqual([
      ...BUILTIN_IDS,
      "github_repo_research",
      "accounting_1866",
    ]);

    // 絞り込んでも同じ順。
    await page.getByRole("searchbox").fill("調査");
    await expect(page.locator('tr[data-testid^="skill-row-"]')).toHaveCount(3);
    expect(await listedSkillIds(page)).toEqual([
      "business-rag-research",
      "rag-and-structured-data",
      "github_repo_research",
    ]);
  });

  test("業務 Agent の編集のスキルの選択も同じ順で、絞り込み・選択中だけ表示でも変わらない", async ({
    page,
    mockApi,
  }) => {
    seedRuntimeSkills(mockApi);
    seedAgent(mockApi);
    await page.goto(`/agents?id=${AGENT_ID}`);
    await expect(
      page.getByRole("heading", { name: "並びの確認の Agent", level: 1 }),
    ).toBeVisible();

    const picker = page.getByTestId("agent-skill-picker");
    await expect(picker.getByRole("option")).toHaveCount(5);
    expect(await optionNames(picker)).toEqual([
      ...BUILTIN_NAMES,
      "GitHub リポジトリ調査（テスト）",
      "経理の手順",
    ]);

    // 選ぶ順を逆にしても、選択中だけ表示は一覧の順。
    for (const name of [
      "経理の手順",
      "資料とデータの横断調査",
      "業務 RAG 調査",
    ]) {
      await picker.getByRole("option", { name }).click();
    }
    await picker.getByTestId("agent-skill-picker-show-selected").click();
    await expect(picker.getByRole("option")).toHaveCount(3);
    expect(await optionNames(picker)).toEqual([
      "業務 RAG 調査",
      "資料とデータの横断調査",
      "経理の手順",
    ]);
    await picker.getByTestId("agent-skill-picker-show-selected").click();

    // 絞り込んでも同じ順。
    await picker.getByRole("searchbox").fill("調査");
    await expect(picker.getByRole("option")).toHaveCount(3);
    expect(await optionNames(picker)).toEqual([
      "業務 RAG 調査",
      "資料とデータの横断調査",
      "GitHub リポジトリ調査（テスト）",
    ]);
  });
});
