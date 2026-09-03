import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";
import { dropFiles } from "./_helpers/file-dropzone";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
}

function createRequestGate() {
  let release: () => void = () => undefined;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

async function expectButtonLabelFits(button: Locator) {
  const label = button.locator("span").last();
  await expect(label).toBeVisible();
  const metrics = await label.evaluate((element) => {
    const buttonElement = element.closest("button");
    if (!(buttonElement instanceof HTMLButtonElement)) {
      throw new Error("Button label is not inside a button.");
    }
    const labelRect = element.getBoundingClientRect();
    const buttonRect = buttonElement.getBoundingClientRect();
    const style = window.getComputedStyle(element);
    return {
      buttonBottom: buttonRect.bottom,
      buttonTop: buttonRect.top,
      fontSize: Number.parseFloat(style.fontSize),
      labelBottom: labelRect.bottom,
      labelTop: labelRect.top,
      lineHeight: Number.parseFloat(style.lineHeight),
    };
  });
  expect(metrics.lineHeight).toBeGreaterThan(metrics.fontSize);
  expect(metrics.labelTop).toBeGreaterThanOrEqual(metrics.buttonTop - 0.5);
  expect(metrics.labelBottom).toBeLessThanOrEqual(metrics.buttonBottom + 0.5);
}

async function expectBuildPanelsStackedFullWidth(page: Page) {
  const section = page.getByTestId("profile-ontology-build");
  const setupPanel = page.getByTestId("ontology-build-setup-panel");
  const reviewPanel = page.getByTestId("ontology-build-review-panel");
  await expect(page.getByTestId("fixed-split-pane-ontology-build-workspace")).toHaveCount(0);
  await expect(setupPanel).toBeVisible();
  await expect(reviewPanel).toBeVisible();
  const [sectionBox, setupBox, reviewBox] = await Promise.all([
    section.boundingBox(),
    setupPanel.boundingBox(),
    reviewPanel.boundingBox(),
  ]);
  expect(sectionBox).not.toBeNull();
  expect(setupBox).not.toBeNull();
  expect(reviewBox).not.toBeNull();
  expect(reviewBox!.y).toBeGreaterThan(setupBox!.y + setupBox!.height - 1);
  expect(Math.abs(setupBox!.x - reviewBox!.x)).toBeLessThanOrEqual(1);
  expect(setupBox!.x).toBeGreaterThanOrEqual(sectionBox!.x);
  expect(reviewBox!.x).toBeGreaterThanOrEqual(sectionBox!.x);
  expect(setupBox!.x + setupBox!.width).toBeLessThanOrEqual(
    sectionBox!.x + sectionBox!.width + 1
  );
  expect(reviewBox!.x + reviewBox!.width).toBeLessThanOrEqual(
    sectionBox!.x + sectionBox!.width + 1
  );
  expect(setupBox!.width).toBeGreaterThanOrEqual(sectionBox!.width - 40);
  expect(reviewBox!.width).toBeGreaterThanOrEqual(sectionBox!.width - 40);
}

async function expectSourceDropzoneMatchesQaStyle(page: Page) {
  const sourcePanel = page.getByTestId("ontology-build-source-panel");
  await expect(sourcePanel).toHaveClass(/grid min-w-0 gap-2/);
  await expect(sourcePanel).not.toHaveClass(/rounded-md/);
  await expect(sourcePanel).not.toHaveClass(/border/);

  const [sourceRootClass, qaRootClass] = await Promise.all([
    page.getByTestId("ontology-build-source-files").getAttribute("class"),
    page.getByTestId("ontology-build-qa-file").getAttribute("class"),
  ]);
  expect(sourceRootClass).not.toBeNull();
  expect(sourceRootClass).toBe(qaRootClass);

  await expect(page.getByTestId("ontology-build-source-files-input")).toHaveAttribute(
    "aria-required",
    "false"
  );
  await expect(page.locator("label").filter({ hasText: /^構築資料$/ })).toHaveCount(1);
}

type BuildRunOptions = {
  runSchemaNaming: boolean | null;
  runQaExtraction: boolean | null;
  runTextExtraction: boolean | null;
};

function multipartFieldValue(postData: string, fieldName: string): string | null {
  const marker = `name="${fieldName}"`;
  const markerIndex = postData.indexOf(marker);
  if (markerIndex < 0) return null;
  const valueStart = postData.indexOf("\r\n\r\n", markerIndex);
  if (valueStart < 0) return null;
  const rest = postData.slice(valueStart + 4);
  const valueEnd = rest.indexOf("\r\n");
  return (valueEnd < 0 ? rest : rest.slice(0, valueEnd)).trim();
}

function multipartBooleanValue(postData: string, fieldName: string): boolean | null {
  const value = multipartFieldValue(postData, fieldName);
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

async function expectExtractionTargetsHidden(page: Page) {
  await expect(page.getByText("実行する抽出", { exact: true })).toHaveCount(0);
  await expect(page.getByText("業務エンティティ命名・説明", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Q/A からの関係・指標抽出", { exact: true })).toHaveCount(0);
  await expect(page.getByText("業務説明からの補強", { exact: true })).toHaveCount(0);
}

const profiles = [
  {
    id: "default",
    name: "既定プロファイル",
    category: "既定",
    description: "AI 構築の確認",
    allowed_tables: ["ORDERS", "CUSTOMERS"],
    allowed_views: [],
    glossary: {},
    sql_rules: [],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    select_ai_config: null,
    archived: false,
  },
];

const ontologyView = {
  profile_ontology_view: {
    id: "profile-view:default",
    profile_id: "default",
    ontology_revision_id: "revision-1",
    etag: "view-etag",
    node_ids: ["table:APP:ORDERS", "table:APP:CUSTOMERS"],
    edge_ids: ["fk:orders-customers"],
    allowed_path_ids: [],
  },
  ontology_graph: {
    revision: {
      id: "revision-1",
      version: 3,
      status: "published",
      schema_fingerprint: "fp",
      etag: "rev-etag",
    },
    nodes: [
      {
        id: "table:APP:ORDERS",
        kind: "table",
        business_name_ja: "受注",
        review_status: "approved",
        physical_mappings: [
          { object_ref: { owner: "APP", object_name: "ORDERS", object_type: "table" } },
        ],
      },
      {
        id: "table:APP:CUSTOMERS",
        kind: "table",
        business_name_ja: "顧客",
        review_status: "approved",
        physical_mappings: [
          { object_ref: { owner: "APP", object_name: "CUSTOMERS", object_type: "table" } },
        ],
      },
    ],
    edges: [
      {
        id: "fk:orders-customers",
        kind: "foreign_key",
        source_node_id: "table:APP:ORDERS",
        target_node_id: "table:APP:CUSTOMERS",
        relationship_name_ja: "顧客を参照",
        cardinality: "many_to_one",
        review_status: "approved",
      },
    ],
  },
};

const generatedDraftMarkdown = [
  "# Ontology Draft",
  "",
  "## Input Summary",
  "- Profile: `default`",
  "- DB schema objects: 2",
  "",
  "## Physical Objects",
  "- `APP.ORDERS` (table)",
  "  - business_name: 受注",
  "  - usage: 確定済み受注の売上分析に使用",
  "- `APP.CUSTOMERS` (table)",
  "  - business_name: 顧客",
  "",
  "## Entities",
  "- 受注 (`APP.ORDERS`)",
  "",
  "## Relationships / Join",
  "- 受注 (`APP.ORDERS`) -> 顧客 (`APP.CUSTOMERS`): 顧客を参照",
  "  - allowed_path: true",
  "  - join_conditions:",
  "    - `APP.ORDERS.CUSTOMER_ID = APP.CUSTOMERS.ID`",
  "",
  "## Metrics",
  "- 受注金額合計",
  "",
  "## Business Rules / Enum Values",
  "- なし",
  "",
  "## Synonyms",
  "- target: `APP.ORDERS`",
  "",
  "## Evidence / Warnings",
  "- 命名候補 APP.SECRET を profile 範囲内に解決できません。",
].join("\n");

function markdownDraftPayload(markdown: string, etag = "markdown-etag-1") {
  return {
    draft_markdown: markdown,
    published_markdown: "",
    draft_revision: {
      id: "revision-draft-4",
      version: 4,
      status: "draft",
      schema_fingerprint: "fp",
      etag: "draft-etag-4",
    },
    published_revision: null,
    draft_etag: etag,
    published_at: null,
  };
}

function emptyMarkdownPayload() {
  return {
    draft_markdown: "",
    published_markdown: "",
    draft_revision: null,
    published_revision: null,
    draft_etag: "",
    published_at: null,
  };
}

function buildJob(status: string, stepStatus: string, proposalIds: string[] = []) {
  const stepTimes =
    stepStatus === "pending"
      ? {}
      : {
          started_at: "2026-07-12T00:00:01Z",
          finished_at: stepStatus === "running" ? null : "2026-07-12T00:00:05Z",
        };
  const events =
    stepStatus === "pending"
      ? []
      : [
          { at: "2026-07-12T00:00:01Z", message_ja: "AI オントロジー構築を開始しました。" },
          {
            at: "2026-07-12T00:00:02Z",
            message_ja: "スキーマ情報を準備しました(表・ビュー 2 件、列 5 件)。",
          },
          ...(status === "succeeded"
            ? [
                {
                  at: "2026-07-12T00:00:08Z",
                  message_ja: "Markdown Draft v4 を生成しました(候補 2 件、警告 1 件)。",
                },
                {
                  at: "2026-07-12T00:00:09Z",
                  message_ja: "構築が完了しました(Markdown Draft v4、警告 1 件)。",
                },
              ]
            : []),
        ];
  return {
    job: {
      id: "job-1",
      profile_id: "default",
      status,
      steps: [
        {
          name: "schema_context",
          status: stepStatus,
          detail_ja: "表・ビュー 2 件、列 5 件",
          ...stepTimes,
        },
        { name: "schema_naming", status: stepStatus, detail_ja: "", ...stepTimes },
        { name: "text_extraction", status: stepStatus, detail_ja: "", ...stepTimes },
        {
          name: "proposal_registration",
          status: stepStatus,
          detail_ja:
            stepStatus === "succeeded"
              ? "Markdown Draft v4 を生成しました(候補 2 件、警告 1 件)。"
              : "",
          ...stepTimes,
        },
      ],
      events,
      proposal_ids: proposalIds,
      draft_revision_id: status === "succeeded" ? "revision-draft-4" : "",
      draft_etag: status === "succeeded" ? "markdown-etag-1" : "",
      markdown_output: status === "succeeded" ? generatedDraftMarkdown : "",
      warnings_ja: status === "succeeded" ? ["命名候補 APP.SECRET を profile 範囲内に解決できません。"] : [],
      error_message_ja: "",
      created_at: "2026-07-12T00:00:00Z",
      started_at: stepStatus === "pending" ? null : "2026-07-12T00:00:01Z",
      finished_at: status === "succeeded" ? "2026-07-12T00:00:10Z" : null,
    },
  };
}

const proposalsPending = [
  {
    id: "proposal-1",
    session_id: "ontology_build:job-1",
    profile_id: "default",
    base_revision_id: "revision-1",
    title_ja: "業務エンティティ命名: 受注",
    description_ja: "APP.ORDERS の業務名候補",
    kind: "mapping",
    status: "submitted",
    proposal_payload: { kind: "mapping", values: {} },
    created_at: "2026-07-12T00:00:10Z",
  },
  {
    id: "proposal-2",
    session_id: "ontology_build:job-1",
    profile_id: "default",
    base_revision_id: "revision-1",
    title_ja: "業務関係の提案: 顧客を参照",
    description_ja: "Q/A の JOIN 句",
    kind: "relationship",
    status: "submitted",
    proposal_payload: { kind: "relationship", values: {} },
    created_at: "2026-07-12T00:00:10Z",
  },
];

async function mockApi(page: Page) {
  const state = {
    jobPolls: 0,
    accepted: new Set<string>(),
    published: false,
    publishPolls: 0,
    startPayloadSeen: false,
    startCalls: 0,
    idempotencySeen: false,
    sourceFilesSeen: false,
    qaFileSeen: false,
    latestBusinessText: null as string | null,
    schemaRefreshCalls: 0,
    latestRunOptions: null as BuildRunOptions | null,
    ontologyDraftPayload: null as Record<string, unknown> | null,
    ontologyViewCalls: 0,
    draftMarkdown: generatedDraftMarkdown,
    draftMarkdownEtag: "markdown-etag-1",
    savedDraftMarkdown: null as string | null,
    publishedMarkdown: "",
  };
  const currentOntologyViewPayload = () => {
    const revision = state.published
      ? {
          id: "revision-draft-4",
          version: 4,
          status: "published",
          schema_fingerprint: "fp",
          etag: "draft-etag-4",
        }
      : ontologyView.ontology_graph.revision;
    return {
      ...ontologyView,
      profile_ontology_view: {
        ...ontologyView.profile_ontology_view,
        ontology_revision_id: revision.id,
      },
      ontology_graph: {
        ...ontologyView.ontology_graph,
        revision,
      },
    };
  };
  const markdownStatePayload = () => {
    const hasDraft = state.jobPolls >= 2;
    return {
      draft_markdown: hasDraft ? state.draftMarkdown : "",
      published_markdown: state.published ? state.publishedMarkdown || state.draftMarkdown : "",
      draft_revision: hasDraft
        ? {
            id: "revision-draft-4",
            version: 4,
            status: "draft",
            schema_fingerprint: "fp",
            etag: "draft-etag-4",
          }
        : null,
      published_revision: state.published
        ? {
            id: "revision-draft-4",
            version: 4,
            status: "published",
            schema_fingerprint: "fp",
            etag: "draft-etag-4",
            published_at: "2026-07-12T00:00:20Z",
          }
        : null,
      draft_etag: hasDraft ? state.draftMarkdownEtag : "",
      published_at: state.published ? "2026-07-12T00:00:20Z" : null,
    };
  };
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, { refreshed_at: "2026-07-12T00:00:00Z", tables: [] })
  );
  await page.route("**/api/schema/refresh-jobs", (route) => {
    state.schemaRefreshCalls += 1;
    return fulfillJson(route, {
      job_id: "ontology-build-schema-refresh-done",
      status: "done",
      created_at: "2026-07-12T00:00:00Z",
      scanned_objects: 2,
      changed_objects: 1,
      deleted_objects: 0,
      catalog_version: 2,
      error_code: "",
    });
  });
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles**", (route) =>
    fulfillJson(route, { runtime: "deterministic", profiles: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profiles));
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: profiles.map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: profile.archived,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: Object.keys(profile.glossary).length,
        few_shot_count: profile.few_shot_examples.length,
        version: 1,
        etag: `etag-${profile.id}`,
        updated_at: "2026-07-12T00:00:00Z",
      })),
      next_cursor: null,
      total: profiles.length,
      change_token: 1,
    })
  );
  await page.route(/\/api\/nl2sql\/profiles\/[^/?]+$/, (route) =>
    fulfillJson(route, profiles[0])
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) => {
    state.ontologyViewCalls += 1;
    if (route.request().method() === "PATCH") {
      state.ontologyDraftPayload = route.request().postDataJSON() as Record<string, unknown>;
    }
    return fulfillJson(route, {
      ...currentOntologyViewPayload(),
      materialized: true,
      stale: false,
    });
  });
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfillJson(route, {
      revisions: [ontologyView.ontology_graph.revision],
      active_revision_id: ontologyView.ontology_graph.revision.id,
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [] })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-source-documents**", (route) =>
    fulfillJson(route, { source_documents: [] })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown/draft", (route) => {
    const body = route.request().postDataJSON() as { markdown?: string; base_etag?: string };
    state.savedDraftMarkdown = body.markdown ?? "";
    state.draftMarkdown = state.savedDraftMarkdown;
    state.draftMarkdownEtag = "markdown-etag-2";
    return fulfillJson(route, markdownStatePayload());
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, markdownStatePayload())
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-build", async (route) => {
    state.startPayloadSeen = true;
    state.startCalls += 1;
    state.idempotencySeen = Boolean(route.request().headers()["idempotency-key"]);
    const postData = route.request().postData() ?? "";
    state.latestBusinessText = multipartFieldValue(postData, "business_text");
    state.latestRunOptions = {
      runSchemaNaming: multipartBooleanValue(postData, "run_schema_naming"),
      runQaExtraction: multipartBooleanValue(postData, "run_qa_extraction"),
      runTextExtraction: multipartBooleanValue(postData, "run_text_extraction"),
    };
    state.sourceFilesSeen = postData.includes("rules.md") && postData.includes("terms.csv");
    state.qaFileSeen = postData.includes("qa_cases.csv");
    await fulfillJson(route, buildJob("queued", "pending"));
  });
  await page.route("**/api/nl2sql/ontology-build/*", async (route) => {
    state.jobPolls += 1;
    if (state.jobPolls < 2) {
      await fulfillJson(route, buildJob("running", "running"));
      return;
    }
    await fulfillJson(route, buildJob("succeeded", "succeeded", ["proposal-1", "proposal-2"]));
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", (route) =>
    fulfillJson(route, {
      // job 完了までは提案なし(初期表示は空状態)
      proposals:
        state.jobPolls < 2
          ? []
          : proposalsPending.map((proposal) =>
              state.accepted.has(proposal.id) ? { ...proposal, status: "accepted" } : proposal
            ),
    })
  );
  await page.route("**/api/nl2sql/ontology/proposals/batch-accept", async (route) => {
    const body = route.request().postDataJSON() as { proposal_ids?: string[] };
    (body.proposal_ids ?? []).forEach((id) => state.accepted.add(id));
    await fulfillJson(route, {
      proposals: proposalsPending.map((proposal) =>
        state.accepted.has(proposal.id) ? { ...proposal, status: "accepted" } : proposal
      ),
      draft: {
        revision: {
          id: "revision-draft-4",
          version: 4,
          status: "draft",
          schema_fingerprint: "fp",
          etag: "draft-etag-4",
        },
        nodes: [],
        edges: [],
      },
    });
  });
  await page.route("**/api/nl2sql/ontology/proposals/*/accept", async (route) => {
    const url = route.request().url();
    const proposalId = url.split("/proposals/")[1]?.split("/")[0] ?? "";
    state.accepted.add(proposalId);
    await fulfillJson(route, {
      proposal: {
        ...proposalsPending.find((proposal) => proposal.id === proposalId),
        status: "accepted",
      },
      draft: {
        revision: {
          id: "revision-draft-4",
          version: 4,
          status: "draft",
          schema_fingerprint: "fp",
          etag: "draft-etag-4",
        },
        nodes: [],
        edges: [],
      },
    });
  });
  await page.route("**/api/nl2sql/ontology/revisions/*/publish", async (route) => {
    state.published = true;
    state.publishedMarkdown = state.draftMarkdown;
    await fulfillJson(route, {
      job: {
        id: "publish-job-1",
        revision_id: "revision-draft-4",
        requested_etag: "draft-etag-4",
        status: "queued",
      },
    });
  });
  await page.route("**/api/nl2sql/ontology-publish/*", async (route) => {
    state.publishPolls += 1;
    await fulfillJson(route, {
      job: {
        id: "publish-job-1",
        revision_id: "revision-draft-4",
        requested_etag: "draft-etag-4",
        status: "succeeded",
        rdf_graph_name: "ONT_0123456789ABCDEF",
        inferred_graph_name: "INF_0123456789ABCDEF",
        shacl_conforms: true,
      },
    });
  });
  return state;
}

test("AI オントロジー構築の実行 → 進捗 → Markdown Draft 編集 → 公開の導線が機能する", async ({ context, page }, testInfo) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: `http://127.0.0.1:${process.env.PLAYWRIGHT_PORT ?? "3101"}`,
  });
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await expect(section.getByRole("heading", { name: "オントロジー構築" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Ontology view を/ })).toHaveCount(0);
  await expect(page.getByText("オントロジー連携", { exact: true })).toHaveCount(0);
  await expect(page.getByText("業種テンプレート", { exact: true })).toHaveCount(0);
  await expect(page.getByText("OWL(RDF)エクスポート / インポート", { exact: true })).toHaveCount(
    0
  );
  await expect(page.getByText("60 分以上かかることがあります", { exact: false })).toBeVisible();
  await expectBuildPanelsStackedFullWidth(page);
  await expectSourceDropzoneMatchesQaStyle(page);
  await expectExtractionTargetsHidden(page);

  // 初期状態では Draft / Published とも空
  await expect(section.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
  await section.getByRole("tab", { name: "Markdown Ontology Published" }).click();
  await expect(section.getByText("公開済み Markdown はまだありません。")).toBeVisible();
  await section.getByRole("tab", { name: "Markdown Ontology Draft" }).click();
  await expect(page.getByTestId("ontology-build-source-files-input")).toHaveAttribute(
    "accept",
    ".pdf,.docx,.txt,.md,.csv,.xlsx,.xls,.xlsm"
  );
  await expect(
    page
      .getByTestId("ontology-build-source-files")
      .getByText(".PDF / .DOCX / .TXT / .MD / .CSV / .XLSX / .XLS / .XLSM", {
        exact: true,
      })
  ).toBeVisible();
  await expect(
    page.getByText(
      "PDF / DOCX / TXT / MD / CSV / XLSX / XLS / XLSM を最大 5 件まで選択できます。原本と証拠位置を保持します。",
      { exact: true }
    )
  ).toBeVisible();
  await expect(page.getByTestId("ontology-build-qa-file-input")).toHaveAttribute(
    "accept",
    ".csv,.xlsx,.xls,.xlsm"
  );
  await expect(
    page.getByText("CSV / XLSX / XLS / XLSM(QUESTION, SQL 列)", { exact: true })
  ).toBeVisible();

  // 実行 → ステップ進捗 → 完了
  await section
    .getByLabel("業務説明(自然言語)")
    .fill("受注は顧客に紐づく。売上は受注金額の合計。");
  const sourceClear = page
    .getByTestId("ontology-build-source-files")
    .getByRole("button", { name: "クリア" });
  const qaClear = page
    .getByTestId("ontology-build-qa-file")
    .getByRole("button", { name: "クリア" });
  await expect(sourceClear).toBeDisabled();
  await expect(qaClear).toBeDisabled();
  await dropFiles(
    page,
    page.getByTestId("ontology-build-source-files-dropzone"),
    Array.from({ length: 6 }, (_, index) => ({
      name: `source-${index + 1}.md`,
      type: "text/markdown",
      content: `# 資料 ${index + 1}`,
      lastModified: 1_700_000_010_000 + index,
    }))
  );
  await expect(
    page.getByText(
      "構築資料は最大 5 件までアップロードできます。ファイルを減らして再度選択してください。",
      { exact: true }
    )
  ).toBeVisible();
  await expect(section.getByRole("list", { name: "選択した構築資料" })).toHaveCount(0);
  await expect(sourceClear).toBeDisabled();
  await dropFiles(page, page.getByTestId("ontology-build-source-files-dropzone"), [
    {
      name: "rules.md",
      type: "text/markdown",
      content: "# 受注ルール",
      lastModified: 1_700_000_000_000,
    },
    {
      name: "terms.csv",
      type: "text/csv",
      content: "用語,説明\n受注,顧客からの注文",
      lastModified: 1_700_000_001_000,
    },
  ]);
  await expect(sourceClear).toBeEnabled();
  await sourceClear.click();
  await expect(section.getByRole("list", { name: "選択した構築資料" })).toHaveCount(0);
  await expect(sourceClear).toBeDisabled();
  await dropFiles(page, page.getByTestId("ontology-build-source-files-dropzone"), [
    {
      name: "rules.md",
      type: "text/markdown",
      content: "# 受注ルール",
      lastModified: 1_700_000_000_000,
    },
    {
      name: "terms.csv",
      type: "text/csv",
      content: "用語,説明\n受注,顧客からの注文",
      lastModified: 1_700_000_001_000,
    },
  ]);
  await dropFiles(page, page.getByTestId("ontology-build-source-files-dropzone"), [
    {
      name: "rules.md",
      type: "text/markdown",
      content: "# 受注ルール",
      lastModified: 1_700_000_000_000,
    },
  ]);
  await dropFiles(page, page.getByTestId("ontology-build-qa-file-dropzone"), [
    {
      name: "qa_cases.csv",
      type: "text/csv",
      content: "QUESTION,SQL\n受注件数は,SELECT COUNT(*) FROM ORDERS",
    },
  ]);
  const sourceFileList = section.getByRole("list", { name: "選択した構築資料" });
  await expect(sourceFileList.getByText("rules.md", { exact: true })).toHaveCount(1);
  await expect(sourceFileList.getByText("terms.csv", { exact: true })).toBeVisible();
  await section.getByRole("button", { name: "terms.csv を資料一覧から削除" }).click();
  await expect(sourceFileList.getByText("terms.csv", { exact: true })).toHaveCount(0);
  await dropFiles(page, page.getByTestId("ontology-build-source-files-dropzone"), [
    {
      name: "terms.csv",
      type: "text/csv",
      content: "用語,説明\n受注,顧客からの注文",
      lastModified: 1_700_000_001_000,
    },
  ]);
  await expect(sourceFileList.getByText("terms.csv", { exact: true })).toBeVisible();
  await expect(section.getByText("選択済み: qa_cases.csv")).toBeVisible();
  await expect(qaClear).toBeEnabled();
  await qaClear.click();
  await expect(section.getByText("選択済み: qa_cases.csv")).toHaveCount(0);
  await expect(qaClear).toBeDisabled();
  await dropFiles(page, page.getByTestId("ontology-build-qa-file-dropzone"), [
    {
      name: "qa_cases.csv",
      type: "text/csv",
      content: "QUESTION,SQL\n受注件数は,SELECT COUNT(*) FROM ORDERS",
    },
  ]);
  await expect(section.getByText("選択済み: qa_cases.csv")).toBeVisible();
  await expect(qaClear).toBeEnabled();
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  const steps = page.getByTestId("ontology-build-steps");
  await expect(steps.getByText("スキーマ情報の準備")).toBeVisible();
  await expect(steps.getByText("業務エンティティ命名")).toBeVisible();
  // 完了は工程ステッパーの「完了」バッジ(永続)で判定する。完了の“瞬間”通知は toast のため
  // section スコープには残らない(spec §9: 完了は状態表示が担い、瞬間だけ toast)。
  await expect(steps.getByText("完了").first()).toBeVisible({ timeout: 15000 });
  expect(state.startPayloadSeen).toBe(true);
  expect(state.idempotencySeen).toBe(true);
  expect(state.latestRunOptions).toEqual({
    runSchemaNaming: true,
    runQaExtraction: true,
    runTextExtraction: true,
  });
  expect(state.sourceFilesSeen).toBe(true);
  expect(state.qaFileSeen).toBe(true);
  // 時刻付きイベントは独立ログ枠ではなく、関連する構築ステップ内の詳細ログとして表示される
  const schemaStep = page.getByTestId("ontology-build-step-schema_context");
  await expect(schemaStep.getByText("スキーマ情報を準備しました", { exact: false })).toBeVisible();
  const proposalStep = page.getByTestId("ontology-build-step-proposal_registration");
  await expect(proposalStep).toHaveAttribute("data-step-status", "succeeded");
  await expect(steps.getByText("Markdown Draft v4 を生成しました", { exact: false })).toBeVisible();
  await expect(steps.getByText("構築リクエストを受け付けました", { exact: false })).toHaveCount(0);
  await expect(steps.getByText("AI オントロジー構築を開始しました", { exact: false })).toHaveCount(0);
  await expect(steps.getByText("構築が完了しました", { exact: false })).toHaveCount(0);
  await expect(page.locator('[aria-label="構築ジョブの補足ログ"]')).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-timeline")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-history")).toHaveCount(0);
  await expect(steps.getByRole("timer")).toHaveAccessibleName(/処理時間 \d{2}:\d{2}/);
  // スコープ外候補の警告が確認できる
  await steps.locator("summary").filter({ hasText: "警告" }).click();
  await expect(steps.getByText("APP.SECRET", { exact: false })).toBeVisible();

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(
    markdown.getByTestId("ontology-build-markdown-actions").getByText("Markdown Ontology", {
      exact: true,
    })
  ).toBeVisible();
  await expect(markdown.getByRole("tab", { name: "Markdown Ontology Draft" })).toBeVisible();
  await expect(markdown.getByRole("tab", { name: "Markdown Ontology Published" })).toBeVisible();
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("未公開");
  const draftEditor = markdown.getByTestId("ontology-markdown-draft-editor");
  await expect(draftEditor).toHaveValue(/# Ontology Draft/);
  await expect(draftEditor).toHaveValue(/## Relationships \/ Join/);
  await markdown.getByRole("button", { name: "Markdown をコピー" }).click();
  await expect(page.getByText("コピーしました")).toBeVisible();

  await markdown.getByRole("tab", { name: "Markdown Ontology Published" }).click();
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "公開済み Markdown はまだありません。"
  );
  await markdown.getByRole("tab", { name: "Markdown Ontology Draft" }).click();

  // 標準図示は質問の接地確認グラフへ一本化され、公開後は同じ公開 revision へ更新される
  const ontologyQueryPanel = page.locator("#ontology-query-playground-panel");
  await expect(page.getByTestId("ontology-mermaid-panel")).toHaveCount(0);
  await expect(
    ontologyQueryPanel.getByRole("heading", { name: "質問の Ontology 接地確認用グラフ" })
  ).toBeVisible();
  await expect(ontologyQueryPanel.getByTestId("ontology-playground-revision-id")).toContainText(
    "revision-1"
  );
  const graphExpandButton = ontologyQueryPanel.getByRole("button", { name: "グラフを表示" });
  if (await graphExpandButton.isVisible()) {
    await graphExpandButton.click();
  }
  await expect(ontologyQueryPanel.getByTestId("ontology-graph-mode-physical_er")).toBeVisible();

  await draftEditor.fill(`${generatedDraftMarkdown}\n\n## Manual Notes\n- 公開確認済み`);
  await expect(markdown.getByText("未保存")).toBeVisible();
  const ontologyViewCallsBeforePublish = state.ontologyViewCalls;

  await expect(page.getByTestId("ontology-build-proposals")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /承認/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /却下/ })).toHaveCount(0);

  const publishActions = page.getByTestId("ontology-publish-actions");
  const publishButton = publishActions.getByRole("button", { name: "Ontology を公開" });
  await expectButtonLabelFits(publishButton);
  await publishButton.click();
  // 公開完了の“瞬間”は toast(document.body 直下)で通知する(spec §9)。section 外なので page スコープで確認。
  await expect(page.getByText("Ontology を公開しました。")).toBeVisible();
  expect(state.savedDraftMarkdown).toContain("Manual Notes");
  expect(state.published).toBe(true);
  await expect(page.getByTestId("ontology-publish-status")).toContainText("完了");
  await expect.poll(() => state.ontologyViewCalls).toBeGreaterThan(ontologyViewCallsBeforePublish);
  await expect(ontologyQueryPanel.getByTestId("ontology-playground-revision-id")).toContainText(
    "revision-draft-4"
  );
  await expect(page.getByTestId("ontology-mermaid-panel")).toHaveCount(0);
  await markdown.getByRole("tab", { name: "Markdown Ontology Published" }).click();
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "Manual Notes"
  );
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-published-meta")).toContainText(
    /公開日時: 07\/12 \d{2}:00/u
  );

  // 旧「物理・業務モデル編集」は Markdown Draft に統合され、別編集 UI は表示しない
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);
  await expect(page.getByText("Inspector", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Draft を保存", exact: true })).toHaveCount(0);
  await expect(markdown.getByRole("button", { name: "Markdown Draft を保存" })).toBeVisible();
  await expect(markdown.getByRole("tab")).toHaveCount(2);
  await markdown.getByRole("tab", { name: "Markdown Ontology Draft" }).click();
  await expect(draftEditor).toHaveValue(/## Physical Objects/);
  await expect(draftEditor).toHaveValue(/## Business Rules \/ Enum Values/);
  await expect(page.getByTestId("ontology-build-mermaid")).toHaveCount(0);
  await expect(section).toBeVisible();

  // 横スクロールが発生しない
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(overflow).toBe(false);

  await ontologyQueryPanel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("ontology-build.png"), fullPage: true });
});

test("オントロジー構築の処理状況は折りたたみでき、再実行で自動展開する", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await section.getByLabel("業務説明(自然言語)").fill("受注は顧客に紐づく。");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();

  const steps = page.getByTestId("ontology-build-steps");
  const toggle = page.getByTestId("ontology-build-progress-toggle");
  await expect(steps.getByText("スキーマ情報の準備")).toBeVisible();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");

  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(steps.getByText("オントロジー構築の処理状況")).toBeVisible();
  await expect(steps.getByText("スキーマ情報の準備")).toBeHidden();

  await expect(steps).toHaveAttribute("data-job-status", "succeeded", { timeout: 15000 });
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(steps.getByText("スキーマ情報の準備")).toBeVisible();
  expect(state.startCalls).toBe(2);

  await page.setViewportSize({ width: 375, height: 812 });
  const mobileOverflow = await steps.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(mobileOverflow.scrollWidth).toBeLessThanOrEqual(mobileOverflow.clientWidth + 1);
});

test("実行する抽出 UI は表示せず、入力有無から抽出対象を自動判定する", async ({ page }) => {
  test.setTimeout(60_000);
  const state = await mockApi(page);
  const expectedSchemaOnly = {
    runSchemaNaming: true,
    runQaExtraction: false,
    runTextExtraction: false,
  };

  async function openBuildForm() {
    state.latestRunOptions = null;
    state.jobPolls = 0;
    await page.goto("/ontology-build?profile=default");
    const section = page.getByTestId("profile-ontology-build");
    await expect(section.getByRole("heading", { name: "オントロジー構築" })).toBeVisible();
    await expectExtractionTargetsHidden(page);
    return section;
  }

  async function submitAndExpect(section: Locator, expected: BuildRunOptions) {
    await section.getByRole("button", { name: "AI 構築を実行" }).click();
    await expect.poll(() => state.latestRunOptions).toEqual(expected);
  }

  await test.step("Profile schema のみなら schema naming だけ実行する", async () => {
    const section = await openBuildForm();
    await submitAndExpect(section, expectedSchemaOnly);
  });

  await test.step("業務説明があれば text extraction を実行する", async () => {
    const section = await openBuildForm();
    await section.getByLabel("業務説明(自然言語)").fill("受注は顧客に紐づく。");
    await submitAndExpect(section, {
      ...expectedSchemaOnly,
      runTextExtraction: true,
    });
  });

  await test.step("構築資料があれば text extraction を実行する", async () => {
    const section = await openBuildForm();
    await dropFiles(page, page.getByTestId("ontology-build-source-files-dropzone"), [
      {
        name: "rules.md",
        type: "text/markdown",
        content: "# 受注ルール",
      },
    ]);
    await submitAndExpect(section, {
      ...expectedSchemaOnly,
      runTextExtraction: true,
    });
  });

  await test.step("Q/A ファイルがあれば QA extraction を実行する", async () => {
    const section = await openBuildForm();
    await dropFiles(page, page.getByTestId("ontology-build-qa-file-dropzone"), [
      {
        name: "qa_cases.csv",
        type: "text/csv",
        content: "QUESTION,SQL\n受注件数は,SELECT COUNT(*) FROM ORDERS",
      },
    ]);
    await submitAndExpect(section, {
      ...expectedSchemaOnly,
      runQaExtraction: true,
    });
  });

  await test.step("すべて入力されていればすべて実行する", async () => {
    const section = await openBuildForm();
    await section.getByLabel("業務説明(自然言語)").fill("売上は確定済み受注の受注金額の合計。");
    await dropFiles(page, page.getByTestId("ontology-build-source-files-dropzone"), [
      {
        name: "rules.md",
        type: "text/markdown",
        content: "# 受注ルール",
      },
    ]);
    await dropFiles(page, page.getByTestId("ontology-build-qa-file-dropzone"), [
      {
        name: "qa_cases.csv",
        type: "text/csv",
        content: "QUESTION,SQL\n売上は,SELECT SUM(AMOUNT) FROM ORDERS",
      },
    ]);
    await submitAndExpect(section, {
      runSchemaNaming: true,
      runQaExtraction: true,
      runTextExtraction: true,
    });
  });
});

test("送信直後にプレースホルダーが出て、完了後は Markdown Draft を表示する", async ({ page }) => {
  await mockApi(page);
  // POST を遅らせて「送信中」プレースホルダーを観測できるようにする
  await page.route("**/api/nl2sql/profiles/*/ontology-build", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    await fulfillJson(route, buildJob("queued", "pending"));
  });
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  const submitting = page.getByTestId("ontology-build-submitting");
  await expect(submitting).toBeVisible();
  // 送信中のスピナーはボタン内 1 つに一本化(ステータス帯に二重表示しない)。
  await expect(submitting.locator(".animate-spin")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-steps")).toBeVisible({ timeout: 15000 });

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(markdown.getByTestId("ontology-markdown-draft-editor")).toHaveValue(
    /# Ontology Draft/,
    { timeout: 15000 }
  );
  await expect(page.getByTestId("ontology-build-proposals")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /すべて承認/ })).toHaveCount(0);
});

test("Draft artifact 保存後は job 完了前でも Markdown Draft を表示する", async ({ page }) => {
  await mockApi(page);
  let artifactSaved = false;
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, artifactSaved ? markdownDraftPayload(generatedDraftMarkdown) : emptyMarkdownPayload())
  );
  await page.unroute("**/api/nl2sql/ontology-build/*");
  await page.route("**/api/nl2sql/ontology-build/*", (route) => {
    artifactSaved = true;
    const running = buildJob("running", "running").job;
    running.steps = running.steps.map((step) =>
      step.name === "proposal_registration"
        ? {
            ...step,
            status: "running",
            detail_ja: "Draft revision v4 を保存しました。Markdown artifact を保存しました。",
            started_at: "2026-07-12T00:00:03Z",
            finished_at: null,
          }
        : step
    );
    running.events = [
      ...running.events,
      { at: "2026-07-12T00:00:04Z", message_ja: "Markdown artifact を保存しました。" },
    ];
    running.draft_revision_id = "revision-draft-4";
    running.draft_etag = "markdown-etag-1";
    running.markdown_output = generatedDraftMarkdown;
    return fulfillJson(route, { job: running });
  });

  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await expect(section).toBeVisible({ timeout: 20000 });
  await expect(section.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
  await section.getByRole("button", { name: "AI 構築を実行" }).click();

  await expect(page.getByTestId("ontology-build-steps")).toHaveAttribute(
    "data-job-status",
    "running",
    { timeout: 15000 }
  );
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toHaveValue(
    /# Ontology Draft/,
    { timeout: 15000 }
  );
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toHaveValue(
    /## Relationships \/ Join/
  );
});

test("Markdown Draft 保存後は stale refresh でエディタ値を戻さない", async ({ page }) => {
  await mockApi(page);
  let saved = false;
  let markdownReadsAfterSave = 0;
  let buildPolls = 0;
  await page.unroute("**/api/nl2sql/profiles/*/ontology-build-jobs**");
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) => {
    const running = buildJob("running", "running").job;
    running.draft_revision_id = "revision-draft-4";
    running.draft_etag = "markdown-etag-1";
    return fulfillJson(route, { jobs: [running] });
  });
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) => {
    if (saved) {
      markdownReadsAfterSave += 1;
      return fulfillJson(route, emptyMarkdownPayload());
    }
    return fulfillJson(route, markdownDraftPayload(generatedDraftMarkdown));
  });
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown/draft");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown/draft", (route) => {
    const body = route.request().postDataJSON() as { markdown?: string };
    saved = true;
    return fulfillJson(route, markdownDraftPayload(body.markdown ?? "", "markdown-etag-2"));
  });
  await page.unroute("**/api/nl2sql/ontology-build/*");
  await page.route("**/api/nl2sql/ontology-build/*", (route) => {
    buildPolls += 1;
    const running = buildJob("running", "running").job;
    const signal = saved
      ? `Markdown artifact を保存しました。stale refresh ${buildPolls}`
      : "Markdown artifact を保存しました。";
    running.steps = running.steps.map((step) =>
      step.name === "proposal_registration"
        ? {
            ...step,
            status: "running",
            detail_ja: signal,
            started_at: "2026-07-12T00:00:03Z",
            finished_at: null,
          }
        : step
    );
    running.events = [
      ...running.events,
      { at: "2026-07-12T00:00:04Z", message_ja: signal },
    ];
    running.draft_revision_id = "revision-draft-4";
    running.draft_etag = saved ? `markdown-etag-stale-${buildPolls}` : "markdown-etag-1";
    return fulfillJson(route, { job: running });
  });
  await page.goto("/ontology-build?profile=default");

  const markdown = page.getByTestId("ontology-build-markdown");
  const draftEditor = markdown.getByTestId("ontology-markdown-draft-editor");
  await expect(markdown).toBeVisible({ timeout: 20000 });
  await expect(draftEditor).toHaveValue(/# Ontology Draft/, { timeout: 20000 });
  await expect(draftEditor).toBeEnabled({ timeout: 20000 });
  const savedMarkdown = `${generatedDraftMarkdown}\n\n## Manual Notes\n- 保存後も保持`;
  await draftEditor.fill(savedMarkdown);
  await expect(markdown.getByText("未保存")).toBeVisible();
  await markdown.getByRole("button", { name: "Markdown Draft を保存" }).click();

  await expect(page.getByText("Markdown Draft を保存しました。")).toBeVisible();
  await expect(draftEditor).toHaveValue(savedMarkdown);
  await expect(markdown.getByText("未保存")).toHaveCount(0);
  await expect.poll(() => markdownReadsAfterSave).toBeGreaterThan(0);
  await expect(draftEditor).toHaveValue(savedMarkdown);
});

test("Profile と Markdown Ontology の初期読込では loading を表示する", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-29T00:00:00.000Z") });
  await mockApi(page);
  const profilesGate = createRequestGate();
  const markdownGate = createRequestGate();
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", async (route) => {
    await profilesGate.promise;
    await fulfillJson(route, {
      items: profiles.map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: profile.archived,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: 0,
        few_shot_count: 0,
        version: 1,
        etag: `etag-${profile.id}`,
        updated_at: "2026-07-12T00:00:00Z",
      })),
      next_cursor: null,
      total: 1,
      change_token: 1,
    });
  });
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", async (route) => {
    await markdownGate.promise;
    await fulfillJson(route, {
      draft_markdown: "",
      published_markdown: "",
      draft_revision: null,
      published_revision: null,
      draft_etag: "",
      published_at: null,
    });
  });

  await page.goto("/ontology-build?profile=default");
  await expect(page.getByTestId("ontology-profile-compact-skeleton")).toBeVisible();
  profilesGate.release();
  await expect(page.getByTestId("ontology-build-profile-select")).toBeVisible();
  const skeleton = page.getByTestId("ontology-markdown-loading");
  await expect(skeleton).toBeVisible();
  await expect(skeleton).toHaveAttribute("data-processing-placement", "panel");
  await expect(skeleton.getByRole("timer")).toHaveAccessibleName("経過時間 00:00");
  await expect(skeleton.locator("svg.animate-spin")).toBeVisible();
  await expect(skeleton.getByTestId("db-management-skeleton-block")).toHaveCount(3);
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toHaveCount(0);
  await expect(page.getByTestId("ontology-publish-actions")).toHaveCount(0);

  await page.clock.fastForward(11_000);
  await expect(skeleton.getByRole("timer")).toHaveAccessibleName("経過時間 00:11");
  await expect(skeleton).toContainText("通常より時間がかかっています");
  await expect(skeleton).not.toContainText(
    "このままお待ちいただくか、取消可能な処理はキャンセルできます。",
  );

  await page.setViewportSize({ width: 375, height: 812 });
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.body.scrollWidth > document.body.clientWidth + 1,
    ),
  ).toBe(false);

  markdownGate.release();
  await expect(page.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
});

test("Markdown Ontology の初期読込はキャンセルでき、取消後の応答で上書きしない", async ({ page }) => {
  await mockApi(page);
  const markdownGate = createRequestGate();
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", async (route) => {
    await markdownGate.promise;
    try {
      await fulfillJson(route, markdownDraftPayload(generatedDraftMarkdown));
    } catch {
      // ユーザー取消で破棄された request は fulfill できなくても正常。
    }
  });

  await page.goto("/ontology-build?profile=default");
  const skeleton = page.getByTestId("ontology-markdown-loading");
  await expect(skeleton).toBeVisible();
  await skeleton.getByRole("button", { name: "キャンセル" }).click();

  await expect(skeleton).toHaveCount(0);
  await expect(page.getByText("Markdown Ontology を読み込めませんでした。")).toHaveCount(0);
  await expect(page.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
  markdownGate.release();
  await page.waitForTimeout(50);
  // Draft 未生成のままなのでエディタは表示されない(取消後の応答で上書きされていない)
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toHaveCount(0);
});

test("Profile の読込失敗から再試行できる", async ({ page }) => {
  await mockApi(page);
  let allowProfiles = false;
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", async (route) => {
    if (!allowProfiles) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ data: null, error_messages: ["一時的な接続エラー"] }),
      });
      return;
    }
    await fulfillJson(route, {
      items: profiles.map((profile) => ({
        id: profile.id,
        name: profile.name,
        category: profile.category,
        description: profile.description,
        archived: profile.archived,
        allowed_table_count: profile.allowed_tables.length,
        allowed_view_count: profile.allowed_views.length,
        glossary_count: 0,
        few_shot_count: 0,
        version: 1,
        etag: `etag-${profile.id}`,
        updated_at: "2026-07-12T00:00:00Z",
      })),
      next_cursor: null,
      total: 1,
      change_token: 1,
    });
  });
  await page.goto("/ontology-build?profile=default");
  const profilePanel = page.getByRole("region", { name: "対象プロファイル" });
  await expect(profilePanel.getByText("プロファイルの読込に失敗しました。")).toBeVisible({
    timeout: 20000,
  });
  allowProfiles = true;
  await profilePanel.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByTestId("ontology-build-profile-select")).toBeVisible();
});

test("Markdown Ontology の読込失敗から再試行できる", async ({ page }) => {
  await mockApi(page);
  let allowMarkdown = false;
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", async (route) => {
    if (!allowMarkdown) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ data: null, error_messages: ["一時的な接続エラー"] }),
      });
      return;
    }
    await fulfillJson(route, {
      draft_markdown: "",
      published_markdown: "",
      draft_revision: null,
      published_revision: null,
      draft_etag: "",
      published_at: null,
    });
  });

  await page.goto("/ontology-build?profile=default");

  const markdownPanel = page.getByTestId("ontology-build-markdown");
  await expect(markdownPanel.getByText("Markdown Ontology を読み込めませんでした。")).toBeVisible();
  allowMarkdown = true;
  await markdownPanel.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
});

test("AI 提案レビュー UI は表示せず Markdown タブだけを表示する", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  await expect(page.getByTestId("ontology-build-proposals")).toHaveCount(0);
  await expect(page.getByText("AI 提案のレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /すべて承認/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "承認", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "却下", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Markdown Ontology Draft" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Markdown Ontology Published" })).toBeVisible();
});

test("Markdown Ontology tabs はキーボードで切り替えできる", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const draftTab = page.getByRole("tab", { name: "Markdown Ontology Draft" });
  const publishedTab = page.getByRole("tab", { name: "Markdown Ontology Published" });
  await expect(draftTab).toHaveAttribute("aria-selected", "true");
  await draftTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(publishedTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("ontology-markdown-published-viewer")).toBeVisible();
  await page.keyboard.press("Home");
  await expect(draftTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
});

test("Markdown Ontology tabs は profile 別 version を優先して表示する", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, {
      draft_markdown: generatedDraftMarkdown,
      published_markdown: "",
      draft_revision: {
        id: "revision-draft-4",
        version: 4,
        status: "draft",
        schema_fingerprint: "fp",
        etag: "draft-etag-4",
      },
      published_revision: null,
      draft_version: 1,
      published_version: null,
      draft_etag: "markdown-etag-1",
      published_at: null,
    })
  );

  await page.goto("/ontology-build?profile=default");

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v1");
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("未公開");
});

test("未公開 Markdown Draft はリロード後も公開ボタンが表示される", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, {
      draft_markdown: generatedDraftMarkdown,
      published_markdown: "",
      draft_revision: {
          id: "revision-draft-4",
          version: 4,
          status: "draft",
          schema_fingerprint: "fp",
          etag: "draft-etag-4",
      },
      published_revision: null,
      draft_etag: "markdown-etag-1",
      published_at: null,
    })
  );

  await page.goto("/ontology-build?profile=default");

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("未公開");
  await expect(page.getByTestId("ontology-publish-actions").getByText("Draft v4")).toHaveCount(0);
  await expect(page.getByTestId("ontology-publish-actions").getByRole("button", { name: "Ontology を公開" })).toBeVisible();
});

test("SHACL Violation で公開を止め、修正後の再公開で復旧できる", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, {
      draft_markdown: generatedDraftMarkdown,
      published_markdown: "",
      draft_revision: {
          id: "revision-draft-4",
          version: 4,
          status: "draft",
          schema_fingerprint: "fp",
          etag: "draft-etag-4",
      },
      published_revision: null,
      draft_etag: "markdown-etag-1",
      published_at: null,
    })
  );
  let publishAttempt = 0;
  await page.route("**/api/nl2sql/ontology/revisions/*/publish", (route) => {
    publishAttempt += 1;
    return fulfillJson(route, {
      job: {
        id: `publish-recovery-${publishAttempt}`,
        revision_id: "revision-draft-4",
        requested_etag: "draft-etag-4",
        status: "queued",
      },
    });
  });
  await page.route("**/api/nl2sql/ontology-publish/*", (route) =>
    fulfillJson(route, {
      job: {
        id: `publish-recovery-${publishAttempt}`,
        revision_id: "revision-draft-4",
        requested_etag: "draft-etag-4",
        status: publishAttempt === 1 ? "failed" : "succeeded",
        shacl_conforms: publishAttempt === 1 ? false : true,
        error_code: publishAttempt === 1 ? "ONTOLOGY_SHACL_VIOLATION" : "",
        error_message_ja:
          publishAttempt === 1
            ? "SHACL Core の Violation があるため公開を中止しました。"
            : "",
      },
    })
  );

  await page.goto("/ontology-build?profile=default");
  const publish = page
    .getByTestId("ontology-publish-actions")
    .getByRole("button", { name: "Ontology を公開" });
  await publish.click();
  await expect(
    page.getByText("SHACL Core の Violation があるため公開を中止しました。")
  ).toBeVisible();
  await expect(publish).toBeEnabled();

  await publish.click();
  await expect(page.getByText("Ontology を公開しました。")).toBeVisible();
  expect(publishAttempt).toBe(2);
});

test("公開ポーリングは一時エラー後も進行状態を維持して復旧する", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, markdownDraftPayload(generatedDraftMarkdown))
  );
  await page.unroute("**/api/nl2sql/ontology-publish/*");
  let publishPolls = 0;
  await page.route("**/api/nl2sql/ontology-publish/*", (route) => {
    publishPolls += 1;
    if (publishPolls === 1) {
      return route.fulfill({
        status: 500,
        contentType: "application/json",
        body: JSON.stringify({ error_messages: ["一時的な publish poll failure"] }),
      });
    }
    return fulfillJson(route, {
      job: {
        id: "publish-job-1",
        revision_id: "revision-draft-4",
        requested_etag: "draft-etag-4",
        status: "succeeded",
        rdf_graph_name: "ONT_0123456789ABCDEF",
        inferred_graph_name: "INF_0123456789ABCDEF",
        shacl_conforms: true,
      },
    });
  });

  await page.goto("/ontology-build?profile=default");
  const publish = page
    .getByTestId("ontology-publish-actions")
    .getByRole("button", { name: "Ontology を公開" });
  await publish.click();

  const status = page.getByTestId("ontology-publish-status");
  await expect(status).toContainText("待機中");
  await expect(status).toContainText("公開完了", { timeout: 7000 });
  await expect(page.getByText("Ontology を公開しました。")).toBeVisible();
  expect(publishPolls).toBeGreaterThanOrEqual(2);
});

test("job 取得が 404 のときポーリングを停止しエラー表示で実行ボタンが復帰する", async ({
  page,
}) => {
  await mockApi(page);
  // job がサーバ再起動等で消えたケース(後勝ちで 404 に上書き)
  let polls = 0;
  await page.route("**/api/nl2sql/ontology-build/*", async (route) => {
    polls += 1;
    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({
        data: null,
        error_messages: ["AI オントロジー構築 job が見つかりません。"],
      }),
    });
  });
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  await expect(
    section.getByText("構築ジョブの状態を取得できませんでした", { exact: false })
  ).toBeVisible({ timeout: 15000 });
  // スピナーが解除され再実行できる
  const runButton = section.getByRole("button", { name: "AI 構築を実行" });
  await expect(runButton).toBeEnabled();
  // ステップ表示は消える(実体の無い job の進捗を残さない)
  await expect(page.getByTestId("ontology-build-steps")).toHaveCount(0);
  // ポーリングが停止している(追加の GET が発生しない)
  const stopped = polls;
  await page.waitForTimeout(2500);
  expect(polls).toBe(stopped);
});

test("job 取得が連続失敗しても長時間猶予内は監視を継続する", async ({ page }) => {
  await mockApi(page);
  let polls = 0;
  await page.route("**/api/nl2sql/ontology-build/*", async (route) => {
    polls += 1;
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ data: null, error_messages: ["内部エラー"] }),
    });
  });
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  await expect(
    section.getByText("構築状況の取得に連続して失敗した", { exact: false })
  ).toHaveCount(0);
  await expect.poll(() => polls, { timeout: 12000 }).toBeGreaterThanOrEqual(8);
  await expect(section.getByRole("button", { name: "構築中…" })).toBeDisabled();
  await expect(page.getByTestId("ontology-build-steps")).toBeVisible();
  const before = polls;
  await page.waitForTimeout(2500);
  expect(polls).toBeGreaterThan(before);
});

test("プロファイルが無いときは案内を表示し AI 構築は出さない", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/profiles");
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, []));
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, { items: [], next_cursor: null, total: 0, change_token: 1 })
  );
  await page.goto("/ontology-build");

  await expect(page.getByText("業務プロファイルがありません")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "AI 構築を実行" })).toHaveCount(0);
});

test("旧 tab URL を正規化し、モバイルでは単一ページを縦積みにする", async ({ page }) => {
  await mockApi(page);
  await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/ontology-build?profile=default&tab=usage&legacy=1");

  await expect(page).toHaveURL(/\/ontology-build\?profile=default$/);
  await expect(page.getByTestId("ontology-build-markdown").getByRole("tab")).toHaveCount(2);
  await expect(page.getByTestId("ontology-mermaid-panel")).toHaveCount(0);
  await expect(page.getByText("利用・コンテキスト")).toHaveCount(0);
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-editor")).toHaveCount(0);
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);
  await expect(page.locator("#ontology-query-playground-panel")).toBeVisible();
  await expectBuildPanelsStackedFullWidth(page);
  await expect(page.getByTestId("fixed-split-pane-profile-ontology-editor")).toHaveCount(0);

  await page.getByTestId("ontology-build-profile-select").focus();
  await expect(page.getByTestId("ontology-build-profile-select")).toBeFocused();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(overflow).toBe(false);
});

test("Ontology View の API エラーを表示し、キーボードで再試行して復旧する", async ({
  page,
}) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/profiles/*/ontology-view");
  let ontologyViewReads = 0;
  let releaseRetryOntologyView: () => void = () => undefined;
  const retryOntologyViewGate = new Promise<void>((resolve) => {
    releaseRetryOntologyView = resolve;
  });
  let reportRetryOntologyViewStarted: () => void = () => undefined;
  const retryOntologyViewStarted = new Promise<void>((resolve) => {
    reportRetryOntologyViewStarted = resolve;
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-view", async (route) => {
    if (route.request().method() !== "GET") {
      await fulfillJson(route, ontologyView);
      return;
    }
    ontologyViewReads += 1;
    if (ontologyViewReads === 1) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          data: null,
          error_messages: ["プロファイル範囲の準備に失敗しました。"],
        }),
      });
      return;
    }
    if (ontologyViewReads === 2) {
      reportRetryOntologyViewStarted();
      await retryOntologyViewGate;
    }
    await fulfillJson(route, { ...ontologyView, materialized: true, stale: false });
  });

  await page.goto("/ontology-build?profile=default");

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("プロファイル範囲の準備に失敗しました。");
  await expect(alert).toContainText("「再試行」で再度読み込んでください。");

  const retry = alert.getByRole("button", { name: "再試行" });
  await retry.focus();
  await page.keyboard.press("Enter");

  await retryOntologyViewStarted;
  await expect(page.getByTestId("ontology-workspace-detail-skeleton")).toBeVisible();
  await expect(alert).toHaveCount(0);
  releaseRetryOntologyView();
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-editor")).toHaveCount(0);
  await expect(alert).toHaveCount(0);
  expect(ontologyViewReads).toBe(2);
});

test("リロード後も実行中の構築ジョブを復元して進捗を追跡する", async ({ page }) => {
  const state = await mockApi(page);
  // 直近ジョブが実行中(リロード前に開始済みの想定)
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [buildJob("running", "running").job] })
  );
  await page.goto("/ontology-build?profile=default");

  // フォーム送信なしで進捗カードが復元され、ポーリングで完了まで進む
  const steps = page.getByTestId("ontology-build-steps");
  await expect(steps).toContainText("オントロジー構築の処理状況");
  await expect(steps.getByText("スキーマ情報の準備")).toBeVisible();
  const runningStep = page.getByTestId("ontology-build-step-schema_context");
  await expect(runningStep).toHaveAttribute("data-step-status", "running");
  await expect(runningStep).toHaveAttribute("aria-current", "step");
  await expect(runningStep.locator("svg.animate-spin").first()).toBeVisible();
  await expect(steps.getByRole("timer")).toHaveAttribute("aria-live", "off");
  await expect(page.getByTestId("ontology-build-step-progress")).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  const mobileOverflow = await steps.evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
  }));
  expect(mobileOverflow.scrollWidth).toBeLessThanOrEqual(mobileOverflow.clientWidth + 1);
  await expect(steps).toHaveAttribute("data-job-status", "succeeded", { timeout: 15000 });
  await expect(steps.getByText("Markdown Draft v4 を生成しました", { exact: false })).toBeVisible();
  await expect(steps.getByText("構築が完了しました", { exact: false })).toHaveCount(0);
  await expect(page.locator('[aria-label="構築ジョブの補足ログ"]')).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-timeline")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-history")).toHaveCount(0);
  expect(state.jobPolls).toBeGreaterThan(0);
});

test("実行中の構築ジョブを確認ダイアログ経由で中止できる", async ({ page }) => {
  await mockApi(page);
  let cancelCalls = 0;
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [] })
  );
  // ポーリングは実行中のまま(完了させない)
  await page.unroute("**/api/nl2sql/ontology-build/*");
  await page.route("**/api/nl2sql/ontology-build/*", (route) =>
    fulfillJson(route, buildJob("running", "running"))
  );
  await page.route("**/api/nl2sql/ontology-build/*/cancel", (route) => {
    cancelCalls += 1;
    const cancelled = buildJob("cancelled", "skipped");
    cancelled.job.error_message_ja = "利用者の操作で構築を中止しました。";
    return fulfillJson(route, cancelled);
  });
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await section.getByLabel("業務説明(自然言語)").fill("受注は顧客に紐づく。");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();

  await page.getByTestId("ontology-build-cancel").click();
  await page.getByRole("button", { name: "構築を中止" }).click();

  await expect(page.getByText("構築を中止しました。", { exact: false })).toBeVisible();
  await expect(page.getByTestId("ontology-build-retry")).toHaveCount(0);
  await expect(section.getByRole("button", { name: "AI 構築を実行" })).toBeEnabled();
  expect(cancelCalls).toBe(1);
});

test("Markdown Draft 生成が長時間更新されない場合に警告を表示する", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/ontology-build/*");
  await page.route("**/api/nl2sql/ontology-build/*", (route) => {
    const stale = buildJob("running", "succeeded");
    stale.job.steps = stale.job.steps.map((step) =>
      step.name === "proposal_registration"
        ? {
            ...step,
            status: "running",
            detail_ja: "Draft revision を保存しています…",
            started_at: "2026-07-12T00:00:03Z",
            finished_at: null,
          }
        : step
    );
    stale.job.events = [
      ...stale.job.events,
      { at: "2026-07-12T00:00:03Z", message_ja: "Draft revision を保存しています…" },
    ];
    stale.job.finished_at = null;
    return fulfillJson(route, stale);
  });
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await section.getByLabel("業務説明(自然言語)").fill("受注は顧客に紐づく。");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();

  await expect(
    page.getByText("60 分以上、Markdown Draft 生成の更新がありません。", { exact: false })
  ).toBeVisible();
  await expect(page.getByTestId("ontology-build-cancel")).toBeVisible();
});

test("完了 job に実行中 step が混在しても Markdown Draft 生成を完了表示に寄せる", async ({ page }) => {
  const state = await mockApi(page);
  await page.unroute("**/api/nl2sql/ontology-build/*");
  await page.route("**/api/nl2sql/ontology-build/*", (route) => {
    state.jobPolls = Math.max(state.jobPolls, 2);
    const inconsistent = buildJob("succeeded", "succeeded");
    inconsistent.job.steps = inconsistent.job.steps.map((step) =>
      step.name === "proposal_registration"
        ? {
            ...step,
            status: "running",
            detail_ja: "構築 job の完了状態を保存しています…",
            started_at: "2026-07-12T00:00:03Z",
            finished_at: null,
          }
        : step
    );
    return fulfillJson(route, inconsistent);
  });
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await section.getByLabel("業務説明(自然言語)").fill("受注は顧客に紐づく。");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();

  const proposalStep = page.getByTestId("ontology-build-step-proposal_registration");
  await expect(page.getByTestId("ontology-build-steps")).toHaveAttribute(
    "data-job-status",
    "succeeded"
  );
  await expect(page.getByTestId("ontology-build-step-progress")).toContainText("4/4");
  await expect(proposalStep).toHaveAttribute("data-step-status", "succeeded");
  await expect(proposalStep.locator(".animate-spin")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-cancel")).toHaveCount(0);
});

test("profile scope の schema 解決失敗から DB 構造を再取得できる", async ({ page }) => {
  const state = await mockApi(page);
  const failed = buildJob("failed", "failed");
  failed.job.id = "job-schema-scope-failed";
  failed.job.steps = [
    {
      name: "schema_context",
      status: "failed",
      detail_ja: "profile 範囲に DB 表・ビューがありません。",
      started_at: "2026-07-12T00:00:01Z",
      finished_at: "2026-07-12T00:00:03Z",
    },
    { name: "schema_naming", status: "skipped", detail_ja: "" },
    { name: "proposal_registration", status: "skipped", detail_ja: "" },
  ];
  failed.job.events = [
    {
      at: "2026-07-12T00:00:01Z",
      message_ja: "DB から profile 範囲のスキーマ情報を取得しています。",
    },
  ];
  failed.job.error_message_ja =
    "profile の対象オブジェクトを DB schema catalog に解決できません。DB 構造を再取得するか、Profile の対象 object を確認してから再実行してください。";
  failed.job.warnings_ja = [
    "「APP.INVOICES」を DB schema catalog の table として解決できません。DB 構造を再取得するか、Profile の対象 object 名(owner 付き)を確認してください。",
  ];
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [failed.job] })
  );
  await page.goto("/ontology-build?profile=default");

  const steps = page.getByTestId("ontology-build-steps");
  await expect(steps.getByText("Profile 範囲の DB schema を解決できません")).toBeVisible();
  await expect(steps.getByText("DB 構造を再取得してから", { exact: false })).toBeVisible();
  await expect(steps.getByText("公開 Ontology", { exact: false })).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-retry")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "AI 構築を実行" })).toBeEnabled();
  await page.getByTestId("ontology-build-schema-refresh").click();

  await expect(page.getByText("DB 構造を再取得しました。")).toBeVisible();
  expect(state.schemaRefreshCalls).toBe(1);
});

test("失敗した構築後は主ボタンで現在の入力を再送信できる", async ({ page }) => {
  const state = await mockApi(page);
  let retryCalls = 0;
  const failed = buildJob("failed", "failed");
  failed.job.id = "job-failed";
  failed.job.error_message_ja = "Enterprise AI が未設定です。";
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [failed.job] })
  );
  await page.route("**/api/nl2sql/ontology-build/*/retry", (route) => {
    retryCalls += 1;
    return fulfillJson(route, buildJob("queued", "pending"));
  });
  await page.goto("/ontology-build?profile=default");

  // リロード復旧: 失敗ジョブの終端カードに失敗理由が表示される
  await expect(page.getByTestId("ontology-build-retry")).toHaveCount(0);
  const failedSteps = page.getByTestId("ontology-build-steps");
  await expect(failedSteps.getByText("Enterprise AI が未設定です。")).toBeVisible();
  await expect(failedSteps.getByText("再実行してください", { exact: false })).toBeVisible();
  await expect(page.getByTestId("ontology-build-history")).toHaveCount(0);

  // 主ボタンで現在の入力を再送信 → 新ジョブがポーリングで完了まで進む
  const section = page.getByTestId("profile-ontology-build");
  await section.getByLabel("業務説明(自然言語)").fill("受注は顧客に紐づく。");
  await dropFiles(page, page.getByTestId("ontology-build-source-files-dropzone"), [
    {
      name: "rules.md",
      type: "text/markdown",
      content: "# 受注ルール",
    },
    {
      name: "terms.csv",
      type: "text/csv",
      content: "用語,説明\n受注,顧客からの注文",
    },
  ]);
  await dropFiles(page, page.getByTestId("ontology-build-qa-file-dropzone"), [
    {
      name: "qa_cases.csv",
      type: "text/csv",
      content: "QUESTION,SQL\n受注件数は,SELECT COUNT(*) FROM ORDERS",
    },
  ]);
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  await expect.poll(() => state.startCalls).toBe(1);
  expect(state.latestBusinessText).toBe("受注は顧客に紐づく。");
  expect(state.sourceFilesSeen).toBe(true);
  expect(state.qaFileSeen).toBe(true);
  expect(state.latestRunOptions).toEqual({
    runSchemaNaming: true,
    runQaExtraction: true,
    runTextExtraction: true,
  });
  const steps = page.getByTestId("ontology-build-steps");
  await expect(steps).toHaveAttribute("data-job-status", "succeeded", { timeout: 15000 });
  await expect(steps.getByText("Markdown Draft v4 を生成しました", { exact: false })).toBeVisible();
  await expect(steps.getByText("構築が完了しました", { exact: false })).toHaveCount(0);
  await expect(page.locator('[aria-label="構築ジョブの補足ログ"]')).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-timeline")).toHaveCount(0);
  expect(retryCalls).toBe(0);
});

test("失敗した構築 job は「再実行」ボタンで retry API から再開できる", async ({ page }) => {
  await mockApi(page);
  const failedJob = buildJob("failed", "failed");
  failedJob.job.error_message_ja = "LLM 抽出に失敗しました。";
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) =>
    fulfillJson(route, { jobs: [failedJob.job] })
  );
  let retryCalled = 0;
  const retriedJob = buildJob("running", "running");
  retriedJob.job.id = "job-retried";
  await page.route("**/api/nl2sql/ontology-build/job-1/retry", (route) => {
    retryCalled += 1;
    return fulfillJson(route, retriedJob);
  });
  await page.route("**/api/nl2sql/ontology-build/job-retried", (route) =>
    fulfillJson(route, retriedJob)
  );

  await page.goto("/ontology-build?profile=default");
  const retryButton = page.getByTestId("ontology-build-retry");
  await expect(retryButton).toBeVisible();
  await retryButton.click();

  await expect
    .poll(() => retryCalled, { timeout: 8_000 })
    .toBeGreaterThan(0);
  // 新 job の進捗カードへ切り替わる(失敗バナーは消える)
  await expect(page.getByTestId("ontology-build-retry")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-steps")).toContainText("オントロジー構築の処理状況");
});

test("公開済み Markdown が無いときは公開日時を表示しない(revision だけ公開済み)", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(route, {
      draft_markdown: "",
      published_markdown: "",
      draft_revision: null,
      published_revision: {
        id: "revision-published-1",
        version: 1,
        status: "published",
        schema_fingerprint: "fp",
        etag: "published-etag-1",
        published_at: "2026-08-29T02:33:06Z",
      },
      draft_etag: "",
      published_at: "2026-08-29T02:33:06Z",
    })
  );

  await page.goto("/ontology-build?profile=default");
  const markdown = page.getByTestId("ontology-build-markdown");
  await markdown.getByRole("tab", { name: "Markdown Ontology Published" }).click();
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "公開済み Markdown はまだありません。"
  );
  await expect(markdown.getByTestId("ontology-markdown-published-meta")).toHaveCount(0);
});
