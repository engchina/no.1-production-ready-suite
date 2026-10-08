import { expect, type Page, type Route, test } from "./fixtures/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

// 業務ガイド（#1237）: 検索・回答プロファイルの知識の「業務ガイド」タブ。
// 一覧・作成・保存・検証（問題 / 注意）・公開・409 の競合・履歴とロールバック・取り込みの確認。
// desktop / mobile（375px）の 2 つの project で動かす。

const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });
const failure = (status: number, messages: string[]) => ({
  status,
  json: { data: null, error_messages: messages, warning_messages: [] },
});

const profileSummary = {
  id: "bv-1",
  name: "受注サポート",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 1,
  created_at: "2026-09-25T00:00:00Z",
  updated_at: "2026-09-25T00:00:00Z",
  archived_at: null,
};

const profileDetail = {
  ...profileSummary,
  config: {
    version: 1,
    knowledge_base_ids: ["kb-1"],
    query: { guardrail_policy: null, answer_engine: "grounded" },
    serving_mode: "single",
  },
  knowledge_bases: [{ id: "kb-1", name: "受注マニュアル" }],
};

type Content = Record<string, unknown> & { title: string };
type Issue = { severity: "error" | "warning"; code: string; path: string; message: string };
type Revision = {
  revision: number;
  title: string;
  content_sha256: string;
  published_at: string;
  published_by: string | null;
  rollback_from: number | null;
  content: Content;
};
type Guide = {
  guide_id: string;
  status: "active" | "archived";
  draft_revision: number;
  draft: Content;
  published_revision: number | null;
  revisions: Revision[];
};

function content(title: string, overrides: Partial<Content> = {}): Content {
  return {
    schema_version: 1,
    title,
    description: "",
    goal: { expected_result: "手順を終えられる", intent_examples: ["パスワードを忘れた"], match_terms: [] },
    applicability: {
      business_domains: [],
      object_types: [],
      versions: [],
      effective_from: null,
      effective_to: null,
    },
    conditions: [],
    steps: [
      {
        id: "step1",
        title: "本人を確かめる",
        purpose: "",
        depends_on: [],
        retrieval_hints: [],
        evidence_requirements: [],
        allowed_tools: ["rag_search"],
        done_when: "",
      },
    ],
    branches: [],
    references: [],
    completion: [],
    impact: { scope: "individual", approval_required: false, approval_note: "" },
    handoff: { conditions: [], contact: "" },
    ...overrides,
  };
}

const coverageWarning: Issue = {
  severity: "warning",
  code: "branch_not_covered",
  path: "conditions[0]",
  message: "条件「アカウントの種類」の「派遣」に当たる分岐がありません。",
};
const referenceError: Issue = {
  severity: "error",
  code: "reference_unavailable",
  path: "references[0]",
  message: "資料「運用手順書」: 索引が済んでいません。",
};

/** 業務ガイドの API の状態を持つ mock（ほかの知識の API と検索・回答プロファイルも返す）。 */
class SupportGuideApi {
  guides: Guide[] = [];
  /** 検証で参照の問題を返すか（資料が索引済みになったら false）。 */
  referenceBroken = true;
  /** 次の下書きの保存を 409 にする（ほかの人が先に保存した）。 */
  conflictNext = false;
  requests: { method: string; path: string; body: unknown }[] = [];
  /** 下書きで試したとき、この下書きのガイドで答えたか（#1288）。 */
  tryUsesDraft = true;
  /** 下書きで試す応答を遅らせる時間（ms。処理中の表示を確かめる）。 */
  tryDelayMs = 0;

  detail(guide: Guide) {
    const published = guide.revisions.find((item) => item.revision === guide.published_revision);
    const draftJson = JSON.stringify(guide.draft);
    return {
      guide_id: guide.guide_id,
      search_answer_profile_id: "bv-1",
      status: guide.status,
      title: guide.draft.title,
      draft_revision: guide.draft_revision,
      published_revision: guide.published_revision,
      has_unpublished_changes: !published || JSON.stringify(published.content) !== draftJson,
      updated_at: "2026-10-07T01:00:00Z",
      updated_by: "管理者",
      draft: guide.draft,
      published: published?.content ?? null,
      revisions: [...guide.revisions]
        .sort((a, b) => b.revision - a.revision)
        .map(({ content: _content, ...rest }) => rest),
      issues: this.contentIssues(guide.draft),
    };
  }

  contentIssues(draft: Content): Issue[] {
    return Array.isArray(draft.branches) && draft.branches.length > 0 ? [coverageWarning] : [];
  }

  summary(guide: Guide) {
    const { draft: _draft, published: _published, revisions: _revisions, issues: _issues, ...rest } =
      this.detail(guide);
    return rest;
  }

  publish(guide: Guide, rollbackFrom: number | null = null) {
    const revision = (guide.revisions.at(-1)?.revision ?? 0) + 1;
    guide.revisions.push({
      revision,
      title: guide.draft.title,
      content_sha256: `sha-${revision}`,
      published_at: `2026-10-0${Math.min(revision, 9)}T02:00:00Z`,
      published_by: "管理者",
      rollback_from: rollbackFrom,
      content: guide.draft,
    });
    guide.published_revision = revision;
  }

  async handle(route: Route) {
    const request = route.request();
    const method = request.method();
    const path = new URL(request.url()).pathname;
    const body = request.postData() ? JSON.parse(request.postData() as string) : null;
    const base = "/api/search-answer-profiles/bv-1/support-guides";
    if (!path.startsWith(base)) return this.handleOther(route, path);
    this.requests.push({ method, path, body });
    const rest = path.slice(base.length);
    if (rest === "" && method === "GET") {
      const includeArchived = new URL(request.url()).searchParams.get("include_archived") === "true";
      return route.fulfill({
        json: envelope(
          this.guides
            .filter((guide) => includeArchived || guide.status === "active")
            .map((guide) => this.summary(guide)),
        ),
      });
    }
    if (rest === "" && method === "POST") {
      const guide: Guide = {
        guide_id: `guide-${this.guides.length + 1}`,
        status: "active",
        draft_revision: 1,
        draft: body.draft,
        published_revision: null,
        revisions: [],
      };
      this.guides.push(guide);
      return route.fulfill({ status: 201, json: envelope(this.detail(guide)) });
    }
    if (rest === "/import/preview") {
      const items = (body.guides as Content[]).map((item, index) => {
        const valid = typeof item.goal === "object" && item.goal !== null;
        // 同じ名前の既存のガイドとの差分（#1288。backend の diff_contents の形）。
        const existing = valid ? this.guides.find((guide) => guide.draft.title === item.title) : undefined;
        return {
          index,
          title: item.title ?? null,
          valid,
          issues: valid
            ? []
            : [{ severity: "error", code: "invalid_content", path: "goal", message: "入力してください。" }],
          existing: existing
            ? {
                guide_id: existing.guide_id,
                title: existing.draft.title,
                matched_by: "title",
                base: existing.published_revision != null ? "published" : "draft",
                revision: existing.published_revision ?? existing.draft_revision,
                status: existing.status,
                changes: [
                  { section: "basic", kind: "changed", key: "", label: item.title, fields: ["description"] },
                  {
                    section: "conditions",
                    kind: "changed",
                    key: "account",
                    label: "アカウントの種類",
                    fields: ["allowed_values"],
                  },
                  { section: "steps", kind: "added", key: "notify", label: "利用者に知らせる", fields: [] },
                  { section: "steps", kind: "removed", key: "step1", label: "本人を確かめる", fields: [] },
                  { section: "impact", kind: "changed", key: "", label: "", fields: ["scope"] },
                ],
              }
            : null,
        };
      });
      return route.fulfill({
        json: envelope({ items, importable_count: items.filter((item) => item.valid).length }),
      });
    }
    if (rest === "/import") {
      const created = (body.guides as Content[]).map((draft) => {
        const guide: Guide = {
          guide_id: `guide-${this.guides.length + 1}`,
          status: "active",
          draft_revision: 1,
          draft,
          published_revision: null,
          revisions: [],
        };
        this.guides.push(guide);
        return this.summary(guide);
      });
      return route.fulfill({ json: envelope({ created }) });
    }
    const match = /^\/([^/]+)(\/.*)?$/.exec(rest);
    const guide = this.guides.find((item) => item.guide_id === match?.[1]);
    if (!guide) return route.fulfill(failure(404, ["業務ガイドが見つかりません。"]));
    const action = match?.[2] ?? "";
    if (action === "" && method === "GET") return route.fulfill({ json: envelope(this.detail(guide)) });
    if (action === "" && method === "PUT") {
      if (this.conflictNext || body.base_revision !== guide.draft_revision) {
        this.conflictNext = false;
        guide.draft = content("別の人が直したタイトル");
        guide.draft_revision += 1;
        return route.fulfill(
          failure(409, [
            `読み込んだ後にほかの人が業務ガイドを保存しました。最新の内容（版 ${guide.draft_revision}）を読み込み直してから保存してください。`,
          ]),
        );
      }
      guide.draft = body.draft;
      guide.draft_revision += 1;
      return route.fulfill({ json: envelope(this.detail(guide)) });
    }
    if (action === "/try") {
      // 下書きで試す（#1288）。公開の版・下書きは変えない。
      if (body.draft_revision !== guide.draft_revision) {
        return route.fulfill(
          failure(409, [
            `読み込んだ後にほかの人が業務ガイドを保存しました。最新の内容（版 ${guide.draft_revision}）を読み込み直してから試してください。`,
          ]),
        );
      }
      if (this.tryDelayMs) await new Promise((resolve) => setTimeout(resolve, this.tryDelayMs));
      const conditions = body.conditions as Record<string, string>;
      return route.fulfill({
        json: envelope({
          trace_id: "guide-preview-0001",
          guide_id: guide.guide_id,
          draft_revision: guide.draft_revision,
          published_revision: guide.published_revision,
          guide_used: this.tryUsesDraft,
          guide: this.tryUsesDraft
            ? {
                guide_id: guide.guide_id,
                revision: guide.draft_revision,
                title: guide.draft.title,
                decision: conditions.account ? "answer" : "branch",
                known_conditions: conditions.account
                  ? [{ id: "account", label: "アカウントの種類", value: conditions.account, source: "user" }]
                  : [],
                unknown_conditions: conditions.account ? [] : [{ id: "account", label: "アカウントの種類" }],
                applicability: {},
                draft: true,
              }
            : null,
          outcome: "answered",
          answer: "社員の場合は、ポータルの「パスワードを忘れた」から再設定します。",
          citations: [
            {
              document_id: "doc-1",
              chunk_id: "doc-1:c1",
              text: "パスワードを忘れた場合は、ログイン画面のリンクから再設定する。",
              score: 0.91,
              rerank_score: null,
              file_name: "運用手順書.pdf",
              category_name: null,
              metadata: {},
            },
          ],
          clarifications: [],
          elapsed_ms: 4200,
        }),
      });
    }
    if (action === "/validate") {
      const issues = [...(this.referenceBroken ? [referenceError] : []), ...this.contentIssues(guide.draft)];
      return route.fulfill({
        json: envelope({ valid: !issues.some((issue) => issue.severity === "error"), issues }),
      });
    }
    if (action === "/publish") {
      if (this.referenceBroken) {
        return route.fulfill(
          failure(422, ["検証で問題が見つかったため公開できません。", referenceError.message]),
        );
      }
      this.publish(guide);
      return route.fulfill({ json: envelope(this.detail(guide)) });
    }
    const revisionMatch = /^\/revisions\/(\d+)$/.exec(action);
    if (revisionMatch) {
      const revision = guide.revisions.find((item) => item.revision === Number(revisionMatch[1]));
      return revision
        ? route.fulfill({ json: envelope(revision) })
        : route.fulfill(failure(404, ["業務ガイドが見つかりません。"]));
    }
    if (action === "/rollback") {
      const target = guide.revisions.find((item) => item.revision === body.revision);
      if (!target) return route.fulfill(failure(404, ["業務ガイドが見つかりません。"]));
      // 下書きを置き換えるので、読み込んだ下書きの版を照合する（#1278）。
      if (this.conflictNext || body.base_revision !== guide.draft_revision) {
        this.conflictNext = false;
        guide.draft = content("別の人が直したタイトル");
        guide.draft_revision += 1;
        return route.fulfill(
          failure(409, [
            `読み込んだ後にほかの人が業務ガイドを保存しました。最新の内容（版 ${guide.draft_revision}）を読み込み直してから戻してください。`,
          ]),
        );
      }
      guide.draft = target.content;
      guide.draft_revision += 1;
      this.publish(guide, target.revision);
      return route.fulfill({ json: envelope(this.detail(guide)) });
    }
    if (action === "/archive" || action === "/restore") {
      guide.status = action === "/archive" ? "archived" : "active";
      return route.fulfill({ json: envelope(this.detail(guide)) });
    }
    return route.fulfill(failure(404, ["見つかりません。"]));
  }

  handleOther(route: Route, path: string) {
    if (path.endsWith("/approved-faq")) {
      return route.fulfill({ json: envelope({ search_answer_profile_id: "bv-1", enabled: true, records: [] }) });
    }
    if (path.endsWith("/bv-1")) return route.fulfill({ json: envelope(profileDetail) });
    return route.fulfill({
      json: envelope({ items: [profileSummary], total: 1, limit: 50, offset: 0, has_next: false }),
    });
  }
}

async function openSupportGuides(page: Page, guides: Guide[] = []) {
  const api = new SupportGuideApi();
  api.guides = guides;
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: envelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) }),
  );
  await page.route("**/api/search-answer-profiles**", (route) => api.handle(route));
  await page.goto("/search-answer-profiles?id=bv-1");
  await page.getByRole("tab", { name: "業務ガイド" }).click();
  await expect(page.getByRole("tab", { name: "業務ガイド" })).toHaveAttribute("aria-selected", "true");
  return api;
}

function publishedGuide(): Guide {
  const first = content("パスワードの再設定");
  const second = content("パスワードの再設定（改訂）");
  return {
    guide_id: "guide-1",
    status: "active",
    draft_revision: 3,
    draft: second,
    published_revision: 2,
    revisions: [
      {
        revision: 1,
        title: first.title,
        content_sha256: "sha-1",
        published_at: "2026-10-01T02:00:00Z",
        published_by: "管理者",
        rollback_from: null,
        content: first,
      },
      {
        revision: 2,
        title: second.title,
        content_sha256: "sha-2",
        published_at: "2026-10-02T02:00:00Z",
        published_by: "管理者",
        rollback_from: null,
        content: second,
      },
    ],
  };
}

test("空の一覧から業務ガイドを作り、保存・検証（問題と注意）・公開できる", async ({ page }) => {
  const api = await openSupportGuides(page);
  await expect(page.getByText("業務ガイドはまだありません")).toBeVisible();

  await page.getByRole("button", { name: "新しい業務ガイド" }).click();
  const editor = page.getByTestId("support-guide-editor");
  await expect(editor.getByRole("heading", { name: "新しい業務ガイド" })).toBeVisible();
  // 検証・公開は保存してから。
  await expect(editor.getByRole("button", { name: "検証", exact: true })).toBeDisabled();

  // 必須の未入力は保存の前に止め、操作の直下と欄の直下に出す。
  await editor.getByRole("button", { name: "保存", exact: true }).click();
  const result = page.getByTestId("support-guide-result");
  await expect(result.getByText("保存できない入力が 2 件あります")).toBeVisible();
  await expect(editor.getByLabel("タイトル")).toBeFocused();
  expect(api.requests.filter((item) => item.method === "POST")).toHaveLength(0);

  await editor.getByLabel("タイトル").fill("パスワードの再設定");
  await editor.getByLabel("期待する結果").fill("利用者が再設定を終えられる");
  await editor.getByLabel("質問の例").fill("パスワードを忘れた\nログインできない");

  await editor.getByRole("button", { name: "条件を足す", exact: true }).click();
  const condition = editor.getByRole("listitem", { name: "条件 1" });
  await condition.getByLabel("名前").fill("アカウントの種類");
  await condition.getByLabel("選択肢").fill("社員\n派遣");
  await condition.getByLabel("確かめる問い").fill("社員ですか、派遣ですか？");

  await editor.getByRole("button", { name: "手順を足す" }).click();
  await editor.getByRole("listitem", { name: "手順 1" }).getByLabel("題名").fill("本人を確かめる");
  await editor.getByRole("button", { name: "手順を足す" }).click();
  await editor.getByRole("listitem", { name: "手順 2" }).getByLabel("題名").fill("再設定する");

  await editor.getByRole("button", { name: "分岐を足す" }).click();
  const branch = editor.getByRole("listitem", { name: "分岐 1" });
  await branch.getByRole("combobox", { name: /^条件/ }).click();
  await page.getByRole("option", { name: "アカウントの種類（cond1）" }).click();
  await branch.getByRole("combobox", { name: /^値/ }).click();
  await page.getByRole("option", { name: "社員" }).click();
  await branch.getByRole("combobox", { name: /^行き先の手順/ }).click();
  await page.getByRole("option", { name: "再設定する（step2）" }).click();

  await editor.getByRole("button", { name: "資料を足す" }).click();
  const reference = editor.getByRole("listitem", { name: "資料 1" });
  await reference.getByLabel("文書 ID").fill("doc-1");
  await reference.getByLabel("表示名").fill("運用手順書");

  await editor.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("業務ガイドを下書きとして作りました。")).toBeVisible();
  const created = api.requests.find((item) => item.method === "POST" && item.path.endsWith("/support-guides"));
  expect(created?.body).toMatchObject({
    draft: {
      title: "パスワードの再設定",
      goal: { intent_examples: ["パスワードを忘れた", "ログインできない"] },
      conditions: [{ id: "cond1", allowed_values: ["社員", "派遣"] }],
      branches: [{ when: { condition_id: "cond1", operator: "equals", values: ["社員"] }, goto_step: "step2" }],
    },
  });
  await expect(editor.getByRole("heading", { name: "編集中: パスワードの再設定" })).toBeVisible();
  await expect(editor.getByText("下書きのみ")).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "パスワードの再設定" })).toBeVisible();
  // 保存した下書きの内容の検証（注意）を出す。
  await expect(page.getByTestId("support-guide-warnings")).toContainText("分岐がありません");

  // 検証: 参照する資料の問題（公開できない）と注意を分けて、位置を利用者の言葉で出す。
  await editor.getByRole("button", { name: "検証", exact: true }).click();
  await expect(result.getByText("公開できない問題が 1 件あります")).toBeVisible();
  await expect(page.getByTestId("support-guide-errors")).toContainText("資料 1: 資料「運用手順書」: 索引が済んでいません。");
  await expect(result.getByText("確かめてほしい点が 1 件あります")).toBeVisible();
  await expect(page.getByTestId("support-guide-warnings")).toContainText("条件 1:");

  // 問題が残ったまま公開すると、断られた理由を出す。
  await editor.getByRole("button", { name: "公開", exact: true }).click();
  await page.getByRole("alertdialog", { name: "この業務ガイドを公開しますか？" }).getByRole("button", { name: "公開" }).click();
  await expect(result.getByText("検証で問題が見つかったため公開できません。")).toBeVisible();
  await expect(page.getByTestId("support-guide-refused-issues")).toContainText("索引が済んでいません。");

  // 資料が索引済みになったら、検証に通り公開できる。
  api.referenceBroken = false;
  await editor.getByRole("button", { name: "検証", exact: true }).click();
  await expect(page.getByTestId("support-guide-errors")).toHaveCount(0);
  await editor.getByRole("button", { name: "公開", exact: true }).click();
  await page.getByRole("alertdialog", { name: "この業務ガイドを公開しますか？" }).getByRole("button", { name: "公開" }).click();
  await expect(page.getByText("業務ガイドを公開しました（版 1）。")).toBeVisible();
  await expect(editor.getByText("公開中", { exact: true }).first()).toBeVisible();
  await expect(editor.getByRole("button", { name: "公開", exact: true })).toBeDisabled();
  await expect(page.getByRole("rowheader", { name: "版 1 公開中" })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("ほかの人が先に保存していたら、読み込み直しを案内し、最新の内容を読み込める", async ({ page }) => {
  const api = await openSupportGuides(page, [publishedGuide()]);
  await page.getByRole("button", { name: "パスワードの再設定（改訂） を編集" }).click();
  const editor = page.getByTestId("support-guide-editor");
  await expect(editor.getByText("未公開の変更あり")).toHaveCount(0);
  await editor.getByLabel("タイトル").fill("手元で直したタイトル");
  // 保存していない変更があるうちは、検証・公開できない理由を出す。
  await expect(page.getByTestId("support-guide-save-first")).toBeVisible();

  api.conflictNext = true;
  await editor.getByRole("button", { name: "保存", exact: true }).click();
  const result = page.getByTestId("support-guide-result");
  await expect(result.getByText("ほかの人が先に保存しました")).toBeVisible();
  await expect(result).toContainText("最新の内容（版 4）を読み込み直してから保存してください。");

  await result.getByRole("button", { name: "最新の内容を読み込む" }).click();
  const dialog = page.getByRole("alertdialog", { name: "保存していない変更を破棄しますか？" });
  await dialog.getByRole("button", { name: "最新の内容を読み込む" }).click();
  await expect(page.getByText("最新の内容を読み込みました。")).toBeVisible();
  await expect(editor.getByLabel("タイトル")).toHaveValue("別の人が直したタイトル");
  await expect(page.getByTestId("support-guide-save-first")).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("公開の履歴から前の版を見て、その版に戻せる", async ({ page }) => {
  const api = await openSupportGuides(page, [publishedGuide()]);
  await page.getByRole("button", { name: "パスワードの再設定（改訂） を編集" }).click();
  const editor = page.getByTestId("support-guide-editor");
  await expect(editor.getByRole("heading", { name: "公開の履歴" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: "版 2 公開中" })).toBeVisible();

  await page.getByRole("button", { name: "版 1 の操作" }).click();
  await page.getByRole("menuitem", { name: "この版を見る" }).click();
  const view = page.getByTestId("support-guide-revision-view");
  await expect(view.getByRole("heading", { name: "版 1 の内容" })).toBeVisible();
  await expect(view.getByTestId("support-guide-content-view")).toContainText("パスワードの再設定");
  await expect(view.getByTestId("support-guide-content-view")).toContainText("本人を確かめる");

  await view.getByRole("button", { name: "この版に戻す" }).click();
  const dialog = page.getByRole("alertdialog", { name: "版 1 に戻しますか？" });
  await expect(dialog).toContainText("版 1 の内容を新しい版として公開します。");
  await dialog.getByRole("button", { name: "この版に戻す" }).click();
  await expect(page.getByText("版 1 の内容を版 3 として公開しました。")).toBeVisible();
  expect(api.requests.find((item) => item.path.endsWith("/rollback"))?.body).toEqual({
    revision: 1,
    base_revision: 3,
  });
  await expect(page.getByRole("rowheader", { name: "版 3 公開中" })).toBeVisible();
  await expect(page.getByText("版 1 に戻した")).toBeVisible();
  await expect(editor.getByLabel("タイトル")).toHaveValue("パスワードの再設定");
  await expectNoPageOverflow(page);
});

test("ほかの人が先に下書きを保存していたら、前の版に戻さずに読み込み直しを案内する", async ({ page }) => {
  const api = await openSupportGuides(page, [publishedGuide()]);
  await page.getByRole("button", { name: "パスワードの再設定（改訂） を編集" }).click();
  const editor = page.getByTestId("support-guide-editor");
  await expect(page.getByRole("rowheader", { name: "版 2 公開中" })).toBeVisible();

  api.conflictNext = true;
  await page.getByRole("button", { name: "版 1 の操作" }).click();
  await page.getByRole("menuitem", { name: "この版に戻す" }).click();
  await page
    .getByRole("alertdialog", { name: "版 1 に戻しますか？" })
    .getByRole("button", { name: "この版に戻す" })
    .click();
  const result = page.getByTestId("support-guide-result");
  await expect(result.getByText("ほかの人が先に保存しました")).toBeVisible();
  await expect(result).toContainText("最新の内容（版 4）を読み込み直してから戻してください。");
  // 戻していない（公開の版は 2 のまま）。
  await expect(page.getByRole("rowheader", { name: "版 2 公開中" })).toBeVisible();
  await expect(page.getByRole("rowheader", { name: /版 3/ })).toHaveCount(0);

  await result.getByRole("button", { name: "最新の内容を読み込む" }).click();
  await expect(page.getByText("最新の内容を読み込みました。")).toBeVisible();
  await expect(editor.getByLabel("タイトル")).toHaveValue("別の人が直したタイトル");
  await expectNoPageOverflow(page);
});

test("JSON の取り込みは確認で各ガイドの可否を示し、すべて取り込めるときだけ取り込む", async ({ page }) => {
  const api = await openSupportGuides(page);
  await page.getByRole("button", { name: "取り込み" }).click();
  const panel = page.getByTestId("support-guide-import");
  const field = panel.getByRole("textbox", { name: /^JSON/ });

  // JSON として読めないときは送らずに知らせる。
  await field.fill("{");
  await panel.getByRole("button", { name: "確認" }).click();
  await expect(panel.getByText("JSON として読めません。")).toBeVisible();

  await field.fill(JSON.stringify({ schema_version: 1, guides: [content("取り込むガイド"), { title: "壊れたガイド" }] }));
  await panel.getByRole("button", { name: "確認" }).click();
  await expect(panel.getByText("2 件のうち 1 件を取り込めます。")).toBeVisible();
  await expect(panel.getByTestId("support-guide-import-item-0")).toContainText("取り込める");
  await expect(panel.getByTestId("support-guide-import-item-1")).toContainText("取り込めない");
  await expect(panel.getByTestId("support-guide-import-item-1")).toContainText("目的: 入力してください。");
  await expect(panel.getByRole("button", { name: "取り込む" })).toBeDisabled();

  await field.fill(JSON.stringify({ schema_version: 1, guides: [content("取り込むガイド")] }));
  await expect(panel.getByText("内容が変わりました。もう一度「確認」してください。")).toBeVisible();
  await panel.getByRole("button", { name: "確認" }).click();
  await expect(panel.getByText("1 件のうち 1 件を取り込めます。")).toBeVisible();
  await panel.getByRole("button", { name: "取り込む" }).click();
  await expect(page.getByText("業務ガイド 1 件を下書きとして取り込みました。")).toBeVisible();
  expect(api.requests.find((item) => item.path.endsWith("/import"))?.body).toMatchObject({
    guides: [{ title: "取り込むガイド" }],
  });
  await expect(page.getByRole("rowheader", { name: "取り込むガイド" })).toBeVisible();
  await expect(page.getByTestId("support-guide-import")).toHaveCount(0);
  await expectNoPageOverflow(page);
});

/** アプリの外観の設定（localStorage）でテーマを切り替える（`emulateMedia` では切り替わらない）。 */
async function useTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((value) => {
    window.localStorage.setItem("production-ready-rag.ui", JSON.stringify({ state: { theme: value }, version: 0 }));
  }, theme);
}

/** 公開の版 2 と、利用者に確かめる条件を足した下書き（版 3）を持つガイド。 */
function guideWithConditionDraft(): Guide {
  const guide = publishedGuide();
  guide.draft = content("パスワードの再設定（改訂）", {
    conditions: [
      {
        id: "account",
        label: "アカウントの種類",
        type: "enum",
        allowed_values: ["社員", "派遣"],
        required: true,
        source: "user",
        unknown_handling: "ask",
        question: "社員ですか、派遣ですか？",
        value_aliases: {},
      },
    ],
  });
  return guide;
}

for (const theme of ["light", "dark"] as const) {
  test(`下書きで試すと、公開の版を変えずに下書きで答えた回答を示す（${theme}）`, async ({ page }, testInfo) => {
    await useTheme(page, theme);
    const api = await openSupportGuides(page, [guideWithConditionDraft()]);
    api.tryDelayMs = 400;
    await page.getByRole("button", { name: "パスワードの再設定（改訂） を編集" }).click();
    const editor = page.getByTestId("support-guide-editor");
    const panel = editor.getByTestId("support-guide-try");
    await expect(panel.getByRole("heading", { name: "下書きで試す" })).toBeVisible();
    await expect(panel).toContainText("保存した下書き（版 3）で回答を作り");

    // 質問が空なら送らずに欄の直下で知らせる。
    await panel.getByRole("button", { name: "試す", exact: true }).click();
    await expect(panel.getByText("質問を入れてください。")).toBeVisible();
    expect(api.requests.filter((item) => item.path.endsWith("/try"))).toHaveLength(0);

    // 分かっている条件を選んで試す。
    await panel.getByRole("combobox", { name: /^アカウントの種類/ }).click();
    await page.getByRole("option", { name: "社員" }).click();
    await panel.getByRole("textbox", { name: /^質問/ }).fill("パスワードを忘れた");
    await panel.getByRole("button", { name: "試す", exact: true }).click();
    await expect(panel.getByTestId("support-guide-try-processing")).toBeVisible();
    const result = panel.getByTestId("support-guide-try-answer");
    await expect(result.getByText("下書き（版 3）の業務ガイドで答えました。")).toBeVisible();
    await expect(result).toContainText("利用者の回答の履歴・フィードバック・評価には入りません。");
    await expect(result).toContainText("所要時間: 4.2 秒");
    await expect(result.getByText("手順どおりに答えた")).toBeVisible();
    await expect(result.getByTestId("support-guide-try-known")).toHaveText("アカウントの種類: 社員");
    await expect(result).toContainText("ポータルの「パスワードを忘れた」から再設定します。");
    await expect(result.getByRole("heading", { name: "根拠（1 件）" })).toBeVisible();
    expect(api.requests.find((item) => item.path.endsWith("/try"))?.body).toEqual({
      query: "パスワードを忘れた",
      draft_revision: 3,
      conditions: { account: "社員" },
    });
    // 公開の版は 2 のまま（試しは保存しない）。
    await expect(page.getByRole("rowheader", { name: "版 2 公開中" })).toBeVisible();
    expect(api.requests.some((item) => /\/(publish|rollback)$/.test(item.path))).toBe(false);
    await expectNoPageOverflow(page);
    await panel.screenshot({ path: testInfo.outputPath(`support-guide-try-${theme}.png`) });

    // この下書きのガイドが使われなかったときは、見直す点を出す。
    api.tryUsesDraft = false;
    api.tryDelayMs = 0;
    await panel.getByRole("button", { name: "試す", exact: true }).click();
    await expect(result.getByText("この質問では、業務ガイドを使わずに答えました。")).toBeVisible();
    await expect(result).toContainText("質問の例・照合の語・適用範囲");

    // 保存していない変更があるうちは、保存した下書きでしか試せないので押せない。
    await editor.getByLabel("タイトル").fill("手元で直したタイトル");
    await expect(panel.getByRole("button", { name: "試す", exact: true })).toBeDisabled();
    await expect(panel.getByTestId("support-guide-try-save-first")).toBeVisible();
  });
}

test("取り込みの確認で、同じ名前の既存のガイドとの違いを節ごとに示す", async ({ page }, testInfo) => {
  await openSupportGuides(page, [publishedGuide()]);
  await page.getByRole("button", { name: "取り込み" }).click();
  const panel = page.getByTestId("support-guide-import");
  await panel
    .getByRole("textbox", { name: /^JSON/ })
    .fill(
      JSON.stringify({
        schema_version: 1,
        guides: [content("パスワードの再設定（改訂）"), content("新しいガイド")],
      }),
    );
  await panel.getByRole("button", { name: "確認" }).click();
  const diff = panel.getByTestId("support-guide-import-diff-0");
  await expect(diff.getByRole("heading", { name: "既存のガイド「パスワードの再設定（改訂）」との違い" })).toBeVisible();
  await expect(diff).toContainText("名前が同じ");
  await expect(diff).toContainText("公開の版 2 と比べています");
  await expect(diff).toContainText("取り込んでも既存のガイドは変わらず、別の下書きとして作ります。");
  await expect(diff).toContainText("追加 1・削除 1・変更 3");
  await expect(diff).toContainText("確認する条件");
  await expect(diff).toContainText("変わった項目: 選択肢");
  await expect(diff.locator('[data-change-kind="added"]')).toContainText("利用者に知らせる");
  await expect(diff.locator('[data-change-kind="removed"]')).toContainText("本人を確かめる");
  await expect(diff).toContainText("変わった項目: 影響の範囲");
  await expect(panel.getByTestId("support-guide-import-new-1")).toContainText("同じ ID・名前のガイドはありません。");
  await expect(panel.getByRole("button", { name: "取り込む" })).toBeEnabled();
  await expectNoPageOverflow(page);
  await panel.screenshot({ path: testInfo.outputPath("support-guide-import-diff.png") });
});
