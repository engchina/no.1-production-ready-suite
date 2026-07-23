import { expect, test, type Page, type Route } from "@playwright/test";
import { mockDatabaseGateReady } from "./_helpers/database-gate";

test.beforeEach(async ({ page }) => mockDatabaseGateReady(page));

async function fulfillJson(route: Route, data: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ data }),
  });
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
                  at: "2026-07-12T00:00:09Z",
                  message_ja: "構築が完了しました(提案 2 件、警告 1 件)。",
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
        { name: "schema_context", status: stepStatus, detail_ja: "表・ビュー 2 件、列 5 件", ...stepTimes },
        { name: "schema_naming", status: stepStatus, detail_ja: "", ...stepTimes },
        { name: "text_extraction", status: stepStatus, detail_ja: "", ...stepTimes },
        { name: "proposal_registration", status: stepStatus, detail_ja: "", ...stepTimes },
      ],
      events,
      proposal_ids: proposalIds,
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
    idempotencySeen: false,
    sourceFilesSeen: false,
    ontologyDraftPayload: null as Record<string, unknown> | null,
    materialized: true,
    stale: false,
    materializeCalls: 0,
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
    if (route.request().method() === "PATCH") {
      state.ontologyDraftPayload = route.request().postDataJSON() as Record<string, unknown>;
    }
    return fulfillJson(route, {
      ...ontologyView,
      materialized: state.materialized,
      stale: state.stale,
    });
  });
  await page.route("**/api/nl2sql/profiles/*/ontology-view/materialize", async (route) => {
    state.materializeCalls += 1;
    state.materialized = true;
    state.stale = false;
    await fulfillJson(route, { ...ontologyView, materialized: true, stale: false });
  });
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfillJson(route, {
      revisions: [ontologyView.ontology_graph.revision],
      active_revision_id: ontologyView.ontology_graph.revision.id,
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-view/mermaid", (route) =>
    fulfillJson(route, {
      profile_id: "default",
      ontology_revision_id: "revision-1",
      mermaid: 'erDiagram\n    "APP.ORDERS" }o--|| "APP.CUSTOMERS" : "顧客を参照"',
    })
  );
  await page.route("**/api/nl2sql/profiles/*/ontology-build", async (route) => {
    state.startPayloadSeen = true;
    state.idempotencySeen = Boolean(route.request().headers()["idempotency-key"]);
    const postData = route.request().postData() ?? "";
    state.sourceFilesSeen = postData.includes("rules.md") && postData.includes("terms.csv");
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

test("AI オントロジー構築の実行 → 進捗 → 提案承認 → 公開の導線が機能する", async ({ page }, testInfo) => {
  const state = await mockApi(page);
  await page.goto("/ontology-build?profile=default");

  const section = page.getByTestId("profile-ontology-build");
  await expect(section.getByRole("heading", { name: "オントロジー構築" })).toBeVisible();

  if (testInfo.project.name === "desktop") {
    const buildSplit = page.getByTestId("fixed-split-pane-ontology-build-workspace");
    await expect(buildSplit).toHaveAttribute("data-split-layout", "split");
    const businessTextareaBox = await section
      .getByLabel("業務説明(自然言語)")
      .boundingBox();
    const sourcePanelBox = await section
      .getByTestId("ontology-build-source-panel")
      .boundingBox();
    const leftPaneBox = await page
      .getByTestId("fixed-split-pane-ontology-build-workspace-left")
      .boundingBox();
    expect(businessTextareaBox).not.toBeNull();
    expect(sourcePanelBox).not.toBeNull();
    expect(leftPaneBox).not.toBeNull();
    expect(sourcePanelBox?.y ?? 0).toBeGreaterThan(businessTextareaBox?.y ?? 0);
    expect(businessTextareaBox?.x ?? 0).toBeGreaterThanOrEqual(leftPaneBox?.x ?? 0);
    expect((sourcePanelBox?.x ?? 0) + (sourcePanelBox?.width ?? 0)).toBeLessThanOrEqual(
      (leftPaneBox?.x ?? 0) + (leftPaneBox?.width ?? 0) + 1
    );
  }

  // 空状態の提案リスト
  await expect(section.getByText("レビュー対象の提案はありません", { exact: false })).toBeVisible();

  // 実行 → ステップ進捗 → 完了
  await section
    .getByLabel("業務説明(自然言語)")
    .fill("受注は顧客に紐づく。売上は受注金額の合計。");
  await page.getByTestId("ontology-build-source-files").setInputFiles([
    { name: "rules.md", mimeType: "text/markdown", buffer: Buffer.from("# 受注ルール") },
    {
      name: "terms.csv",
      mimeType: "text/csv",
      buffer: Buffer.from("用語,説明\n受注,顧客からの注文"),
    },
  ]);
  await expect(section.getByText("rules.md")).toBeVisible();
  await expect(section.getByText("terms.csv")).toBeVisible();
  await section.getByRole("button", { name: "AI 構築を実行" }).click();
  const steps = page.getByTestId("ontology-build-steps");
  await expect(steps.getByText("スキーマ情報の準備")).toBeVisible();
  await expect(steps.getByText("業務エンティティ命名")).toBeVisible();
  // 完了は工程ステッパーの「完了」バッジ(永続)で判定する。完了の“瞬間”通知は toast のため
  // section スコープには残らない(spec §9: 完了は状態表示が担い、瞬間だけ toast)。
  await expect(steps.getByText("完了").first()).toBeVisible({ timeout: 15000 });
  expect(state.startPayloadSeen).toBe(true);
  expect(state.idempotencySeen).toBe(true);
  expect(state.sourceFilesSeen).toBe(true);
  // アクティビティタイムライン(時刻付きイベント)が表示される
  const timeline = page.getByTestId("ontology-build-timeline");
  await expect(timeline.getByText("スキーマ情報を準備しました", { exact: false })).toBeVisible();
  await expect(timeline.getByText("構築が完了しました", { exact: false })).toBeVisible();
  await expect(steps.getByText(/経過 \d+ 秒/)).toBeVisible();
  // スコープ外候補の警告が確認できる
  await steps.locator("summary").filter({ hasText: "警告" }).click();
  await expect(steps.getByText("APP.SECRET", { exact: false })).toBeVisible();

  // 提案レビュー → 承認(2 件以上のときは「すべて承認」も出る)
  const proposalList = page.getByTestId("ontology-build-proposals");
  await expect(proposalList.getByText("業務エンティティ命名: 受注")).toBeVisible();
  await expect(proposalList.getByText("業務関係の提案: 顧客を参照")).toBeVisible();
  await expect(
    proposalList.getByRole("button", { name: "すべて承認 (2 件)" })
  ).toBeVisible();
  await proposalList
    .getByRole("button", { name: "提案「業務関係の提案: 顧客を参照」を承認する" })
    .click();
  await expect(proposalList.getByText("承認済み").first()).toBeVisible();
  // 残り 1 件になると「すべて承認」は消える
  await expect(proposalList.getByRole("button", { name: /すべて承認/ })).toHaveCount(0);

  // 公開
  await expect(proposalList.getByText("rev v4", { exact: false })).toBeVisible();
  await proposalList.getByRole("button", { name: "Ontology を公開" }).click();
  // 公開完了の“瞬間”は toast(document.body 直下)で通知する(spec §9)。section 外なので page スコープで確認。
  await expect(page.getByText("Ontology を公開しました。")).toBeVisible();
  expect(state.published).toBe(true);
  await expect(page.getByTestId("ontology-publish-status")).toContainText("完了");

  // 「業務モデル」tab は廃止され、編集領域が同じページに続く
  const modelSection = page.locator('section[aria-label="物理・業務モデル編集"]');
  await expect(modelSection).toBeVisible();
  await expect(page.getByRole("tab")).toHaveCount(0);
  await modelSection.getByLabel("Inspector の編集対象").selectOption("node:table:APP:ORDERS");
  await modelSection.getByLabel("日本語の業務名").fill("受注明細");
  await modelSection.getByRole("button", { name: "Draft を保存" }).click();
  await expect(page.getByText("Draft を保存しました")).toBeVisible();
  expect(state.ontologyDraftPayload).toMatchObject({
    node_overrides: [{ node_id: "table:APP:ORDERS", business_name_ja: "受注明細" }],
  });

  // Mermaid はページ末尾の読み取り専用技術表現として取得する
  const mermaidPanel = page.getByTestId("ontology-mermaid-panel");
  await mermaidPanel.getByText("Mermaid ER コードを表示").click();
  await mermaidPanel.getByRole("button", { name: "コードを取得" }).click();
  await expect(page.getByTestId("ontology-build-mermaid")).toContainText("erDiagram");
  await expect(page.getByTestId("ontology-build-mermaid")).toContainText("APP.ORDERS");
  await expect(section).toBeVisible();

  // 横スクロールが発生しない
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(overflow).toBe(false);

  await mermaidPanel.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("ontology-build.png"), fullPage: true });
});

test("古い Ontology view をキーボードで明示的に再構築できる", async ({ page }) => {
  const state = await mockApi(page);
  state.stale = true;
  await page.goto("/ontology-build?profile=default");

  const lifecycle = page.getByTestId("ontology-view-lifecycle");
  await expect(lifecycle.getByText("再構築が必要", { exact: true })).toBeVisible();
  const rebuild = lifecycle.getByRole("button", { name: "Ontology view を再構築" });
  await rebuild.focus();
  await expect(rebuild).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(lifecycle.getByText("最新", { exact: true })).toBeVisible();
  expect(state.materializeCalls).toBe(1);
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(overflow).toBe(false);
});

test("送信直後にプレースホルダーが出て、すべて承認で一括承認できる", async ({ page }) => {
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

  // job 完了 → 2 件を「すべて承認」で一括処理
  const proposalList = page.getByTestId("ontology-build-proposals");
  const acceptAll = proposalList.getByRole("button", { name: "すべて承認 (2 件)" });
  await expect(acceptAll).toBeVisible({ timeout: 15000 });
  await acceptAll.click();
  await expect(proposalList.getByText("承認済み", { exact: true })).toHaveCount(2);
  await expect(proposalList.getByRole("button", { name: "承認", exact: true })).toHaveCount(0);
  await expect(proposalList.getByText("rev v4", { exact: false })).toBeVisible();
});

test("Profile と提案の初期読込では Skeleton を表示する", async ({ page }) => {
  await mockApi(page);
  let releaseProfiles: () => void = () => undefined;
  const profilesGate = new Promise<void>((resolve) => {
    releaseProfiles = resolve;
  });
  let releaseProposals: () => void = () => undefined;
  const proposalsGate = new Promise<void>((resolve) => {
    releaseProposals = resolve;
  });
  await page.unroute("**/api/nl2sql/profiles/search?*");
  await page.route("**/api/nl2sql/profiles/search?*", async (route) => {
    await profilesGate;
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
  await page.unroute("**/api/nl2sql/profiles/*/ontology-proposals");
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", async (route) => {
    await proposalsGate;
    await fulfillJson(route, { proposals: [] });
  });

  await page.goto("/ontology-build?profile=default");
  await expect(page.getByTestId("ontology-profile-compact-skeleton")).toBeVisible();
  releaseProfiles();
  await expect(page.getByTestId("ontology-build-profile-select")).toBeVisible();
  await expect(page.getByTestId("ontology-proposals-list-skeleton")).toBeVisible();
  releaseProposals();
  await expect(page.getByText("レビュー対象の提案はありません", { exact: false })).toBeVisible();
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

test("提案の読込失敗から再試行できる", async ({ page }) => {
  await mockApi(page);
  let allowProposals = false;
  await page.unroute("**/api/nl2sql/profiles/*/ontology-proposals");
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", async (route) => {
    if (!allowProposals) {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ data: null, error_messages: ["一時的な接続エラー"] }),
      });
      return;
    }
    await fulfillJson(route, { proposals: [] });
  });

  await page.goto("/ontology-build?profile=default");

  const proposalPanel = page.getByTestId("ontology-build-proposals");
  await expect(proposalPanel.getByText("AI 提案を読み込めませんでした。")).toBeVisible();
  allowProposals = true;
  await proposalPanel.getByRole("button", { name: "再試行" }).click();
  await expect(proposalPanel.getByText("レビュー対象の提案はありません", { exact: false })).toBeVisible();
});

test("過去 run の提案は最新実行分のみ表示され、すべて承認も最新分に限定される", async ({
  page,
}) => {
  await mockApi(page);
  // 旧 run(古い created_at・別 session)と最新 run(job-1)を混在させて返す
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", (route) =>
    fulfillJson(route, {
      proposals: [
        {
          id: "proposal-old",
          session_id: "ontology_build:job-old",
          profile_id: "default",
          base_revision_id: "revision-1",
          title_ja: "旧 run の提案: 廃止候補",
          description_ja: "過去実行で残った submitted 提案",
          kind: "mapping",
          status: "submitted",
          proposal_payload: { kind: "mapping", values: {} },
          created_at: "2026-07-11T00:00:00Z",
        },
        ...proposalsPending,
      ],
    })
  );
  await page.goto("/ontology-build?profile=default");

  const proposalList = page.getByTestId("ontology-build-proposals");
  // 最新 run の 2 件だけが並ぶ
  await expect(proposalList.getByText("業務エンティティ命名: 受注")).toBeVisible();
  await expect(proposalList.getByText("業務関係の提案: 顧客を参照")).toBeVisible();
  // 旧 run の提案は表示されない
  await expect(proposalList.getByText("旧 run の提案: 廃止候補")).toHaveCount(0);
  // 「すべて承認」も最新 run 分(2 件)に限定される
  await expect(proposalList.getByRole("button", { name: "すべて承認 (2 件)" })).toBeVisible();
  await expect(proposalList.getByRole("button", { name: "すべて承認 (3 件)" })).toHaveCount(0);
});

test("すべて承認 中はボタン自身のみスピナー表示で各カードには出さない", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/profiles/*/ontology-proposals", (route) =>
    fulfillJson(route, { proposals: proposalsPending })
  );
  // batch-accept を遅延させてローディング状態を観測できるようにする
  await page.route("**/api/nl2sql/ontology/proposals/batch-accept", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await fulfillJson(route, {
      proposals: proposalsPending.map((proposal) => ({ ...proposal, status: "accepted" })),
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
  await page.goto("/ontology-build?profile=default");

  const proposalList = page.getByTestId("ontology-build-proposals");
  const acceptAll = proposalList.getByRole("button", { name: "すべて承認 (2 件)" });
  await expect(acceptAll).toBeVisible();
  await acceptAll.click();

  // 「すべて承認」ボタン自身だけに回転スピナー(svg.animate-spin)が出る
  await expect(acceptAll.locator("svg.animate-spin")).toHaveCount(1);
  // カード内の承認/却下ボタンにはスピナーを出さない(過剰な回転アイコンを防止)
  await expect(
    page.getByTestId("ontology-proposal-list").locator("svg.animate-spin")
  ).toHaveCount(0);

  // 完了後は 2 件とも承認済み
  await expect(proposalList.getByText("承認済み", { exact: true })).toHaveCount(2);
});

test("承認済みで未公開の draft はリロード後も公開ボタンが表示される", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfillJson(route, {
      revisions: [
        ontologyView.ontology_graph.revision,
        {
          id: "revision-draft-4",
          version: 4,
          status: "draft",
          schema_fingerprint: "fp",
          etag: "draft-etag-4",
        },
      ],
      active_revision_id: ontologyView.ontology_graph.revision.id,
    })
  );

  await page.goto("/ontology-build?profile=default");

  const proposalList = page.getByTestId("ontology-build-proposals");
  await expect(proposalList.getByText("rev v4", { exact: false })).toBeVisible();
  await expect(proposalList.getByRole("button", { name: "Ontology を公開" })).toBeVisible();
});

test("SHACL Violation で公開を止め、修正後の再公開で復旧できる", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/nl2sql/ontology/revisions", (route) =>
    fulfillJson(route, {
      revisions: [
        ontologyView.ontology_graph.revision,
        {
          id: "revision-draft-4",
          version: 4,
          status: "draft",
          schema_fingerprint: "fp",
          etag: "draft-etag-4",
        },
      ],
      active_revision_id: ontologyView.ontology_graph.revision.id,
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
  const proposalList = page.getByTestId("ontology-build-proposals");
  const publish = proposalList.getByRole("button", { name: "Ontology を公開" });
  await publish.click();
  await expect(
    page.getByText("SHACL Core の Violation があるため公開を中止しました。")
  ).toBeVisible();
  await expect(publish).toBeEnabled();

  await publish.click();
  await expect(page.getByText("Ontology を公開しました。")).toBeVisible();
  expect(publishAttempt).toBe(2);
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

test("job 取得が連続失敗したらポーリングを停止しエラーを表示する", async ({ page }) => {
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
  ).toBeVisible({ timeout: 20000 });
  await expect(section.getByRole("button", { name: "AI 構築を実行" })).toBeEnabled();
  const stopped = polls;
  expect(stopped).toBeGreaterThanOrEqual(5);
  await page.waitForTimeout(2500);
  expect(polls).toBe(stopped);
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
  await expect(page.getByRole("tab")).toHaveCount(0);
  await expect(page.getByText("利用・コンテキスト")).toHaveCount(0);
  await expect(page.getByTestId("profile-ontology-build")).toBeVisible();
  await expect(page.getByTestId("profile-ontology-editor")).toBeVisible();
  await expect(page.getByTestId("ontology-mermaid-panel")).toBeVisible();
  await expect(page.getByTestId("fixed-split-pane-ontology-build-workspace")).toHaveAttribute(
    "data-split-layout",
    "stacked"
  );
  await expect(page.getByTestId("fixed-split-pane-profile-ontology-editor")).toHaveAttribute(
    "data-split-layout",
    "stacked"
  );

  await page.getByTestId("ontology-build-profile-select").focus();
  await expect(page.getByTestId("ontology-build-profile-select")).toBeFocused();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth
  );
  expect(overflow).toBe(false);
});
