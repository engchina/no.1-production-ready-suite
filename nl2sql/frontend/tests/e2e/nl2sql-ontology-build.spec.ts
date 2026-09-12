import { expect, test, type Locator, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady, systemAdminMe } from "./_helpers/database-gate";
import { dropFiles } from "./_helpers/file-dropzone";
import { expectLargeActionButton } from "./_helpers/action-button";

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
  const spans = button.locator("span");
  const label = await spans.count() ? spans.last() : button;
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

async function loadOntologyBuildWorkspace(page: Page) {
  const fetchButton = page.getByTestId("ontology-view-fetch");
  await expect(fetchButton).toBeVisible();
  await fetchButton.click();
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
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
  "# オントロジー下書き",
  "",
  "## 入力サマリー",
  "- プロファイル: `default`",
  "- DB スキーマオブジェクト: 2",
  "",
  "## 物理オブジェクト",
  "- `APP.ORDERS` (table)",
  "  - 業務名: 受注",
  "  - 用途: 確定済み受注の売上分析に使用",
  "- `APP.CUSTOMERS` (table)",
  "  - 業務名: 顧客",
  "",
  "## 業務エンティティ",
  "- 受注 (`APP.ORDERS`)",
  "",
  "## 関係 / Join",
  "- 受注 (`APP.ORDERS`) → 顧客 (`APP.CUSTOMERS`): 顧客を参照",
  "  - 検索利用: 利用可",
  "  - Join 条件:",
  "    - `APP.ORDERS.CUSTOMER_ID = APP.CUSTOMERS.ID`",
  "",
  "## 指標",
  "- 受注金額合計",
  "",
  "## 業務ルール / 列挙値",
  "- なし",
  "",
  "## 同義語",
  "- 対象: `APP.ORDERS`",
  "",
  "## 証拠 / 確認事項",
  "- なし",
  "",
  "## 採用外候補",
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
                  message_ja: "Markdown 下書き v4 を生成しました(候補 2 件、採用外 1 件)。",
                },
                {
                  at: "2026-07-12T00:00:09Z",
                  message_ja: "構築が完了しました(Markdown 下書き v4、採用外 1 件)。",
                },
              ]
            : []),
        ];
  return {
    job: {
      id: "job-1",
      definition_phases: ["freeze", "evidence", "objects", "shared", "capabilities", "validation"].map(name => ({ name, status: stepStatus, detail_ja: "" })),
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
              ? "Markdown 下書き v4 を生成しました(候補 2 件、採用外 1 件)。"
              : "",
          ...stepTimes,
        },
      ],
      events,
      proposal_ids: proposalIds,
      result_bundle_id: status === "succeeded" ? "bundle-default" : "",
      draft_revision_id: status === "succeeded" ? "revision-draft-4" : "",
      draft_etag: status === "succeeded" ? "markdown-etag-1" : "",
      markdown_output: status === "succeeded" ? generatedDraftMarkdown : "",
      warnings_ja:
        status === "succeeded"
          ? [
              "命名候補 APP.SECRET を profile 範囲内に解決できません。",
              "schema_resolved_join_conditionsが空のため自己結合は明示せず。",
            ]
          : [],
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
  await page.route("**/api/nl2sql/profiles/*/ontology-capabilities", route => fulfillJson(route, {release_id:"",display_version:null,capabilities:[],implementations:{functions:[],actions:[]}}));
  await page.route("**/api/nl2sql/profiles/*/ontology-results/*/workspace", route => fulfillJson(route, { bundle: typedBundle("default"), artifacts: { markdown: "# 型付き定義", mermaid: "graph LR" }, head: { release_id: "", etag: "" } }));
  await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [] }));
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
    publishPayload: null as Record<string, unknown> | null,
    profileDetailCalls: [] as string[],
    buildJobProfileIds: [] as string[],
    sourceDocumentProfileIds: [] as string[],
    markdownProfileIds: [] as string[],
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
            status: state.published ? "published" : "draft",
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
  await page.route(/\/api\/nl2sql\/profiles\/[^/?]+$/, (route) => {
    state.profileDetailCalls.push(profileIdFromUrl(route.request().url()));
    return fulfillJson(route, profiles[0]);
  });
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
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) => {
    state.buildJobProfileIds.push(profileIdFromUrl(route.request().url()));
    return fulfillJson(route, { jobs: [] });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-source-documents**", (route) => {
    state.sourceDocumentProfileIds.push(profileIdFromUrl(route.request().url()));
    return fulfillJson(route, { source_documents: [] });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown/draft", (route) => {
    const body = route.request().postDataJSON() as { markdown?: string; base_etag?: string };
    state.savedDraftMarkdown = body.markdown ?? "";
    state.draftMarkdown = state.savedDraftMarkdown;
    state.draftMarkdownEtag = "markdown-etag-2";
    return fulfillJson(route, markdownStatePayload());
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) => {
    state.markdownProfileIds.push(profileIdFromUrl(route.request().url()));
    return fulfillJson(route, markdownStatePayload());
  });
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
    state.publishPayload = route.request().postDataJSON() as Record<string, unknown>;
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

const profileScopedProfiles = [
  {
    id: "sales",
    name: "販売分析",
    category: "営業",
    description: "営業部門の受注分析",
    allowed_tables: ["ORDERS"],
    allowed_views: [],
    glossary: {},
    sql_rules: [],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    select_ai_config: null,
    archived: false,
  },
  {
    id: "finance",
    name: "経理分析",
    category: "経理",
    description: "経理部門の請求分析",
    allowed_tables: ["INVOICES"],
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

function profileIdFromUrl(url: string): string {
  const match = url.match(/\/api\/nl2sql\/profiles\/([^/?]+)/u);
  return match ? decodeURIComponent(match[1]) : "";
}

function profileScopedOntologyView(profileId: string) {
  return {
    profile_ontology_view: {
      id: `profile-view:${profileId}`,
      profile_id: profileId,
      ontology_revision_id: `revision-${profileId}`,
      etag: `view-etag-${profileId}`,
      node_ids: [],
      edge_ids: [],
      allowed_path_ids: [],
    },
    ontology_graph: {
      revision: {
        id: `revision-${profileId}`,
        version: 1,
        status: "published",
        schema_fingerprint: `fp-${profileId}`,
        etag: `revision-etag-${profileId}`,
      },
      nodes: [],
      edges: [],
    },
    materialized: true,
    stale: false,
    warnings_ja: [],
  };
}

async function mockProfileScopedApi(page: Page) {
  const state = {
    profileDetailCalls: [] as string[],
    ontologyViewCalls: [] as string[],
    buildJobProfileIds: [] as string[],
    sourceDocumentProfileIds: [] as string[],
    markdownProfileIds: [] as string[],
    deletedSourceIds: [] as string[],
    sourceDocumentsByProfile: {
      sales: [
        {
          id: "source-sales",
          profile_id: "sales",
          filename: "sales-rules.md",
          source_role: "source",
          media_type: "text/markdown",
          size_bytes: 2048,
          status: "extracted",
          extracted_chunk_count: 2,
          warnings_ja: [],
          created_at: "2026-07-12T00:00:00Z",
          updated_at: "2026-07-12T00:00:00Z",
        },
      ],
      finance: [
        {
          id: "source-finance",
          profile_id: "finance",
          filename: "finance-rules.xlsx",
          source_role: "source",
          media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          size_bytes: 4096,
          status: "extracted",
          extracted_chunk_count: 4,
          warnings_ja: [],
          created_at: "2026-07-12T00:00:00Z",
          updated_at: "2026-07-12T00:00:00Z",
        },
      ],
    } as Record<string, Array<Record<string, unknown>>>,
  };
  await page.route("**/api/schema/catalog", (route) =>
    fulfillJson(route, { refreshed_at: "2026-07-12T00:00:00Z", tables: [] })
  );
  await page.route("**/api/nl2sql/db-admin/tables", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/db-admin/views", (route) =>
    fulfillJson(route, { runtime: "deterministic", items: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/select-ai/db-profiles**", (route) =>
    fulfillJson(route, { runtime: "deterministic", profiles: [], warnings: [] })
  );
  await page.route("**/api/nl2sql/profiles", (route) => fulfillJson(route, profileScopedProfiles));
  await page.route("**/api/nl2sql/profiles/search?*", (route) =>
    fulfillJson(route, {
      items: profileScopedProfiles.map((profile) => ({
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
      total: profileScopedProfiles.length,
      change_token: 1,
    })
  );
  await page.route(/\/api\/nl2sql\/profiles\/[^/?]+$/, (route) => {
    const profileId = profileIdFromUrl(route.request().url());
    state.profileDetailCalls.push(profileId);
    fulfillJson(
      route,
      profileScopedProfiles.find((profile) => profile.id === profileId) ??
        profileScopedProfiles[0]
    );
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-view", (route) => {
    state.ontologyViewCalls.push(profileIdFromUrl(route.request().url()));
    fulfillJson(route, profileScopedOntologyView(profileIdFromUrl(route.request().url())));
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-build-jobs**", (route) => {
    state.buildJobProfileIds.push(profileIdFromUrl(route.request().url()));
    return fulfillJson(route, { jobs: [] });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-source-documents**", async (route) => {
    const profileId = profileIdFromUrl(route.request().url());
    const sourceDocumentId = route
      .request()
      .url()
      .match(/\/ontology-source-documents\/([^/?]+)/u)?.[1];
    if (route.request().method() === "DELETE" && sourceDocumentId) {
      const decodedSourceDocumentId = decodeURIComponent(sourceDocumentId);
      state.deletedSourceIds.push(`${profileId}:${decodedSourceDocumentId}`);
      const documents = state.sourceDocumentsByProfile[profileId] ?? [];
      const nextDocuments = documents.filter((source) => source.id !== decodedSourceDocumentId);
      state.sourceDocumentsByProfile[profileId] = nextDocuments;
      if (nextDocuments.length === documents.length) {
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({ error: "not found" }),
        });
        return;
      }
      await fulfillJson(route, { source_document_id: decodedSourceDocumentId, deleted: true });
      return;
    }
    state.sourceDocumentProfileIds.push(profileId);
    await fulfillJson(route, {
      source_documents: state.sourceDocumentsByProfile[profileId] ?? [],
    });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) => {
    state.markdownProfileIds.push(profileIdFromUrl(route.request().url()));
    return fulfillJson(route, emptyMarkdownPayload());
  });
  return state;
}

test("AI オントロジー構築の実行 → 進捗 → Markdown 下書き編集 → 公開の導線が機能する", async ({ context, page }, testInfo) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], {
    origin: `http://127.0.0.1:${process.env.PLAYWRIGHT_PORT ?? "3101"}`,
  });
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  await expect(page.locator("#ontology-workspace-not-loaded")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-markdown")).toHaveCount(0);
  expect(state.profileDetailCalls).toEqual([]);
  expect(state.buildJobProfileIds).toEqual([]);
  expect(state.sourceDocumentProfileIds).toEqual([]);
  expect(state.markdownProfileIds).toEqual([]);
  expect(state.ontologyViewCalls).toBe(0);

  await loadOntologyBuildWorkspace(page);
  const section = page.getByTestId("profile-ontology-build");
  await expect(section.getByRole("heading", { name: "オントロジー構築" })).toBeVisible();
  await expect(page.getByRole("button", { name: /オントロジー view を/ })).toHaveCount(0);
  await expect(page.getByText("オントロジー連携", { exact: true })).toHaveCount(0);
  await expect(page.getByText("業種テンプレート", { exact: true })).toHaveCount(0);
  await expect(page.getByText("OWL(RDF)エクスポート / インポート", { exact: true })).toHaveCount(
    0
  );
  await expect(page.getByText("60 分以上かかることがあります", { exact: false })).toBeVisible();
  await expectBuildPanelsStackedFullWidth(page);
  await expectSourceDropzoneMatchesQaStyle(page);
  await expectExtractionTargetsHidden(page);
  await expect(page.getByTestId("ontology-view-fetch")).toBeVisible();
  await expectLargeActionButton(page.getByTestId("ontology-view-fetch"));
  await expect(page.getByTestId("ontology-view-fetch").locator("span").first()).toHaveText(
    "情報を取得"
  );

  // 初期状態では Draft / Published とも空
  await expect(section.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
  await section.getByRole("tab", { name: "公開済み Markdown オントロジー" }).click();
  await expect(section.getByText("公開済み Markdown はまだありません。")).toBeVisible();
  await section.getByRole("tab", { name: "Markdown オントロジー下書き" }).click();
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
    .getByRole("button", { name: "ファイル選択を解除" });
  const qaClear = page
    .getByTestId("ontology-build-qa-file")
    .getByRole("button", { name: "ファイル選択を解除" });
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
  await expectButtonLabelFits(qaClear);
  await qaClear.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("qa-file-deselect.png") });
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
  await expect(steps).toContainText("能力の契約（Capability Contracts）");
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
  await expect(steps.getByText("Markdown 下書き v4 を生成しました", { exact: false })).toBeVisible();
  await expect(steps.getByText("構築リクエストを受け付けました", { exact: false })).toHaveCount(0);
  await expect(steps.getByText("AI オントロジー構築を開始しました", { exact: false })).toHaveCount(0);
  await expect(steps.getByText("構築が完了しました", { exact: false })).toHaveCount(0);
  await expect(page.locator('[aria-label="構築ジョブの補足ログ"]')).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-timeline")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-history")).toHaveCount(0);
  await expect(steps.getByRole("timer")).toHaveAccessibleName(/処理時間 \d{2}:\d{2}/);
  // スコープ外候補は正常な採用外判定なので、成功 job の警告枠には出さない。
  await expect(steps.locator("summary").filter({ hasText: "警告" })).toHaveCount(0);
  await expect(steps.getByText("APP.SECRET", { exact: false })).toHaveCount(0);
  await expect(steps.getByText("schema_resolved_join_conditions", { exact: false })).toHaveCount(0);

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(
    markdown.getByTestId("ontology-build-markdown-actions").getByText("Markdown オントロジー", {
      exact: true,
    })
  ).toBeVisible();
  await expect(markdown.getByRole("tab", { name: "Markdown オントロジー下書き" })).toBeVisible();
  await expect(markdown.getByRole("tab", { name: "公開済み Markdown オントロジー" })).toBeVisible();
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("未公開");
  const draftEditor = markdown.getByTestId("ontology-markdown-draft-editor");
  await expect(draftEditor).toHaveValue(/# オントロジー下書き/);
  await expect(draftEditor).toHaveValue(/## 採用外候補[\s\S]*APP\.SECRET/);
  await expect(draftEditor).toHaveValue(/## 関係 \/ Join/);
  await markdown.getByRole("button", { name: "Markdown をコピー" }).click();
  await expect(page.getByText("コピーしました")).toBeVisible();

  await markdown.getByRole("tab", { name: "公開済み Markdown オントロジー" }).click();
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "公開済み Markdown はまだありません。"
  );
  await markdown.getByRole("tab", { name: "Markdown オントロジー下書き" }).click();

  // 標準図示は質問の接地確認グラフへ一本化され、公開後は同じ公開 revision へ更新される
  const ontologyQueryPanel = page.locator("#ontology-query-playground-panel");
  await expect(page.getByTestId("ontology-mermaid-panel")).toHaveCount(0);
  await expect(
    ontologyQueryPanel.getByRole("heading", { name: "質問のオントロジー接地確認用グラフ" })
  ).toBeVisible();
  await expect.poll(() => state.ontologyViewCalls).toBe(1);
  await expect(ontologyQueryPanel.getByTestId("ontology-playground-version")).toHaveText(
    "公開済みバージョン: v3"
  );
  const graphExpandButton = ontologyQueryPanel.getByRole("button", { name: "グラフを表示" });
  if (await graphExpandButton.isVisible()) {
    await graphExpandButton.click();
  }
  await expect(ontologyQueryPanel.getByTestId("ontology-graph-mode-physical_er")).toBeVisible();

  await draftEditor.fill(`${generatedDraftMarkdown}\n\n## 手動メモ\n- 公開確認済み`);
  await expect(markdown.getByText("未保存")).toBeVisible();
  const ontologyViewCallsBeforePublish = state.ontologyViewCalls;

  await expect(page.getByTestId("ontology-build-proposals")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /承認/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /却下/ })).toHaveCount(0);

  const publishActions = page.getByTestId("ontology-publish-actions");
  const publishButton = publishActions.getByRole("button", { name: "オントロジーを公開" });
  await expectButtonLabelFits(publishButton);
  await publishButton.click();
  // 公開完了の“瞬間”は toast(document.body 直下)で通知する(spec §9)。section 外なので page スコープで確認。
  await expect(page.getByText("オントロジーを公開しました。")).toBeVisible();
  expect(state.savedDraftMarkdown).toContain("手動メモ");
  expect(state.published).toBe(true);
  expect(state.publishPayload).toMatchObject({ etag: "draft-etag-4", profile_id: "default" });
  await expect(page.getByTestId("ontology-publish-status")).toContainText("完了");
  await expect.poll(() => state.ontologyViewCalls).toBeGreaterThan(ontologyViewCallsBeforePublish);
  await expect(ontologyQueryPanel.getByTestId("ontology-playground-version")).toHaveText(
    "公開済みバージョン: v4"
  );
  await expect(page.getByTestId("ontology-mermaid-panel")).toHaveCount(0);
  await markdown.getByRole("tab", { name: "公開済み Markdown オントロジー" }).click();
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "手動メモ"
  );
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-published-meta")).toContainText(
    /公開日時: 07\/12 \d{2}:00/u
  );

  // 旧「物理・業務モデル編集」は Markdown 下書きに統合され、別編集 UI は表示しない
  await expect(page.locator('section[aria-label="物理・業務モデル編集"]')).toHaveCount(0);
  await expect(page.getByText("Inspector", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Draft を保存", exact: true })).toHaveCount(0);
  await expect(markdown.getByRole("button", { name: "Markdown 下書きを保存" })).toBeVisible();
  await expect(markdown.getByRole("tab")).toHaveCount(2);
  await markdown.getByRole("tab", { name: "Markdown オントロジー下書き" }).click();
  await expect(draftEditor).toHaveValue(/## 物理オブジェクト/);
  await expect(draftEditor).toHaveValue(/## 業務ルール \/ 列挙値/);
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

test("オントロジー構築の保存済みファイルは選択プロファイルごとに切り替わる", async ({ page }) => {
  const state = await mockProfileScopedApi(page);

  await page.goto("/ontology-build?profile=sales");

  await expect(page.getByTestId("ontology-build-profile-select")).toHaveValue("sales");
  await expect(page.locator("#ontology-workspace-not-loaded")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  expect(state.profileDetailCalls).toEqual([]);
  expect(state.ontologyViewCalls).toEqual([]);
  expect(state.buildJobProfileIds).toEqual([]);
  expect(state.sourceDocumentProfileIds).toEqual([]);
  expect(state.markdownProfileIds).toEqual([]);
  await loadOntologyBuildWorkspace(page);
  await expect(page.getByTestId("ontology-build-profile-scope")).toContainText(
    "対象: 販売分析（営業）"
  );
  const savedFiles = page.getByTestId("ontology-build-saved-files");
  await expect(savedFiles).toContainText(
    "販売分析（営業）で過去にオントロジー構築へ使用したファイルです。"
  );
  await expect(savedFiles.getByText("sales-rules.md", { exact: true })).toBeVisible();
  await expect(savedFiles.getByText("finance-rules.xlsx", { exact: true })).toHaveCount(0);

  await page.getByTestId("ontology-build-profile-select").selectOption("finance");

  await expect(page).toHaveURL(/profile=finance/u);
  await expect(page.locator("#ontology-workspace-not-loaded")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  await loadOntologyBuildWorkspace(page);
  await expect(page.getByTestId("ontology-build-profile-scope")).toContainText(
    "対象: 経理分析（経理）"
  );
  await expect(savedFiles).toContainText(
    "経理分析（経理）で過去にオントロジー構築へ使用したファイルです。"
  );
  await expect(savedFiles.getByText("finance-rules.xlsx", { exact: true })).toBeVisible();
  await expect(savedFiles.getByText("sales-rules.md", { exact: true })).toHaveCount(0);
  expect(state.sourceDocumentProfileIds).toEqual(expect.arrayContaining(["sales", "finance"]));
});

test("オントロジー構築の保存済みファイルは確認付きで削除できる", async ({ page }) => {
  const state = await mockProfileScopedApi(page);

  await page.goto("/ontology-build?profile=sales");
  await loadOntologyBuildWorkspace(page);

  const savedFiles = page.getByTestId("ontology-build-saved-files");
  await expect(savedFiles.getByText("sales-rules.md", { exact: true })).toBeVisible();
  const deleteButton = savedFiles.getByRole("button", {
    name: "sales-rules.md を保存済みファイルから削除",
  });
  await expect(deleteButton).toBeVisible();

  await deleteButton.click();
  const dialog = page.getByRole("alertdialog", {
    name: "保存済みファイルを削除しますか",
  });
  await expect(dialog).toContainText("sales-rules.md");
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  expect(state.deletedSourceIds).toEqual([]);
  await expect(savedFiles.getByText("sales-rules.md", { exact: true })).toBeVisible();

  await deleteButton.click();
  await page
    .getByRole("alertdialog", { name: "保存済みファイルを削除しますか" })
    .getByRole("button", { name: "削除" })
    .click();

  await expect.poll(() => state.deletedSourceIds).toEqual(["sales:source-sales"]);
  await expect(savedFiles.getByText("sales-rules.md", { exact: true })).toHaveCount(0);
  await expect(savedFiles.getByTestId("ontology-build-saved-files-empty")).toBeVisible();
  await expect(
    page.getByText("「sales-rules.md」を保存済みファイルから削除しました。")
  ).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(overflow).toBe(false);
});

test("公開完了後も下書き版を前の version のまま保持する", async ({ page }) => {
  const state = await mockApi(page);
  state.jobPolls = 2;
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) =>
    fulfillJson(
      route,
      state.published
        ? {
            draft_markdown: state.draftMarkdown,
            published_markdown: state.publishedMarkdown || state.draftMarkdown,
            draft_revision: {
              id: "revision-draft-4",
              version: 4,
              status: "published",
              schema_fingerprint: "fp",
              etag: "draft-etag-4",
              published_at: "2026-07-12T00:00:20Z",
            },
            published_revision: {
              id: "revision-draft-4",
              version: 4,
              status: "published",
              schema_fingerprint: "fp",
              etag: "draft-etag-4",
              published_at: "2026-07-12T00:00:20Z",
            },
            draft_etag: state.draftMarkdownEtag,
            published_at: "2026-07-12T00:00:20Z",
          }
        : markdownDraftPayload(state.draftMarkdown)
    )
  );

  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

  const markdown = page.getByTestId("ontology-build-markdown");
  const draftEditor = markdown.getByTestId("ontology-markdown-draft-editor");
  await expect(draftEditor).toHaveValue(/# オントロジー下書き/);
  await draftEditor.fill(`${generatedDraftMarkdown}\n\n## 手動メモ\n- 公開確認済み`);

  await markdown
    .getByTestId("ontology-publish-actions")
    .getByRole("button", { name: "オントロジーを公開" })
    .click();

  await expect(page.getByText("オントロジーを公開しました。")).toBeVisible();
  await expect(page.getByTestId("ontology-publish-status")).toContainText("完了");
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("v4");

  await markdown.getByRole("tab", { name: "公開済み Markdown オントロジー" }).click();
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "手動メモ"
  );
  await expect(markdown.getByTestId("ontology-markdown-published-meta")).toContainText(
    /公開日時: 07\/12 \d{2}:00/u
  );

  await markdown.getByRole("tab", { name: "Markdown オントロジー下書き" }).click();
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-draft-editor")).toHaveValue(/手動メモ/);
  await expect(markdown.getByTestId("ontology-markdown-draft-editor")).toHaveAttribute(
    "readonly",
    ""
  );
  await expect(
    markdown
      .getByTestId("ontology-publish-actions")
      .getByRole("button", { name: "オントロジーを公開" })
  ).toBeDisabled();
});

test("公開直後の Markdown 再読込が一時失敗しても公開済み内容を保持する", async ({ page }) => {
  const state = await mockApi(page);
  state.jobPolls = 2;
  let failFirstPublishedRefresh = true;
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) => {
    if (state.published && failFirstPublishedRefresh) {
      failFirstPublishedRefresh = false;
      return route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ data: null, error_messages: ["一時的な接続エラー"] }),
      });
    }
    return fulfillJson(
      route,
      state.published
        ? {
            draft_markdown: state.draftMarkdown,
            published_markdown: state.publishedMarkdown || state.draftMarkdown,
            draft_revision: {
              id: "revision-draft-4",
              version: 4,
              status: "published",
              schema_fingerprint: "fp",
              etag: "draft-etag-4",
              published_at: "2026-07-12T00:00:20Z",
            },
            published_revision: {
              id: "revision-draft-4",
              version: 4,
              status: "published",
              schema_fingerprint: "fp",
              etag: "draft-etag-4",
              published_at: "2026-07-12T00:00:20Z",
            },
            draft_etag: state.draftMarkdownEtag,
            published_at: "2026-07-12T00:00:20Z",
          }
        : markdownDraftPayload(state.draftMarkdown)
    );
  });

  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

  const markdown = page.getByTestId("ontology-build-markdown");
  const draftEditor = markdown.getByTestId("ontology-markdown-draft-editor");
  await expect(draftEditor).toHaveValue(/# オントロジー下書き/);
  await draftEditor.fill(
    `${generatedDraftMarkdown}\n\n## 手動メモ\n- 公開直後の再読込に失敗しても保持`
  );

  await markdown
    .getByTestId("ontology-publish-actions")
    .getByRole("button", { name: "オントロジーを公開" })
    .click();

  await expect(page.getByText("オントロジーを公開しました。")).toBeVisible();
  await expect(markdown.getByText("Markdown オントロジーを読み込めませんでした。")).toHaveCount(0);
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("v4");
  await expect(markdown.getByRole("tab", { name: "公開済み Markdown オントロジー" })).toHaveAttribute(
    "aria-selected",
    "true"
  );
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "公開直後の再読込に失敗しても保持"
  );
  await markdown.getByRole("tab", { name: "Markdown オントロジー下書き" }).click();
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-draft-editor")).toHaveValue(
    /公開直後の再読込に失敗しても保持/
  );
  await expect(markdown.getByTestId("ontology-markdown-draft-editor")).toHaveAttribute(
    "readonly",
    ""
  );
});

test("オントロジー構築の処理状況は折りたたみでき、再実行で自動展開する", async ({ page }) => {
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

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
    await loadOntologyBuildWorkspace(page);
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

test("送信直後にプレースホルダーが出て、完了後は Markdown 下書きを表示する", async ({ page }) => {
  await mockApi(page);
  // POST を遅らせて「送信中」プレースホルダーを観測できるようにする
  await page.route("**/api/nl2sql/profiles/*/ontology-build", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    await fulfillJson(route, buildJob("queued", "pending"));
  });
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

  const section = page.getByTestId("profile-ontology-build");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  const submitting = page.getByTestId("ontology-build-submitting");
  await expect(submitting).toBeVisible();
  // 送信中のスピナーはボタン内 1 つに一本化(ステータス帯に二重表示しない)。
  await expect(submitting.locator(".animate-spin")).toHaveCount(0);
  await expect(page.getByTestId("ontology-build-steps")).toBeVisible({ timeout: 15000 });

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(markdown.getByTestId("ontology-markdown-draft-editor")).toHaveValue(
    /# オントロジー下書き/,
    { timeout: 15000 }
  );
  await expect(page.getByTestId("ontology-build-proposals")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /すべて承認/ })).toHaveCount(0);
});

test("下書き成果物の保存後は job 完了前でも Markdown 下書きを表示する", async ({ page }) => {
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
            detail_ja: "下書き revision v4 を保存しました。Markdown 成果物を保存しました。",
            started_at: "2026-07-12T00:00:03Z",
            finished_at: null,
          }
        : step
    );
    running.events = [
      ...running.events,
      { at: "2026-07-12T00:00:04Z", message_ja: "Markdown 成果物を保存しました。" },
    ];
    running.draft_revision_id = "revision-draft-4";
    running.draft_etag = "markdown-etag-1";
    running.markdown_output = generatedDraftMarkdown;
    return fulfillJson(route, { job: running });
  });

  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

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
    /# オントロジー下書き/,
    { timeout: 15000 }
  );
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toHaveValue(
    /## 関係 \/ Join/
  );
});

test("Markdown 下書き保存後は stale refresh でエディタ値を戻さない", async ({ page }) => {
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
      ? `Markdown 成果物を保存しました。stale refresh ${buildPolls}`
      : "Markdown 成果物を保存しました。";
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
  await loadOntologyBuildWorkspace(page);

  const markdown = page.getByTestId("ontology-build-markdown");
  const draftEditor = markdown.getByTestId("ontology-markdown-draft-editor");
  await expect(markdown).toBeVisible({ timeout: 20000 });
  await expect(draftEditor).toHaveValue(/# オントロジー下書き/, { timeout: 20000 });
  await expect(draftEditor).toBeEnabled({ timeout: 20000 });
  const savedMarkdown = `${generatedDraftMarkdown}\n\n## 手動メモ\n- 保存後も保持`;
  await draftEditor.fill(savedMarkdown);
  await expect(markdown.getByText("未保存")).toBeVisible();
  await markdown.getByRole("button", { name: "Markdown 下書きを保存" }).click();

  await expect(page.getByText("Markdown 下書きを保存しました。")).toBeVisible();
  await expect(draftEditor).toHaveValue(savedMarkdown);
  await expect(markdown.getByText("未保存")).toHaveCount(0);
  await expect.poll(() => markdownReadsAfterSave).toBeGreaterThan(0);
  await expect(draftEditor).toHaveValue(savedMarkdown);
});

test("Profile の一覧読込と情報取得後の workspace/Markdown 読込では loading を表示する", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-07-29T00:00:00.000Z") });
  await mockApi(page);
  const profilesGate = createRequestGate();
  const ontologyViewGate = createRequestGate();
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
  await page.unroute("**/api/nl2sql/profiles/*/ontology-view");
  await page.route("**/api/nl2sql/profiles/*/ontology-view", async (route) => {
    await ontologyViewGate.promise;
    await fulfillJson(route, {
      ...ontologyView,
      materialized: true,
      stale: false,
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
  await expect(page.locator("#ontology-workspace-not-loaded")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  await expect(page.getByTestId("ontology-markdown-loading")).toHaveCount(0);

  await page.getByTestId("ontology-view-fetch").click();
  const workspaceSkeleton = page.getByTestId("ontology-workspace-loading");
  await expect(workspaceSkeleton).toBeVisible();
  await expect(workspaceSkeleton).toHaveAttribute("data-processing-placement", "panel");
  await expect(workspaceSkeleton.getByRole("timer")).toHaveAccessibleName("経過時間 00:00");
  await expect(workspaceSkeleton.locator("svg.animate-spin")).toBeVisible();
  await expect(workspaceSkeleton.getByTestId("db-management-skeleton-block")).toHaveCount(3);
  await expect(page.getByTestId("profile-ontology-build")).toHaveCount(0);
  ontologyViewGate.release();

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

test("Markdown オントロジーの読込はキャンセル操作を表示せず完了時に内容を表示する", async ({ page }, testInfo) => {
  await page.clock.install({ time: new Date("2026-09-12T00:00:00Z") });
  await mockApi(page);
  const markdownGate = createRequestGate();
  await page.unroute("**/api/nl2sql/profiles/*/ontology-markdown");
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", async (route) => {
    await markdownGate.promise;
    await fulfillJson(route, markdownDraftPayload(generatedDraftMarkdown));
  });

  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);
  const skeleton = page.getByTestId("ontology-markdown-loading");
  await expect(skeleton).toBeVisible();
  await expect(skeleton.getByRole("button", { name: "キャンセル" })).toHaveCount(0);
  await page.clock.fastForward(21_000);
  await expect(skeleton.getByRole("timer")).toHaveAccessibleName("経過時間 00:21");
  await expect(skeleton).toContainText("通常より時間がかかっています");
  await expect(skeleton.getByRole("button", { name: "キャンセル" })).toHaveCount(0);
  await skeleton.screenshot({ path: testInfo.outputPath("markdown-loading-without-cancel.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);

  markdownGate.release();
  await expect(skeleton).toHaveCount(0);
  await expect(page.getByTestId("ontology-markdown-draft-editor")).toHaveValue(generatedDraftMarkdown);
  await expect(page.getByText("Markdown オントロジーを読み込めませんでした。")).toHaveCount(0);
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

test("Markdown オントロジーの読込失敗から再試行できる", async ({ page }) => {
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
  await loadOntologyBuildWorkspace(page);

  const markdownPanel = page.getByTestId("ontology-build-markdown");
  await expect(markdownPanel.getByText("Markdown オントロジーを読み込めませんでした。")).toBeVisible();
  allowMarkdown = true;
  await markdownPanel.getByRole("button", { name: "再試行" }).click();
  await expect(page.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
});

test("AI 提案レビュー UI は表示せず Markdown タブだけを表示する", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

  await expect(page.getByTestId("ontology-build-proposals")).toHaveCount(0);
  await expect(page.getByText("AI 提案のレビュー")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /すべて承認/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "承認", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "却下", exact: true })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Markdown オントロジー下書き" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "公開済み Markdown オントロジー" })).toBeVisible();
});

test("Markdown オントロジー tabs はキーボードで切り替えできる", async ({ page }) => {
  await mockApi(page);
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

  const draftTab = page.getByRole("tab", { name: "Markdown オントロジー下書き" });
  const publishedTab = page.getByRole("tab", { name: "公開済み Markdown オントロジー" });
  await expect(draftTab).toHaveAttribute("aria-selected", "true");
  await draftTab.focus();
  await page.keyboard.press("ArrowRight");
  await expect(publishedTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("ontology-markdown-published-viewer")).toBeVisible();
  await page.keyboard.press("Home");
  await expect(draftTab).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("ontology-markdown-draft-empty")).toBeVisible();
});

test("Markdown オントロジー tabs は profile 別 version を優先して表示する", async ({ page }) => {
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
  await loadOntologyBuildWorkspace(page);

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v1");
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("未公開");
});

test("未公開 Markdown 下書きはリロード後も公開ボタンが表示される", async ({ page }) => {
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
  await loadOntologyBuildWorkspace(page);

  const markdown = page.getByTestId("ontology-build-markdown");
  await expect(markdown.getByTestId("ontology-markdown-tab-draft-meta")).toHaveText("v4");
  await expect(markdown.getByTestId("ontology-markdown-tab-published-meta")).toHaveText("未公開");
  await expect(page.getByTestId("ontology-publish-actions").getByText("下書き v4")).toHaveCount(0);
  await expect(page.getByTestId("ontology-publish-actions").getByRole("button", { name: "オントロジーを公開" })).toBeVisible();
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
  await loadOntologyBuildWorkspace(page);
  const publish = page
    .getByTestId("ontology-publish-actions")
    .getByRole("button", { name: "オントロジーを公開" });
  await publish.click();
  await expect(
    page.getByText("SHACL Core の Violation があるため公開を中止しました。")
  ).toBeVisible();
  await expect(publish).toBeEnabled();

  await publish.click();
  await expect(page.getByText("オントロジーを公開しました。")).toBeVisible();
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
  await loadOntologyBuildWorkspace(page);
  const publish = page
    .getByTestId("ontology-publish-actions")
    .getByRole("button", { name: "オントロジーを公開" });
  await publish.click();

  const status = page.getByTestId("ontology-publish-status");
  await expect(status).toContainText("待機中");
  await expect(status).toContainText("公開完了", { timeout: 7000 });
  await expect(page.getByText("オントロジーを公開しました。")).toBeVisible();
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
  await loadOntologyBuildWorkspace(page);

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
  await loadOntologyBuildWorkspace(page);

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
  await loadOntologyBuildWorkspace(page);
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

test("オントロジー View の API エラーを表示し、キーボードで再試行して復旧する", async ({
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
  await expect(page.getByText("オントロジー情報は未取得です")).toBeVisible();
  await expectLargeActionButton(page.getByTestId("ontology-view-fetch"));
  expect(ontologyViewReads).toBe(0);
  await page.getByTestId("ontology-view-fetch").focus();
  await page.getByTestId("ontology-view-fetch").press("Enter");

  const alert = page.getByRole("alert");
  await expect(alert).toContainText("プロファイル範囲の準備に失敗しました。");
  await expect(alert).toContainText("「再試行」で再度読み込んでください。");

  const retry = alert.getByRole("button", { name: "再試行" });
  await retry.focus();
  await page.keyboard.press("Enter");

  await retryOntologyViewStarted;
  await expect(page.getByTestId("ontology-workspace-loading")).toBeVisible();
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
  await expect(page.locator("#ontology-workspace-not-loaded")).toBeVisible();
  expect(state.buildJobProfileIds).toEqual([]);
  await loadOntologyBuildWorkspace(page);

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
  await expect(steps.getByText("Markdown 下書き v4 を生成しました", { exact: false })).toBeVisible();
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
  await loadOntologyBuildWorkspace(page);

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

test("Markdown 下書き生成が長時間更新されない場合に警告を表示する", async ({ page }) => {
  await mockApi(page);
  await page.unroute("**/api/nl2sql/ontology-build/*");
  await page.route("**/api/nl2sql/ontology-build/*", (route) => {
    const stale = buildJob("running", "succeeded");
    stale.job.steps = stale.job.steps.map((step) =>
      step.name === "proposal_registration"
        ? {
            ...step,
            status: "running",
            detail_ja: "下書き revision を保存しています…",
            started_at: "2026-07-12T00:00:03Z",
            finished_at: null,
          }
        : step
    );
    stale.job.events = [
      ...stale.job.events,
      { at: "2026-07-12T00:00:03Z", message_ja: "下書き revision を保存しています…" },
    ];
    stale.job.finished_at = null;
    return fulfillJson(route, stale);
  });
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);

  const section = page.getByTestId("profile-ontology-build");
  await section.getByLabel("業務説明(自然言語)").fill("受注は顧客に紐づく。");
  await section.getByRole("button", { name: "AI 構築を実行" }).click();

  await expect(
    page.getByText("60 分以上、Markdown 下書き生成の更新がありません。", { exact: false })
  ).toBeVisible();
  await expect(page.getByTestId("ontology-build-cancel")).toBeVisible();
});

test("完了 job に実行中 step が混在しても Markdown 下書き生成を完了表示に寄せる", async ({ page }) => {
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
  await loadOntologyBuildWorkspace(page);

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
  await loadOntologyBuildWorkspace(page);

  const steps = page.getByTestId("ontology-build-steps");
  await expect(steps.getByText("Profile 範囲の DB schema を解決できません")).toBeVisible();
  await expect(steps.getByText("DB 構造を再取得してから", { exact: false })).toBeVisible();
  await expect(steps.getByText("公開オントロジー", { exact: false })).toHaveCount(0);
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
  await loadOntologyBuildWorkspace(page);

  // リロード復旧: 失敗ジョブの終端カードに失敗理由が表示される
  await expect(page.getByTestId("ontology-build-retry")).toBeVisible();
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
  await expect(steps.getByText("Markdown 下書き v4 を生成しました", { exact: false })).toBeVisible();
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
  await loadOntologyBuildWorkspace(page);
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
  await loadOntologyBuildWorkspace(page);
  const markdown = page.getByTestId("ontology-build-markdown");
  await markdown.getByRole("tab", { name: "公開済み Markdown オントロジー" }).click();
  await expect(markdown.getByTestId("ontology-markdown-published-viewer")).toContainText(
    "公開済み Markdown はまだありません。"
  );
  await expect(markdown.getByTestId("ontology-markdown-published-meta")).toHaveCount(0);
});

test("失効したProfile URLでは別Profileの情報を取得せず明示選択を待つ", async ({ page }, testInfo) => {
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=missing");
  const fetch = page.getByRole("button", { name: "情報を取得", exact: true });
  await expect(fetch).toBeDisabled();
  await expect(page.getByText("指定された profile が見つかりません。")).toBeVisible();
  expect(state.profileDetailCalls).toEqual([]);
  expect(state.ontologyViewCalls).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("ontology-missing-profile.png") });
  await page.getByTestId("ontology-build-profile-select").selectOption("default");
  await expect(fetch).toBeEnabled();
  await fetch.press("Enter");
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
  expect(state.profileDetailCalls).toEqual(["default"]);
});

test("公開前の草稿保存中は編集と構築を固定し失敗後に草稿を保持する", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown", (route) => fulfillJson(route, markdownDraftPayload(generatedDraftMarkdown)));
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);
  const editor = page.getByTestId("ontology-markdown-draft-editor");
  const draft = `${generatedDraftMarkdown}\n\n## 公開対象\n保存して公開する草稿`;
  await editor.fill(draft);
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  let saves = 0;
  let publishes = 0;
  await page.route("**/api/nl2sql/profiles/*/ontology-markdown/draft", async (route) => {
    saves += 1;
    if (saves === 1) {
      await gate;
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "草稿保存テストエラー" }) });
    }
    return fulfillJson(route, markdownDraftPayload(draft, "markdown-etag-2"));
  });
  await page.route("**/api/nl2sql/ontology/revisions/*/publish", async (route) => { publishes += 1; await route.fallback(); });
  const publish = page.getByRole("button", { name: "オントロジーを公開", exact: true });
  await publish.click();
  try {
    await expect(editor).toBeDisabled();
    await expect(page.getByRole("button", { name: "AI 構築を実行", exact: true })).toBeDisabled();
    await expect(page.getByRole("button", { name: "Markdown 下書きを保存", exact: true })).toBeDisabled();
  } finally { release?.(); }
  await expect(page.getByText("草稿保存テストエラー", { exact: true })).toBeVisible();
  await expect(editor).toBeEnabled();
  await expect(editor).toHaveValue(draft);
  expect(publishes).toBe(0);
  await page.screenshot({ path: testInfo.outputPath("ontology-publish-save-recovery.png") });
  await publish.press("Enter");
  await expect.poll(() => publishes).toBe(1);
  await expect(page.getByText("オントロジーを公開しました。", { exact: true })).toBeVisible();
  expect(saves).toBe(2);
});

function typedBundle(profileId: string) {
  const kinds = ["object_type", "property", "link_type", "function", "action_type", "interface"];
  return {
    id: `bundle-${profileId}`, profile_id: profileId, display_version: 2, etag: "bundle-etag", status: "draft",
    created_at: "2026-09-11T00:00:00Z", parent_id: "", findings: [], conflicts: [],
    definitions: kinds.map(kind => ({ id: `${profileId}-${kind}`, api_name: `${profileId}_${kind}`,
      kind, name_ja: `${profileId}の定義`, description_ja: "業務資料から構築した定義です。",
      review_status: "unreviewed", missing_information_ja: [], evidence: [], mappings: [],
      ...(kind === "object_type" ? { primary_key: ["orderId"], grain_ja: "受注単位" } : {}),
    })),
    coverage: kinds.map(kind => ({ kind, count: 1, status: "generated", reason_ja: "" })),
  };
}

test("構築段階は一般状態を日本語だけで表示し専門概念の併記を維持する", async ({ page }, testInfo) => {
  await mockApi(page);
  const payload = buildJob("running", "running");
  const statuses = ["succeeded", "succeeded", "running", "skipped", "pending", "failed"];
  payload.job.definition_phases.forEach((phase, index) => { phase.status = statuses[index]; });
  await page.route("**/api/nl2sql/ontology-build/*", route => fulfillJson(route, payload));
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);
  await page.getByLabel("業務説明(自然言語)").fill("受注と顧客の関係を構築してください。");
  await page.getByRole("button", { name: "AI 構築を実行", exact: true }).click();
  const panel = page.getByTestId("ontology-typed-results");
  const phases = page.getByTestId("ontology-build-steps");
  await expect(phases).toContainText("範囲・版の固定");
  for (const status of ["完了", "処理中", "省略", "待機中", "失敗"]) {
    await expect(phases.getByText(status, { exact: true }).first()).toBeVisible();
  }
  await expect(phases).not.toContainText(/Completed|Running|Skipped|Pending|Failed/);
  await expect(phases).toContainText("オブジェクト・関係（Objects & Links）");
  await expect(phases).toContainText("共有定義（Shared Definitions）");
  await expect(phases).toContainText("能力の契約（Capability Contracts）");
  await expect(panel.getByRole("heading", { name: "構築結果", exact: true })).toBeVisible();
  const refresh = panel.getByRole("button", { name: "最新情報を取得", exact: true });
  await refresh.focus();
  await expect(refresh).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(refresh).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await panel.screenshot({ path: testInfo.outputPath("ontology-build-statuses.png") });
});

test("型付き構築結果の六概念を日英併記で閲覧し、検索・キーボード・再読込を利用できる", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [typedBundle("default")] }));
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);
  const panel = page.getByTestId("ontology-typed-results");
  await panel.getByRole("tab", { name: "モデル", exact: true }).click();
  await expect(panel.getByRole("heading", { name: "構築結果" })).toBeVisible();
  for (const label of ["オブジェクト型（Object Type）", "プロパティ（Property）", "リンク型（Link Type）", "関数（Function）", "アクション型（Action Type）", "インターフェース（Interface）"]) {
    const category = panel.getByRole("combobox", {name:"概念の種類（Concept Types）"});
    await category.click();
    await panel.getByRole("option",{name:`${label} (1)`,exact:true}).click();
    await expect(category).toContainText(label);
    const row = panel.getByRole("button", {name:/defaultの定義/});
    await row.focus(); await page.keyboard.press("Enter");
    await expect(row).toHaveAttribute("aria-pressed", "true");
    await expect(panel.getByText("根拠・来歴（Evidence & Provenance）", { exact: true })).toBeVisible();
  }
  await panel.getByLabel("定義を検索").fill("見つからない定義");
  await expect(panel.getByText("該当する定義がありません。", { exact: true })).toBeVisible();
  await panel.getByLabel("定義を検索").clear();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await panel.screenshot({ path: testInfo.outputPath("ontology-concepts.png") });
  await page.reload();
  await loadOntologyBuildWorkspace(page);
  await expect(page.getByTestId("ontology-typed-results").getByRole("combobox", {name:"概念の種類（Concept Types）"})).toContainText("インターフェース");
});

test("型付き結果の取得失敗は再試行でき、Profile 切替で前の定義を表示しない", async ({ page }) => {
  await mockProfileScopedApi(page);
  let failed = true;
  await page.route("**/api/nl2sql/profiles/*/ontology-results", async route => {
    if (failed) return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "failed" }) });
    const id = new URL(route.request().url()).pathname.split("/")[4];
    await fulfillJson(route, { results: [typedBundle(id)] });
  });
  await page.goto("/ontology-build?profile=sales");
  await loadOntologyBuildWorkspace(page);
  const panel = page.getByTestId("ontology-typed-results");
  await expect(panel.getByRole("alert")).toBeVisible();
  failed = false;
  await panel.getByRole("button", { name: "最新情報を取得" }).click();
  await panel.getByRole("tab", { name: "モデル", exact: true }).click();
  await expect(panel.getByRole("button", {name:/の定義/})).toContainText("salesの定義");
  await page.getByTestId("ontology-build-profile-select").selectOption("finance");
  await loadOntologyBuildWorkspace(page);
  await panel.getByRole("tab", { name: "モデル", exact: true }).click();
  await expect(panel.getByRole("button", {name:/の定義/})).toContainText("financeの定義");
  await expect(panel.getByText("salesの定義", { exact: false })).toHaveCount(0);
});

test("型付き構築の証拠・競合・再検証状態を確認できる", async ({ page }, testInfo) => {
  await mockApi(page);
  const bundle = typedBundle("default");
  const definition = { ...bundle.definitions[0], evidence: [{ source_id: "manual", locator: "line:1", excerpt_ja: "受注は顧客に属します。", verified: true }] };
  await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [{ ...bundle, requires_revalidation: true, definitions: [definition], conflicts: [{ definition_id: definition.id, current: definition, proposed: { ...definition, name_ja: "AI の変更案" } }] }] }));
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);
  const panel = page.getByTestId("ontology-typed-results");
  await expect(panel.getByText(/Profile または Schema が変更されました/)).toBeVisible();
  await panel.getByRole("tab", {name:"レビュー・公開",exact:true}).click();
  await expect(panel.getByText(/AI の変更案/)).toBeVisible();
  await panel.getByRole("tab", { name: "モデル", exact: true }).click();
  await panel.getByRole("button",{name:/defaultの定義/}).click();
  await expect(panel.getByText("受注は顧客に属します。", { exact: true }).last()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await panel.screenshot({ path: testInfo.outputPath("ontology-evidence-conflicts.png") });
});

test("型付き AI 構築から変更解析・独立レビュー・検証・Profile 公開へ進める", async ({ page }, testInfo) => {
  const state = await mockApi(page);
  let bundle = { ...typedBundle("default"), notes_ja: "", validation_report: {} as Record<string, unknown> };
  let head = "";
  let publishCount = 0;
  await page.route("**/api/nl2sql/profiles/default/ontology-results", route => fulfillJson(route, { results: state.jobPolls >= 2 ? [bundle] : [] }));
  await page.route("**/api/nl2sql/profiles/default/ontology-results/bundle-default/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/workspace")) return fulfillJson(route, { bundle, artifacts: { markdown: "# default の業務定義", mermaid: "graph LR", manifest: "同じ版の hash", graph_json: JSON.stringify({ version_id: bundle.id, nodes: bundle.definitions.map(d => ({ id: d.id, name_ja: d.name_ja, kind: d.kind })), edges: [] }) }, head: { release_id: head, display_version: 2, etag: "head-etag" } });
    expect(route.request().headers()["if-match"]).toBe(`"${bundle.etag}"`);
    if (path.endsWith("/analyze")) return fulfillJson(route, { id: "change-1", base_etag: bundle.etag, before: bundle.definitions, after: [{ ...bundle.definitions[0], name_ja: "顧客契約" }] });
    if (path.endsWith("/apply")) bundle = { ...bundle, etag: `${bundle.etag}-edit`, definitions: bundle.definitions.map((d, i) => i ? d : { ...d, name_ja: "顧客契約" }) };
    if (path.endsWith("/review")) bundle = { ...bundle, etag: `${bundle.etag}-review`, definitions: bundle.definitions.map(d => ({ ...d, review_status: "reviewed" })) };
    if (path.endsWith("/validate")) bundle = { ...bundle, etag: `${bundle.etag}-validate`, validation_report: { errors: 0, kind: "static", instance_count: 0, instance_status: "not_run" } };
    if (path.endsWith("/publish")) { expect(route.request().postDataJSON().confirmed).toBe(true); expect(route.request().headers()["idempotency-key"]).toBeTruthy(); publishCount++; head = "release-default"; bundle = { ...bundle, status: "published", etag: "published-etag" }; }
    return fulfillJson(route, bundle);
  });
  await page.goto("/ontology-build?profile=default");
  await loadOntologyBuildWorkspace(page);
  await page.getByLabel("業務説明(自然言語)").fill("資料の定義と契約を抽出してください。");
  await page.getByRole("button", { name: "AI 構築を実行", exact: true }).click();
  const panel = page.getByTestId("ontology-typed-results");
  await expect(panel.getByRole("tab", { name: "概要", exact: true })).toBeVisible();
  await expect(page.getByTestId("ontology-build-markdown")).toBeHidden();
  await panel.getByRole("tab", { name: "レビュー・公開", exact: true }).click();
  const publish = panel.getByRole("button", { name: "この版を公開", exact: true });
  await expect(publish).toBeDisabled();
  await panel.getByLabel("変更したい業務説明").fill("顧客契約という業務名に変更する。");
  await panel.getByRole("button", { name: "変更を解析", exact: true }).click();
  await expect(panel.getByRole("cell",{name:"顧客契約",exact:true}).last()).toBeVisible();
  expect(bundle.definitions[0].name_ja).toBe("defaultの定義");
  await panel.getByRole("button", { name: "変更を適用", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
  await expect.poll(() => bundle.definitions[0].name_ja).toBe("顧客契約");
  await panel.getByRole("button", { name: "定義をレビュー済みにする", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
  await panel.getByRole("button", { name: "定義を検証", exact: true }).click();
  await expect(publish).toBeEnabled();
  await page.reload();
  await loadOntologyBuildWorkspace(page);
  await expect(panel.getByRole("tab", { name: "レビュー・公開", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(panel.getByLabel("変更したい業務説明")).toHaveValue("顧客契約という業務名に変更する。");
  expect(publishCount).toBe(0);
  await publish.press("Enter");
  await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
  await expect.poll(() => publishCount).toBe(1);
  await expect(publish).toBeDisabled();
  await panel.getByRole("tab", { name: "概要", exact: true }).click();
  await expect(panel.getByText("現在の公開版: v2", {exact:true})).toBeVisible();
  await expect(panel.getByRole("region", { name: "同一版の概念グラフ（Concept Graph）", exact: true })).toBeVisible();
  await panel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("ontology-profile-published.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("ontology-profile-published.png") });
});

test("公開能力を明示的に設定し、プレビュー確認・実行・再読込を Profile 内で利用する", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [{ ...typedBundle("default"), status: "published" }] }));
  let bound = false, executions = 0, previews = 0;
  const definition = { id: "action-1", kind: "action_type", name_ja: "承認", api_name: "approve", description_ja: "下書きの受注を承認", parameters: [{ api_name: "status", name_ja: "承認状態", data_type: "string", required: true }] };
  const result = { id: "execution-1", release_id: "release-default", at: "2026-09-11T05:00:00Z", status: "succeeded", before: { "Order.status": "DRAFT" }, after: { "Order.status": "CONFIRMED" } };
  await page.route("**/api/nl2sql/profiles/default/ontology-capabilities**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("ontology-capabilities")) return fulfillJson(route, { release_id: "release-default", implementations: { functions: [], actions: [] }, capabilities: [{ definition, target_parameters: [{ api_name: "Order.id", name_ja: "受注番号", data_type: "integer", required: true }], status: bound ? "available" : "configuration_required", reason_ja: bound ? "" : "実装 binding を設定してください。", binding: bound ? { kind: "property_update", etag: "binding-etag", expression_sql: "", implementation_key: "", state_requirements: [{ property: "Order.status", value: "DRAFT" }], reviewed_rules_ja: "下書きのみ承認", enabled: true } : null }] });
    if (path.endsWith("/binding")) { expect(route.request().headers()["if-match"]).toBe("*"); bound = true; return fulfillJson(route, { etag: "binding-etag" }); }
    if (path.endsWith("/preview")) { previews++; const input = route.request().postDataJSON(); expect(input.target).toEqual({ "Order.id": 1 }); return fulfillJson(route, { id: `preview-${previews}`, before: { "Order.status": "DRAFT" }, after: { "Order.status": input.parameters.status }, expires_at: "2026-09-11T05:10:00Z" }); }
    if (path.endsWith("/execute")) { expect(route.request().postDataJSON().confirmed).toBe(true); expect(route.request().headers()["idempotency-key"]).toBeTruthy(); executions++; return fulfillJson(route, result); }
    return fulfillJson(route, result);
  });
  await page.goto("/ontology-build?profile=default"); await loadOntologyBuildWorkspace(page);
  const region = page.getByRole("region", { name: "公開能力（Published Capabilities）", exact: true });
  await region.getByRole("list").getByRole("button").first().click();
  await expect(region.getByText("設定が必要", { exact: true }).last()).toBeVisible();
  await region.getByLabel("確認済み業務条件（Reviewed Business Rules）").fill("下書きのみ承認");
  await region.getByLabel("状態条件（State Requirements）").fill('[{"property":"Order.status","value":"DRAFT"}]');
  await region.getByRole("button", { name: "実装を設定（Bind Implementation）", exact: true }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
  await expect(region.getByText("利用可能", { exact: true }).last()).toBeVisible();
  await region.getByLabel(/承認状態 \(status\)/).fill("CONFIRMED");
  await region.getByLabel(/受注番号 \(Order.id\)/).fill("1");
  await region.getByRole("button", { name: "変更をプレビュー", exact: true }).click();
  await expect(region.getByRole("cell", { name: "CONFIRMED", exact: true })).toBeVisible();
  expect(executions).toBe(0);
  await region.getByLabel(/承認状態 \(status\)/).fill("APPROVED");
  await expect(region.getByRole("button", { name: "確認して実行", exact: true })).toHaveCount(0);
  await region.getByLabel(/承認状態 \(status\)/).fill("CONFIRMED");
  await region.getByRole("button", { name: "変更をプレビュー", exact: true }).click();
  await page.reload(); await loadOntologyBuildWorkspace(page);
  await expect(region.getByLabel(/承認状態 \(status\)/)).toHaveValue("CONFIRMED");
  await expect(region.getByRole("button", { name: "確認して実行", exact: true })).toHaveCount(0);
  expect(executions).toBe(0);
  await region.getByRole("button", { name: "変更をプレビュー", exact: true }).click();
  const execute = region.getByRole("button", { name: "確認して実行", exact: true });
  await execute.focus(); await page.keyboard.press("Enter");
  await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
  await expect.poll(() => executions).toBe(1);
  await expect(region.getByText(/前回の実行結果/)).toBeVisible();
  await page.reload(); await loadOntologyBuildWorkspace(page);
  await expect(region.getByText(/前回の実行結果/)).toBeVisible();
  expect(executions).toBe(1);
  await region.getByLabel(/承認状態 \(status\)/).fill("NEW");
  await expect(region.getByText("現在の入力は未実行です", { exact: true })).toBeVisible();
  const box = await region.boundingBox(); expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1);
  for (const button of await region.getByRole("button").all()) if (await button.isVisible()) await expectButtonLabelFits(button);
  await region.screenshot({ path: testInfo.outputPath("ontology-capabilities.png") });
});

test("公開能力の空・読込・取得失敗を表示し再試行できる", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [] }));
  let mode = "loading";
  const gate = createRequestGate();
  await page.route("**/api/nl2sql/profiles/default/ontology-capabilities", async route => { if (mode === "loading") await gate.promise; if (mode === "error") return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "fixture unavailable" }) }); return fulfillJson(route, { release_id: "", capabilities: [], implementations: { functions: [], actions: [] } }); });
  await page.goto("/ontology-build?profile=default"); await loadOntologyBuildWorkspace(page);
  const region = page.getByRole("region", { name: "公開能力（Published Capabilities）", exact: true });
  await expect(region.getByText("能力を読み込み中", { exact: true }).last()).toBeVisible();
  mode = "empty"; gate.release();
  await expect(region.getByText("まだ公開されていません", { exact: true })).toBeVisible();
  mode = "error"; await region.getByRole("button", { name: "最新情報を取得", exact: true }).click();
  await expect(region.getByRole("alert")).toBeVisible();
  mode = "empty"; await region.getByRole("button", { name: "最新情報を取得", exact: true }).click();
  await expect(region.getByRole("alert")).toHaveCount(0);
  await region.getByRole("button", {name:"レビュー・公開を確認",exact:true}).click();
  await expect(page.getByTestId("ontology-typed-results")).toBeFocused();
});

test("公開関数の型付き入力で呼出し、過去結果を保持して再実行は確認する", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [{ ...typedBundle("default"), status: "published" }] }));
  let calls = 0;
  await page.route("**/api/nl2sql/profiles/default/ontology-capabilities**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("ontology-capabilities")) return fulfillJson(route, { release_id: "release-default", implementations: { functions: [], actions: [] }, capabilities: [{ definition: { id: "fn", kind: "function", name_ja: "金額計算", api_name: "calculate", description_ja: "定義された計算を実行", parameters: [{ api_name: "amount", name_ja: "金額", data_type: "number", required: true }] }, target_parameters: [], status: "available", reason_ja: "", binding: { etag: "binding", kind: "expression", expression_sql: ":amount * 2", implementation_key: "", state_requirements: [], reviewed_rules_ja: "", enabled: true } }] });
    if (path.endsWith("/invoke")) { calls++; expect(route.request().postDataJSON().parameters).toEqual({ amount: 21 }); }
    return fulfillJson(route, { id: "function-call", release_id: "release-default", at: "2026-09-11T01:00:00Z", status: "succeeded", result: 42 });
  });
  await page.goto("/ontology-build?profile=default"); await loadOntologyBuildWorkspace(page);
  const region = page.getByRole("region", { name: "公開能力（Published Capabilities）", exact: true });
  await region.getByRole("list").getByRole("button").first().click();
  await region.getByLabel(/金額 \(amount\)/).fill("21");
  await region.getByRole("button", { name: "関数を呼出（Invoke Function）", exact: true }).click();
  expect(calls).toBe(0);
  await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
  await expect(region.getByText("42",{exact:true})).toBeVisible();
  await page.reload(); await loadOntologyBuildWorkspace(page);
  await expect(region.getByLabel(/金額 \(amount\)/)).toHaveValue("21");
  await expect(region.getByText("42",{exact:true})).toBeVisible();
  expect(calls).toBe(1);
});

for (const committedBeforeDisconnect of [true, false]) {
  test(`操作の応答喪失後に元の結果を照会し同じ確認だけを再試行する (${committedBeforeDisconnect ? "commit 済み" : "未実行"})`, async ({ page }, testInfo) => {
    await mockApi(page);
    await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [{ ...typedBundle("default"), status: "published" }] }));
    let calls = 0, mutations = 0, previews = 0, lookupAvailable = false;
    let originalKey = "";
    const result = { id: "recovered-execution", release_id: "release-default", at: "2026-09-11T05:00:00Z", status: "succeeded", before: { count: 0 }, after: { count: 1 } };
    await page.route("**/api/nl2sql/profiles/default/ontology-capabilities**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("ontology-capabilities")) return fulfillJson(route, { release_id: "release-default", implementations: { functions: [], actions: [] }, capabilities: [{ definition: { id: "increment", kind: "action_type", name_ja: "加算", api_name: "increment", description_ja: "一度だけ加算する", parameters: [] }, target_parameters: [], status: "available", reason_ja: "", binding: { kind: "backend", etag: "binding", expression_sql: "", implementation_key: "increment", state_requirements: [], reviewed_rules_ja: "一度だけ加算", enabled: true } }] });
      if (path.endsWith("/preview")) { previews++; return fulfillJson(route, { id: "original-preview", before: { count: 0 }, after: { count: 1 }, expires_at: "2026-09-11T05:10:00Z" }); }
      if (path.endsWith("/execute")) {
        calls++;
        expect(route.request().postDataJSON()).toEqual({ preview_id: "original-preview", confirmed: true });
        const key = route.request().headers()["idempotency-key"];
        if (calls === 1) { originalKey = key; if (committedBeforeDisconnect) mutations++; return route.abort("connectionreset"); }
        expect(key).toBe(originalKey);
        if (!mutations) mutations++;
        return fulfillJson(route, result);
      }
      if (path.endsWith("/outcome")) {
        expect(path).toContain("/previews/original-preview/outcome");
        if (!lookupAvailable) return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "照会は一時的に利用できません" }) });
        return fulfillJson(route, mutations ? { status: "succeeded", execution: result } : { status: "unresolved" });
      }
      return fulfillJson(route, result);
    });
    await page.goto("/ontology-build?profile=default"); await loadOntologyBuildWorkspace(page);
      const region = page.getByRole("region", { name: "公開能力（Published Capabilities）", exact: true });
    await region.getByRole("list").getByRole("button").first().click();
  const preview = region.getByRole("button", { name: "変更をプレビュー", exact: true });
    await preview.click();
    await region.getByRole("button", { name: "確認して実行", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
    await expect(region.getByText(/元の操作の結果を確認中/)).toBeVisible();
    await expect(preview).toBeDisabled();
    const retry = region.getByRole("button", { name: "元の操作を再試行", exact: true });
    await expect(retry).toBeDisabled();
    await page.reload(); await loadOntologyBuildWorkspace(page);
    await expect(region.getByText(/元の操作の結果を確認中/)).toBeVisible();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    expect(calls).toBe(1); expect(previews).toBe(1);
    lookupAvailable = true;
    await region.getByRole("button", { name: "元の結果を照会", exact: true }).click();
    if (!committedBeforeDisconnect) {
      await expect(retry).toBeEnabled();
      for (const button of await region.getByRole("button").all()) if (await button.isVisible()) await expectButtonLabelFits(button);
      await region.screenshot({ path: testInfo.outputPath("ontology-original-action-recovery.png") });
      await retry.focus(); await page.keyboard.press("Enter");
      expect(calls).toBe(1);
      await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
    }
    await expect(region.getByText(/前回の実行結果/)).toBeVisible();
    await expect(region.getByText("完了", {exact:true})).toBeVisible();
    expect(mutations).toBe(1); expect(calls).toBe(committedBeforeDisconnect ? 1 : 2); expect(previews).toBe(1);
    await expect(region.getByText(/元の操作の結果を確認中/)).toHaveCount(0);
  });
}

for (const lostFailureResponse of [false, true]) {
  test(`確定 rollback 後に入力を修正し新しいプレビューから実行できる (${lostFailureResponse ? "失敗応答喪失" : "確定失敗"})`, async ({ page }, testInfo) => {
    await mockApi(page);
    await page.route("**/api/nl2sql/profiles/*/ontology-results", route => fulfillJson(route, { results: [{ ...typedBundle("default"), status: "published" }] }));
    let previews = 0, calls = 0, mutations = 0, outcomeAvailable = !lostFailureResponse;
    const failed = { id: "failed-execution", release_id: "release-default", at: "2026-09-12T01:00:00Z", status: "failed", changes_applied: false, message_ja: "操作は失敗し、変更を取り消しました。入力を確認して再プレビューしてください。" };
    const succeeded = { id: "new-execution", release_id: "release-default", at: "2026-09-12T01:02:00Z", status: "succeeded", after: { status: "APPROVED" } };
    await page.route("**/api/nl2sql/profiles/default/ontology-capabilities**", route => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("ontology-capabilities")) return fulfillJson(route, { release_id: "release-default", implementations: { functions: [], actions: [] }, capabilities: [{ definition: { id: "approve", kind: "action_type", name_ja: "承認", api_name: "approve", parameters: [{ api_name: "status", name_ja: "状態", data_type: "string", required: true }] }, target_parameters: [], status: "available", reason_ja: "", binding: { kind: "backend", etag: "binding", expression_sql: "", implementation_key: "trusted.approve", state_requirements: [], reviewed_rules_ja: "条件に合わない入力は取消", enabled: true } }] });
      if (path.endsWith("/preview")) { previews++; expect(route.request().postDataJSON().parameters.status).toBe(previews === 1 ? "CONFIRMED" : "APPROVED"); return fulfillJson(route, { id: `preview-${previews}`, before: { status: "DRAFT" }, after: { status: route.request().postDataJSON().parameters.status }, expires_at: "2026-09-12T01:10:00Z" }); }
      if (path.endsWith("/execute")) {
        calls++; expect(route.request().postDataJSON()).toEqual({ preview_id: `preview-${calls}`, confirmed: true });
        if (calls === 1) return lostFailureResponse ? route.abort("connectionreset") : fulfillJson(route, failed);
        mutations++; return fulfillJson(route, succeeded);
      }
      if (path.endsWith("/outcome")) return outcomeAvailable ? fulfillJson(route, { status: "failed", execution: failed }) : route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "fixture unavailable" }) });
      return fulfillJson(route, path.endsWith("failed-execution") ? failed : succeeded);
    });
    await page.goto("/ontology-build?profile=default"); await loadOntologyBuildWorkspace(page);
      const region = page.getByRole("region", { name: "公開能力（Published Capabilities）", exact: true });
    await region.getByRole("list").getByRole("button").first().click();
  const preview = region.getByRole("button", { name: "変更をプレビュー", exact: true });
    await region.getByLabel(/状態 \(status\)/).fill("CONFIRMED"); await preview.click();
    await region.getByRole("button", { name: "確認して実行", exact: true }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
    if (lostFailureResponse) {
      await expect(region.getByText(/元の操作の結果を確認中/)).toBeVisible(); await expect(preview).toBeDisabled();
      await page.reload(); await loadOntologyBuildWorkspace(page); expect(calls).toBe(1);
      const checkOriginal = region.getByRole("button", { name: "元の結果を照会", exact: true });
      await expect(checkOriginal).toBeEnabled();
      outcomeAvailable = true;
      await checkOriginal.click();
    }
    await expect(region.getByRole("alert")).toContainText("変更を取り消しました");
    await expect(preview).toBeEnabled(); await expect(region.getByText(/元の操作の結果を確認中/)).toHaveCount(0);
    expect(mutations).toBe(0);
    await region.screenshot({ path: testInfo.outputPath(`ontology-rollback-${lostFailureResponse}.png`) });
    await page.reload(); await loadOntologyBuildWorkspace(page); await expect(preview).toBeEnabled(); expect(calls).toBe(1);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await region.getByLabel(/状態 \(status\)/).fill("APPROVED"); await preview.click();
    await region.getByRole("button", { name: "確認して実行", exact: true }).focus(); await page.keyboard.press("Enter");
    expect(calls).toBe(1); await page.getByRole("alertdialog").getByRole("button", { name: "確認して実行", exact: true }).click();
    await expect(region.getByText("APPROVED", {exact:true})).toBeVisible(); expect(calls).toBe(2); expect(mutations).toBe(1); expect(previews).toBe(2);
    await expect(region.getByRole("alert")).toHaveCount(0);
  });
}

test("構築結果の五タブ・版保持・マッピング詳細を共通操作とダークテーマで確認する", async ({page},testInfo) => {
  await mockApi(page);
  const current = {...typedBundle("default"),id:"opaque-current",display_version:8};
  const old = {...typedBundle("default"),id:"opaque-old",display_version:3,definitions:[{...typedBundle("default").definitions[0],name_ja:"旧受注",mappings:[{owner:"APP",object_name:"ORDERS",column_name:"ID",expression_sql:""}]}]};
  let results = [current,old], failed = false;
  await page.route("**/api/nl2sql/profiles/default/ontology-results",route => failed ? route.fulfill({status:503,body:"{}"}) : fulfillJson(route,{results}));
  await page.route("**/ontology-results/*/workspace",route => fulfillJson(route,{bundle:old,artifacts:{},head:{release_id:"opaque-release",display_version:8,etag:"head"},releases:[]}));
  await page.route("**/api/nl2sql/profiles/default/ontology-capabilities",route => fulfillJson(route,{release_id:"opaque-release",display_version:8,capabilities:[],implementations:{functions:[],actions:[]}}));
  await page.goto("/ontology-build?profile=default"); await loadOntologyBuildWorkspace(page);
  const panel=page.getByTestId("ontology-typed-results"), abilities=page.getByRole("region",{name:"公開能力（Published Capabilities）",exact:true});
  await expect(panel.getByRole("tab")).toHaveCount(5);
  const first=panel.getByRole("tab",{name:"概要",exact:true});
  await first.focus(); await page.keyboard.press("End"); await expect(panel.getByRole("tab",{name:"レビュー・公開",exact:true})).toBeFocused();
  await page.keyboard.press("Home"); await expect(first).toBeFocused(); await page.keyboard.press("ArrowRight"); await expect(panel.getByRole("tab",{name:"モデル",exact:true})).toBeFocused();
  const versions=panel.getByRole("combobox",{name:"業務定義の版",exact:true});
  await versions.click(); await page.keyboard.press("Escape"); await expect(versions).toBeFocused();
  await versions.click(); await panel.getByRole("option",{name:/v3 ·/}).click();
  await expect(versions).toContainText("v3"); await expect(abilities).toContainText("現在の公開版: v8");
  await panel.getByRole("tab",{name:"マッピング（Mapping）",exact:true}).click();
  await panel.getByRole("button",{name:/旧受注/}).click();
  await expect(panel.getByTestId("ontology-definition-detail")).toContainText("旧受注");
  await panel.getByRole("tab",{name:"レビュー・公開",exact:true}).click();
  await panel.getByLabel("変更したい業務説明").fill("保持する草稿");
  failed=true; await panel.getByRole("button",{name:"最新情報を取得",exact:true}).click();
  await expect(panel.getByRole("alert")).toContainText("前回の情報"); await expect(panel.getByLabel("変更したい業務説明")).toHaveValue("保持する草稿");
  failed=false; await panel.getByRole("button",{name:"最新情報を取得",exact:true}).click();
  await page.reload(); await loadOntologyBuildWorkspace(page);
  await expect(versions).toContainText("v3"); await expect(panel.getByLabel("変更したい業務説明")).toHaveValue("保持する草稿");
  await page.emulateMedia({colorScheme:"dark"}); await page.evaluate(() => document.documentElement.classList.add("dark"));
  await panel.getByRole("tab",{name:"モデル",exact:true}).click();
  await panel.screenshot({path:testInfo.outputPath("ontology-results-dark.png")});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  results=[current]; await panel.getByRole("button",{name:"最新情報を取得",exact:true}).click();
  await expect(panel.getByText("選択した版が見つかりません。版を選び直してください。",{exact:true})).toBeVisible(); await expect(panel.getByRole("tab")).toHaveCount(0);
  await expect(abilities).toContainText("現在の公開版: v8");
});

test("定義と実データの検証を区別し JSON エラーと公開の阻害事項を案内する",async ({page},testInfo) => {
  await mockApi(page); let calls=0;
  const bundle={...typedBundle("default"),validation_report:{kind:"static",checked_at:"2026-09-12T01:00:00Z",errors:0,data_validation:{kind:"sampled_data",checked_at:"2026-09-12T02:00:00Z",instance_count:12,sample_limit:50,instance_coverage:1,errors:0}}};
  await page.route("**/api/nl2sql/profiles/default/ontology-results",route => fulfillJson(route,{results:[bundle]}));
  await page.route("**/ontology-results/*/workspace",route => fulfillJson(route,{bundle,artifacts:{},head:{release_id:"",etag:""}}));
  await page.route("**/ontology-results/*/validation-jobs",route => {calls++;return fulfillJson(route,{job_id:"data-check",status:"running"});});
  await page.route("**/ontology-validation-jobs/*",route => fulfillJson(route,{job_id:"data-check",status:"succeeded"}));
  await page.goto("/ontology-build?profile=default");await loadOntologyBuildWorkspace(page);
  const panel=page.getByTestId("ontology-typed-results");await panel.getByRole("tab",{name:"検証",exact:true}).click();
  await expect(panel.getByRole("heading",{name:"定義の検証",exact:true})).toBeVisible();await expect(panel.getByRole("heading",{name:"実データの検証",exact:true})).toBeVisible();
  await expect(panel.getByText(/検証件数: 12 件/)).toBeVisible();await expect(panel.getByText(/実データは未検証/)).toHaveCount(0);
  await panel.getByText("独立した業務問題（Acceptance Cases）",{exact:true}).click();
  const input=panel.getByLabel("受入テスト");await input.fill("{");
  await panel.getByRole("button",{name:"実データを検証",exact:true}).click();
  await expect(input).toHaveAttribute("aria-invalid","true");await expect(page.getByRole("alertdialog")).toHaveCount(0);expect(calls).toBe(0);
  await input.fill("[]");await panel.getByRole("button",{name:"実データを検証",exact:true}).click();
  await expect(page.getByRole("alertdialog")).toContainText("v2");await page.getByRole("alertdialog").getByRole("button",{name:"確認して実行",exact:true}).click();
  await expect.poll(()=>calls).toBe(1);await panel.screenshot({path:testInfo.outputPath("ontology-validation.png")});
  await panel.getByRole("tab",{name:"レビュー・公開",exact:true}).click();await expect(panel.getByText("未レビューの定義が 6 件あります。",{exact:true})).toBeVisible();await expect(panel.getByRole("button",{name:"この版を公開",exact:true})).toBeDisabled();
});

test("構築結果がなくても公開能力を利用し必須入力と実行時の版を保持する",async ({page},testInfo)=>{
  await mockApi(page);let calls=0;let displayVersion=9;
  await page.route("**/api/nl2sql/profiles/default/ontology-results",route=>fulfillJson(route,{results:[]}));
  await page.route("**/api/nl2sql/profiles/default/ontology-capabilities**",route=>{
    if(new URL(route.request().url()).pathname.endsWith("ontology-capabilities"))return fulfillJson(route,{release_id:"release",display_version:displayVersion,implementations:{functions:[],actions:[]},capabilities:[{definition:{id:"fn",kind:"function",name_ja:"金額計算",api_name:"calculate",parameters:[{api_name:"amount",name_ja:"金額",data_type:"integer",required:true}]},target_parameters:[],status:"available",reason_ja:"",binding:{etag:"binding",kind:"expression",expression_sql:":amount*2",implementation_key:"",state_requirements:[],reviewed_rules_ja:"",enabled:true}}]});
    if(route.request().method()==="POST")calls++;
    return fulfillJson(route,{id:"execution",release_id:"historical",display_version:4,at:"2026-09-11T00:00:00Z",status:"succeeded",result:42});
  });
  await page.goto("/ontology-build?profile=default");await loadOntologyBuildWorkspace(page);
  const region=page.getByRole("region",{name:"公開能力（Published Capabilities）",exact:true});await region.getByRole("list").getByRole("button").click();
  await region.getByRole("button",{name:"関数を呼出（Invoke Function）",exact:true}).click();
  const input=region.getByLabel(/金額 \(amount\)/);await expect(input).toHaveAttribute("aria-invalid","true");expect(calls).toBe(0);
  await input.fill("1.5");await region.getByRole("button",{name:"関数を呼出（Invoke Function）",exact:true}).click();await expect(region.getByText("整数を入力してください。",{exact:true})).toBeVisible();
  await input.fill("21");await region.getByRole("button",{name:"関数を呼出（Invoke Function）",exact:true}).click();await expect(page.getByRole("alertdialog")).toContainText("v9");await page.getByRole("alertdialog").getByRole("button",{name:"確認して実行",exact:true}).click();
  await expect(region).toContainText("実行時の公開版: v4");displayVersion=10;await region.getByRole("button",{name:"最新情報を取得",exact:true}).click();await expect(region).toContainText("現在の公開版: v10");await expect(region).toContainText("実行時の公開版: v4");
  await page.reload();await loadOntologyBuildWorkspace(page);await expect(input).toHaveValue("21");expect(calls).toBe(1);
  await page.emulateMedia({colorScheme:"dark"});await page.evaluate(()=>document.documentElement.classList.add("dark"));await region.screenshot({path:testInfo.outputPath("ontology-capabilities-dark.png")});
});

test("構築結果の草稿は往復ナビで保持し DB とユーザーを越えて流用しない",async({page})=>{
  await mockApi(page);let database="database-a", user={...systemAdminMe};
  await page.route("**/api/auth/me",route=>fulfillJson(route,user));
  await page.route("**/api/ready/database",route=>fulfillJson(route,{status:"ok",check:"ok",detail:null,context_id:database}));
  await page.route("**/api/nl2sql/profiles/default/ontology-results",route=>fulfillJson(route,{results:[typedBundle("default")]}));
  await page.goto("/ontology-build?profile=default");await loadOntologyBuildWorkspace(page);
  const panel=page.getByTestId("ontology-typed-results"), review=panel.getByRole("tab",{name:"レビュー・公開",exact:true}), input=panel.getByLabel("変更したい業務説明");
  await review.click();await input.fill("最初の利用者と DB の草稿");
  await page.goto("/history");await page.goBack();await loadOntologyBuildWorkspace(page);await expect(input).toHaveValue("最初の利用者と DB の草稿");
  await page.goForward();await page.goBack();await loadOntologyBuildWorkspace(page);await expect(input).toHaveValue("最初の利用者と DB の草稿");
  database="database-b";await page.reload();await loadOntologyBuildWorkspace(page);await review.click();await expect(input).toHaveValue("");await input.fill("別 DB の草稿");
  database="database-a";await page.reload();await loadOntologyBuildWorkspace(page);await expect(input).toHaveValue("最初の利用者と DB の草稿");
  user={...systemAdminMe,user_uuid:"other-ontology-user",login_user_id:"OTHER"};await page.reload();await loadOntologyBuildWorkspace(page);await review.click();await expect(input).toHaveValue("");
});
