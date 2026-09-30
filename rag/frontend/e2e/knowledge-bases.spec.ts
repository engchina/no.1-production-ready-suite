import { expect, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady, mockLocalAuth } from "./_helpers";

type KnowledgeBaseStatus = "ACTIVE" | "ARCHIVED";
type SearchMode = "hybrid" | "vector" | "keyword";
type FileStatus = "UPLOADED" | "INGESTING" | "INDEXED" | "ERROR";

interface KnowledgeBaseSummary {
  id: string;
  name: string;
  description: string | null;
  status: KnowledgeBaseStatus;
  default_search_mode: SearchMode;
  document_count: number;
  indexed_document_count: number;
  error_document_count: number;
  searchable_chunk_count: number;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

interface DocumentSummary {
  id: string;
  file_name: string;
  status: FileStatus;
  category_name: string | null;
  content_type: string | null;
  file_size_bytes: number | null;
  content_sha256: string | null;
  duplicate_of_document_id: string | null;
  uploaded_at: string;
  indexed_at: string | null;
  knowledge_bases: { id: string; name: string }[];
}

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
});

test("ナレッジベース管理で作成、文書追加、文書解除、アーカイブができる", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases");

  // 一覧は list 専用(行は詳細ページへのリンク)。作成は業務ビューと同じく PageHeader の「新規作成」から
  // 作成の画面(`?id=new`)へ移る(#555)。
  await expect(page.getByRole("heading", { name: "ナレッジベース", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "社内規程" })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveCount(0);
  await expectNoPageOverflow(page);

  await page.getByRole("button", { name: "新規作成" }).click();
  await expect(page).toHaveURL(/\/knowledge-bases\?id=new$/);
  await expect(page.getByRole("heading", { name: "ナレッジベースを作成", level: 1 })).toBeVisible();
  const breadcrumbs = page.getByRole("navigation", { name: "パンくず" });
  await expect(breadcrumbs.getByRole("link", { name: "ナレッジベース" })).toHaveAttribute(
    "href",
    "/knowledge-bases"
  );
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toBeFocused();
  await expectNoPageOverflow(page);

  // 作成すると新 KB の詳細ページへ履歴を積まずに移る。
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("設計資料");
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("設計レビュー用の資料");
  await page.getByRole("button", { name: "作成する" }).click();

  // 作成成功 = 新 KB 詳細ページへの遷移 + 見出しで担保(作成トーストはナビと競合し
  // 自動消滅するため、ここでは判定しない)。
  await expect(page).toHaveURL(/\/knowledge-bases\/kb-2$/);
  await expect(page.getByRole("heading", { name: "設計資料", level: 1 })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveValue("設計資料");

  // 詳細ページで文書を追加する。
  await page.getByRole("combobox", { name: "文書を追加" }).click();
  await page.getByRole("option", { name: "guide.txt" }).click();
  await page.getByRole("button", { name: "追加" }).click();

  await expect(page.getByText("文書をナレッジベースに追加しました。").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "guide.txt" })).toBeVisible();

  // 詳細ページで文書を外す。
  const assignedDocument = page.locator("li").filter({ hasText: "guide.txt" });
  await assignedDocument.getByRole("button", { name: "guide.txt の操作" }).click();
  await page.getByRole("menuitem", { name: "外す" }).click();
  const removeDialog = page.getByRole("alertdialog", { name: "所属から外しますか？" });
  await expect(removeDialog).toBeVisible();
  await removeDialog.getByRole("button", { name: "外す" }).click();

  await expect(page.getByText("文書をナレッジベースから外しました。").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "guide.txt" })).toHaveCount(0);

  // 一覧へ戻ってアーカイブする。
  await page.goto("/knowledge-bases");
  const createdRow = page.locator("tr").filter({ hasText: "設計資料" });
  await createdRow.getByRole("button", { name: "設計資料 の操作" }).click();
  await page.getByRole("menuitem", { name: "アーカイブ" }).click();
  const archiveDialog = page.getByRole("alertdialog", { name: "ナレッジベースをアーカイブしますか？" });
  await expect(archiveDialog).toBeVisible();
  await archiveDialog.getByRole("button", { name: "アーカイブ" }).click();

  await expect(page.getByText("ナレッジベースをアーカイブしました。").first()).toBeVisible();
  // 既定 ACTIVE フィルタなので、アーカイブ済みは一覧から消える。
  await expect(page.getByRole("link", { name: "設計資料" })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

// #131: 一覧の行（RowActionMenu）と同じ操作の定義を、詳細では ObjectActionBar で出す。
for (const viewport of [
  { name: "desktop", width: 1280, height: 800 },
  { name: "mobile", width: 375, height: 812 },
]) {
  test(`詳細の ObjectActionBar からアーカイブでき、メニューは Esc でフォーカスを戻す (${viewport.name})`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const state = createKnowledgeBaseState();
    await mockKnowledgeBaseApi(page, state);

    await page.goto("/knowledge-bases/kb-1");
    await expect(page.getByRole("heading", { name: "社内規程", level: 1 })).toBeVisible();

    const bar = page.getByRole("group", { name: "社内規程 の操作" });
    // 危険な操作は常時表示せず「その他の操作」へ入れる（buttons.md §5.1）。
    await expect(bar.getByRole("button", { name: "アーカイブ" })).toHaveCount(0);
    const more = bar.getByRole("button", { name: "その他の操作" });
    await more.focus();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("menuitem", { name: "アーカイブ" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("menu")).toHaveCount(0);
    await expect(more).toBeFocused();

    await more.click();
    await page.getByRole("menuitem", { name: "アーカイブ" }).click();
    const dialog = page.getByRole("alertdialog", { name: "ナレッジベースをアーカイブしますか？" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "キャンセル" }).click();
    expect(state.knowledgeBases[0].status).toBe("ACTIVE");

    await more.click();
    await page.getByRole("menuitem", { name: "アーカイブ" }).click();
    await dialog.getByRole("button", { name: "アーカイブ" }).click();
    await expect(page.getByText("ナレッジベースをアーカイブしました。").first()).toBeVisible();
    expect(state.knowledgeBases[0].status).toBe("ARCHIVED");
    // 業務ビューと同じく、アーカイブしたら一覧へ履歴を積まずに戻る（#555）。
    await expect(page).toHaveURL(/\/knowledge-bases$/);
    await expectNoPageOverflow(page);
  });
}

test("狭い画面幅(375px)でもページ全体が横スクロール(崩れ)しない", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);

  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto("/knowledge-bases");

  await expect(page.getByRole("heading", { name: "ナレッジベース", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "社内規程" })).toBeVisible();
  // documentElement と main の双方で横はみ出し(崩れ)が無いこと。
  // テーブルの min-width はテーブル内の overflow-x-auto に閉じ込める前提。
  await expectNoPageOverflow(page);

  // 検索入力は狭幅で全幅に流動化し、固定幅(w-64)のはみ出しを起こさない。
  const search = page.getByPlaceholder("名前・説明で検索");
  const searchBox = await search.boundingBox();
  expect(searchBox).not.toBeNull();
  expect(searchBox!.width).toBeLessThanOrEqual(375);
});

test("一覧のスクロール領域はホイールの scroll chaining を遮断しない", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases");
  await expect(page.getByRole("link", { name: "社内規程" })).toBeVisible();

  // overscroll-behavior: contain が付くと、中身が少なくスクロール余地が無いときも
  // カード上の wheel がページへ伝播しなくなる(RAG検索の引用カードで実害)。復活をガードする。
  // 一覧は DataTable の表示行数（visibleRows）で表の中を縦スクロールにする（#265）。
  const area = page.getByTestId("knowledge-bases-scroll-region");
  await expect(area).toBeVisible();
  const behavior = await area.evaluate((el) => getComputedStyle(el).overscrollBehaviorY);
  expect(behavior).toBe("auto");
});

test("DEFAULT は先頭表示され、予約名として保護される", async ({ page }) => {
  const state = createKnowledgeBaseState();
  state.knowledgeBases.unshift(makeKnowledgeBase({ id: "kb-default", name: "DEFAULT" }));
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases");

  const rows = page.locator("tbody tr");
  await expect(rows.first()).toContainText("DEFAULT");
  // アーカイブは行の RowActionMenu に入り、DEFAULT では理由付きで無効（#131）。
  await rows.first().getByRole("button", { name: "DEFAULT の操作" }).click();
  await expect(page.getByRole("menuitem", { name: "DEFAULT はアーカイブできません" })).toBeDisabled();
  await page.keyboard.press("Escape");

  await page.goto("/knowledge-bases?id=new");
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("default");
  await page.getByRole("button", { name: "作成する" }).click();
  await expect(page.getByText("DEFAULT は予約名のため使用できません。")).toBeVisible();
  await expect(page).toHaveURL(/\/knowledge-bases\?id=new$/);
  await expectNoPageOverflow(page);
});

function emptyAdapterConfig() {
  return {
    version: 1,
    ingestion: {
      preprocess_profile: null,
      parser_adapter_backend: null,
      parser_docling_enabled: null,
      parser_unstructured_enabled: null,
      chunking_strategy: null,
      chunk_size: null,
      chunk_overlap: null,
      chunk_min_chars: null,
      chunk_context_header_enabled: null,
      graph_profile: null,
      field_extraction_enabled: null,
      vision_enabled: null,
      navigation_summary_enabled: null,
    },
    query: {
      retrieval_strategy: null,
      post_retrieval_pipeline: null,
      generation_profile: null,
      guardrail_policy: null,
    },
  };
}

async function mockKnowledgeBaseApi(
  page: Page,
  state: {
    knowledgeBases: KnowledgeBaseSummary[];
    documents: DocumentSummary[];
  }
) {
  await page.route("**/api/knowledge-bases**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const parts = url.pathname.split("/").filter(Boolean);
    const id = parts[2];

    if (request.method() === "GET" && url.pathname === "/api/knowledge-bases") {
      await route.fulfill({
        json: {
          data: pageKnowledgeBases(state.knowledgeBases, url),
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "GET" && parts.length === 3) {
      const knowledgeBase = state.knowledgeBases.find((item) => item.id === id);
      if (!knowledgeBase) {
        await route.fulfill({ status: 404, json: { detail: "not found" } });
        return;
      }
      await route.fulfill({
        json: {
          data: {
            ...knowledgeBase,
            retrieval_config: {},
            adapter_config: emptyAdapterConfig(),
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "PATCH" && parts.length === 3) {
      const knowledgeBase = state.knowledgeBases.find((item) => item.id === id);
      if (!knowledgeBase) {
        await route.fulfill({ status: 404, json: { detail: "not found" } });
        return;
      }
      const payload = request.postDataJSON() as { adapter_config?: unknown };
      await route.fulfill({
        json: {
          data: {
            ...knowledgeBase,
            retrieval_config: {},
            adapter_config: payload.adapter_config ?? emptyAdapterConfig(),
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "POST" && url.pathname === "/api/knowledge-bases") {
      const payload = request.postDataJSON() as {
        name: string;
        description?: string | null;
        default_search_mode?: SearchMode;
      };
      if (
        state.knowledgeBases.some(
          (item) => item.name.toLowerCase() === payload.name.toLowerCase()
        )
      ) {
        await route.fulfill({
          status: 409,
          json: {
            data: null,
            error_messages: [DUPLICATE_NAME_MESSAGE],
            warning_messages: [],
          },
        });
        return;
      }
      const knowledgeBase = makeKnowledgeBase({
        id: `kb-${state.knowledgeBases.length + 1}`,
        name: payload.name,
        description: payload.description ?? null,
        default_search_mode: payload.default_search_mode ?? "hybrid",
      });
      state.knowledgeBases.push(knowledgeBase);
      await route.fulfill({
        json: {
          data: { ...knowledgeBase, retrieval_config: {}, adapter_config: emptyAdapterConfig() },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "POST" && parts.length === 4 && parts[3] === "archive") {
      const knowledgeBase = state.knowledgeBases.find((item) => item.id === id);
      if (!knowledgeBase) {
        await route.fulfill({ status: 404, json: { detail: "not found" } });
        return;
      }
      knowledgeBase.status = "ARCHIVED";
      knowledgeBase.archived_at = "2026-06-15T00:05:00Z";
      knowledgeBase.updated_at = "2026-06-15T00:05:00Z";
      await route.fulfill({
        json: {
          data: { ...knowledgeBase, retrieval_config: {} },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "POST" && parts.length === 4 && parts[3] === "documents") {
      const payload = request.postDataJSON() as { document_ids: string[] };
      assignDocuments(state, id, payload.document_ids);
      await route.fulfill({
        json: {
          data: {
            ...state.knowledgeBases.find((item) => item.id === id),
            retrieval_config: {},
          },
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    if (request.method() === "DELETE" && parts.length === 5 && parts[3] === "documents") {
      removeDocument(state, id, parts[4]);
      await route.fulfill({
        json: {
          data: null,
          error_messages: [],
          warning_messages: [],
        },
      });
      return;
    }

    await route.fulfill({ status: 404, json: { detail: "not found" } });
  });

  await page.route("**/api/documents**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname !== "/api/documents") {
      await route.fulfill({ status: 404, json: { detail: "not found" } });
      return;
    }
    const knowledgeBaseId = url.searchParams.get("knowledge_base_id");
    const q = url.searchParams.get("q")?.trim().toLowerCase();
    const limit = Number(url.searchParams.get("limit") ?? 50);
    const offset = Number(url.searchParams.get("offset") ?? 0);
    const documents = (
      knowledgeBaseId
        ? state.documents.filter((document) =>
            document.knowledge_bases.some((knowledgeBase) => knowledgeBase.id === knowledgeBaseId)
          )
        : state.documents
    ).filter((document) => !q || document.file_name.toLowerCase().includes(q));
    await route.fulfill({
      json: {
        data: {
          items: documents.slice(offset, offset + limit),
          total: documents.length,
          limit,
          offset,
          has_next: offset + limit < documents.length,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
}

const DUPLICATE_NAME_MESSAGE =
  "同じ名前のナレッジベース（アーカイブ済みを含む）がすでにあります。別の名前を指定してください。";

// #282: 所属文書は件数で打ち切らずページングし、外して空になったページから最後のページへ戻る。
test("所属文書は 10 件ずつページングし、外して空になったページから戻る", async ({ page }) => {
  const state = createKnowledgeBaseState();
  const kb = state.knowledgeBases[0];
  for (let index = 2; index <= 11; index += 1) {
    state.documents.push(
      makeDocument({
        id: `doc-member-${index}`,
        file_name: `member-${String(index).padStart(2, "0")}.txt`,
        status: "INDEXED",
        knowledge_bases: [{ id: kb.id, name: kb.name }],
      })
    );
  }
  refreshKnowledgeBaseCounts(state, kb.id);
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases/kb-1");
  const pagination = page.getByTestId("knowledge-base-documents-pagination");
  await expect(pagination).toContainText("1 - 10 / 11 件");
  await expect(page.getByRole("link", { name: /^member-|^policy/ })).toHaveCount(10);

  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(pagination).toContainText("11 - 11 / 11 件");
  const lastRow = page.locator("li").filter({ hasText: "member-11.txt" });
  await expect(lastRow).toBeVisible();

  // 2 ページ目の唯一の文書を外すと、空の案内ではなく 1 ページ目へ戻る。
  await lastRow.getByRole("button", { name: "member-11.txt の操作" }).click();
  await page.getByRole("menuitem", { name: "外す" }).click();
  const dialog = page.getByRole("alertdialog", { name: "所属から外しますか？" });
  await expect(dialog).toContainText("ほかのナレッジベースに所属していない文書は DEFAULT へ移ります。");
  await dialog.getByRole("button", { name: "外す" }).click();

  await expect(page.getByRole("link", { name: /^member-|^policy/ })).toHaveCount(10);
  await expect(page.getByText("所属文書がありません。")).toHaveCount(0);
  // 1 ページに収まったので Pagination は出さない。
  await expect(pagination).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("追加候補は文書名で検索して選べる", async ({ page }) => {
  const state = createKnowledgeBaseState();
  state.documents.push(
    makeDocument({ id: "doc-3", file_name: "security-handbook.pdf", knowledge_bases: [] })
  );
  await mockKnowledgeBaseApi(page, state);
  const candidateQueries: (string | null)[] = [];
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname === "/api/documents" && !url.searchParams.has("knowledge_base_id")) {
      candidateQueries.push(url.searchParams.get("q"));
    }
  });

  await page.goto("/knowledge-bases/kb-1");
  const search = page.getByRole("searchbox", { name: "追加する文書を検索" });
  await search.fill("security");
  await search.press("Enter");
  await expect.poll(() => candidateQueries.includes("security")).toBe(true);

  await page.getByRole("combobox", { name: "文書を追加" }).click();
  await expect(page.getByRole("option", { name: "security-handbook.pdf" })).toBeVisible();
  await expect(page.getByRole("option", { name: "guide.txt" })).toHaveCount(0);
  await page.getByRole("option", { name: "security-handbook.pdf" }).click();
  await page.getByRole("button", { name: "追加", exact: true }).click();
  await expect(page.getByRole("link", { name: "security-handbook.pdf" })).toBeVisible();
  await expectNoPageOverflow(page);
});

test("ナレッジベースの名前と説明は必須で、空・空白だけでは作成せず最初の不正な欄へフォーカスする", async ({
  page,
}) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);
  const before = state.knowledgeBases.length;

  await page.goto("/knowledge-bases?id=new");
  const name = page.getByRole("textbox", { name: "名前", exact: true });
  const description = page.getByRole("textbox", { name: "説明", exact: true });
  await expect(name).toHaveAttribute("aria-required", "true");
  await expect(description).toHaveAttribute("aria-required", "true");
  await expect(description).toHaveAttribute("maxlength", "2000");

  await page.getByRole("button", { name: "作成する" }).click();
  await expect(page.getByText("名前を入力してください。")).toBeVisible();
  await expect(page.getByText("説明を入力してください。")).toBeVisible();
  await expect(name).toBeFocused();

  await name.fill("設計資料");
  await description.fill(" \u3000 ");
  await page.getByRole("button", { name: "作成する" }).click();
  await expect(page.getByText("説明を入力してください。")).toBeVisible();
  await expect(description).toBeFocused();
  expect(state.knowledgeBases).toHaveLength(before);
  await expect(page).toHaveURL(/\/knowledge-bases\?id=new$/);
  await expectNoPageOverflow(page);
});

test("同じ名前で作成すると理由を名前の欄に表示し、詳細へ移らない", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases?id=new");
  const name = page.getByRole("textbox", { name: "名前", exact: true });
  await name.fill("社内規程");
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("就業規則");
  await page.getByRole("button", { name: "作成する" }).click();

  // 同名（409）は名前の欄の下に理由を出し、名前の欄へフォーカスを戻す（編集と同じ。#555）。
  await expect(page.getByText(DUPLICATE_NAME_MESSAGE)).toHaveCount(1);
  await expect(name).toHaveAttribute("aria-invalid", "true");
  await expect(name).toBeFocused();
  await expect(page).toHaveURL(/\/knowledge-bases\?id=new$/);
  await expect(name).toHaveAttribute("maxlength", "256");
  // 名前を直すと古い理由は消える。
  await name.fill("社内規程 2");
  await expect(page.getByText(DUPLICATE_NAME_MESSAGE)).toHaveCount(0);
});

test("最後のページの KB をアーカイブすると、空の案内ではなく前のページへ戻る", async ({ page }) => {
  const state = createKnowledgeBaseState();
  for (let index = 2; index <= 21; index += 1) {
    state.knowledgeBases.push(
      makeKnowledgeBase({ id: `kb-${index}`, name: `KB ${String(index).padStart(2, "0")}` })
    );
  }
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases");
  // 状態は色だけでなくアイコン付きの StatusBadge で出す。
  await expect(
    page.locator("tbody tr").first().locator('[data-status-variant="success"] svg')
  ).toBeVisible();
  // 共通の Pagination（10 件/ページ、#265）。最後の 3 ページ目へ移る。
  const pagination = page.getByTestId("knowledge-bases-pagination");
  await pagination.getByRole("button", { name: "次へ" }).click();
  await pagination.getByRole("button", { name: "次へ" }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);

  const lastRow = page.locator("tbody tr").first();
  const name = (await lastRow.getByRole("link").textContent())?.trim() ?? "";
  await lastRow.getByRole("button", { name: `${name} の操作` }).click();
  await page.getByRole("menuitem", { name: "アーカイブ" }).click();
  await page
    .getByRole("alertdialog", { name: "ナレッジベースをアーカイブしますか？" })
    .getByRole("button", { name: "アーカイブ" })
    .click();

  await expect(page.locator("tbody tr")).toHaveCount(10);
  await expect(page.getByText("ナレッジベースがありません。")).toHaveCount(0);
  await expect(pagination).toContainText("11 - 20 / 20 件");
});

// #555: 一覧 → 作成 / 詳細の構成を業務ビューにそろえる（page-archetypes.md §1 A）。
test("行のクリックで詳細を開き、詳細の PageHeader に パンくず・状態・件数・一覧へ戻る・保存する を出す", async ({
  page,
}) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);
  await page.goto("/knowledge-bases");

  // 行の操作以外の領域（文書数の列）のクリックで開く。
  await page.getByTestId("knowledge-base-row-kb-1").getByRole("cell").nth(2).click();
  await expect(page).toHaveURL(/\/knowledge-bases\/kb-1$/);
  await expect(page.getByRole("heading", { name: "社内規程", level: 1 })).toBeVisible();
  const breadcrumbs = page.getByRole("navigation", { name: "パンくず" });
  await expect(breadcrumbs.getByText("社内規程")).toHaveAttribute("aria-current", "page");
  const header = page.locator("header[data-page-header]");
  await expect(header.getByText("有効")).toBeVisible();
  await expect(page.getByTestId("knowledge-base-meta")).toContainText("文書 1 件");
  await expect(page.getByTestId("knowledge-base-meta")).toContainText("索引済み 1 件");
  await expect(page.getByRole("button", { name: "保存する" })).toBeVisible();
  // 手書きの「戻る」リンクの帯とカード内の見出しは無い（h1 は PageHeader の 1 つだけ）。
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(1);
  await expectNoPageOverflow(page);

  await clickBackToList(page);
  await expect(page).toHaveURL(/\/knowledge-bases$/);
});

for (const viewport of [
  { name: "1920", width: 1920, height: 1000 },
  { name: "1280", width: 1280, height: 800 },
  { name: "375", width: 375, height: 812 },
]) {
  test(`詳細の PageHeader のタイトルと本文のカードの左端がそろう (${viewport.name})`, async ({ page }) => {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await mockKnowledgeBaseApi(page, createKnowledgeBaseState());
    await page.goto("/knowledge-bases/kb-1");
    await expect(page.getByTestId("knowledge-base-form")).toBeVisible();
    const lefts = await page.evaluate(() => {
      const title = document.querySelector("main h1")?.getBoundingClientRect().left ?? -1;
      let card = document.querySelector('[data-testid="knowledge-base-form"]')?.parentElement ?? null;
      while (card && !card.className.includes("border")) card = card.parentElement;
      return { title: Math.round(title), card: Math.round(card?.getBoundingClientRect().left ?? -100) };
    });
    expect(Math.abs(lefts.title - lefts.card)).toBeLessThanOrEqual(1);
    await expectNoPageOverflow(page);
  });
}

test("`?id=<id>` で開くと詳細の URL へ置き換え、存在しない詳細は別の対象へ置き換えず一覧へ戻る導線を出す", async ({
  page,
}) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases?id=kb-1");
  await expect(page).toHaveURL(/\/knowledge-bases\/kb-1$/);
  await expect(page.getByRole("heading", { name: "社内規程", level: 1 })).toBeVisible();

  await page.goto("/knowledge-bases/kb-missing");
  // 404 は再試行せず、すぐ「対象が見つかりません」を出す。
  await expect(page.getByText("対象が見つかりません")).toBeVisible();
  await expect(page.getByText("「kb-missing」は削除されたか、存在しません。")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveCount(0);
  await expectNoPageOverflow(page);
  await page.getByRole("button", { name: "一覧へ戻る" }).click();
  await expect(page).toHaveURL(/\/knowledge-bases$/);
});

test("作成の画面は未保存の入力があると離脱を確認し、下書きを一覧から再開できる", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);
  await page.goto("/knowledge-bases?id=new");
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("設計資料");

  await page
    .getByRole("navigation", { name: "パンくず" })
    .getByRole("link", { name: "ナレッジベース" })
    .click();
  const dialog = page.getByRole("alertdialog", { name: "保存していない変更があります" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "キャンセル" }).click();
  await expect(page).toHaveURL(/\?id=new$/);

  await clickBackToList(page);
  await page
    .getByRole("alertdialog", { name: "保存していない変更があります" })
    .getByRole("button", { name: "移動する" })
    .click();
  await expect(page).toHaveURL(/\/knowledge-bases$/);

  await expect(page.getByText("作成中のナレッジベースに保存していない下書きがあります。")).toBeVisible();
  await page.getByRole("button", { name: "下書きを開く" }).click();
  await expect(page).toHaveURL(/\?id=new$/);
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveValue("設計資料");
  await expect(page.getByText("保存していない下書きを復元しました。")).toBeVisible();
  await page.getByRole("button", { name: "変更を元に戻す" }).click();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveValue("");
  // 下書きが無くなったので、確認なしで一覧へ戻れる。
  await clickBackToList(page);
  await expect(page).toHaveURL(/\/knowledge-bases$/);
  await expect(page.getByRole("button", { name: "下書きを開く" })).toHaveCount(0);
});

test("詳細で名前・説明を変えると離脱を確認し、同じ詳細を開き直すと下書きを復元する", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);
  await page.goto("/knowledge-bases/kb-1");
  const description = page.getByRole("textbox", { name: "説明", exact: true });
  await description.fill("就業規則と経費精算");

  await clickBackToList(page);
  await page
    .getByRole("alertdialog", { name: "保存していない変更があります" })
    .getByRole("button", { name: "移動する" })
    .click();
  await expect(page).toHaveURL(/\/knowledge-bases$/);

  await page.getByRole("link", { name: "社内規程" }).click();
  await expect(description).toHaveValue("就業規則と経費精算");
  await expect(page.getByText("保存していない下書きを復元しました。")).toBeVisible();
});

test("ナレッジベースが無いときは、空の状態から作成の画面へ進める", async ({ page }) => {
  await mockKnowledgeBaseApi(page, { knowledgeBases: [], documents: [] });
  await page.goto("/knowledge-bases");
  await expect(page.getByText("ナレッジベースがありません。")).toBeVisible();
  await page.getByRole("button", { name: "最初のナレッジベースを作成" }).click();
  await expect(page).toHaveURL(/\?id=new$/);
});

test("アーカイブ済みの詳細は読み取り専用で、保存できない", async ({ page }) => {
  const state = createKnowledgeBaseState();
  state.knowledgeBases.push(
    makeKnowledgeBase({ id: "kb-old", name: "旧規程", description: "旧版", status: "ARCHIVED" })
  );
  await mockKnowledgeBaseApi(page, state);
  await page.goto("/knowledge-bases/kb-old");

  await expect(page.getByText("アーカイブ済みのナレッジベースは、名前・説明の変更と文書の追加・解除ができません。")).toBeVisible();
  await expect(page.getByRole("textbox", { name: "名前", exact: true })).toHaveAttribute("readonly", "");
  await expect(page.getByRole("textbox", { name: "説明", exact: true })).toHaveAttribute("readonly", "");
  await expect(page.getByRole("button", { name: "保存する" })).toBeDisabled();
  // アーカイブ済みには出せる操作がないため、操作のバーごと出さない。
  await expect(page.getByTestId("knowledge-base-detail-actions")).toHaveCount(0);
});

/** エディタの「一覧へ戻る」。375px ではページ操作の「その他の操作」に入る（主操作 1 つ + その他）。 */
async function clickBackToList(page: Page) {
  const actions = page.getByRole("group", { name: "ページ操作" });
  const direct = actions.getByRole("button", { name: "一覧へ戻る" });
  if (await direct.isVisible()) {
    await direct.click();
    return;
  }
  await actions.getByRole("button", { name: "その他の操作" }).click();
  await page.getByRole("menuitem", { name: "一覧へ戻る" }).click();
}

function createKnowledgeBaseState() {
  const knowledgeBases = [
    makeKnowledgeBase({
      id: "kb-1",
      name: "社内規程",
      description: "人事・経費・情報管理の規程",
      document_count: 1,
      indexed_document_count: 1,
      searchable_chunk_count: 8,
    }),
  ];
  const documents = [
    makeDocument({
      id: "doc-1",
      file_name: "policy.txt",
      status: "INDEXED",
      knowledge_bases: [{ id: "kb-1", name: "社内規程" }],
    }),
    makeDocument({
      id: "doc-2",
      file_name: "guide.txt",
      status: "UPLOADED",
      knowledge_bases: [],
    }),
  ];
  return { knowledgeBases, documents };
}

function pageKnowledgeBases(knowledgeBases: KnowledgeBaseSummary[], url: URL) {
  const status = url.searchParams.get("status") as KnowledgeBaseStatus | null;
  const q = url.searchParams.get("q")?.trim();
  const limit = Number(url.searchParams.get("limit") ?? 20);
  const offset = Number(url.searchParams.get("offset") ?? 0);
  const filtered = knowledgeBases.filter((knowledgeBase) => {
    if (status && knowledgeBase.status !== status) return false;
    if (q) {
      return [knowledgeBase.name, knowledgeBase.description ?? ""].some((value) =>
        value.includes(q)
      );
    }
    return true;
  });
  const items = filtered.slice(offset, offset + limit);
  return {
    items,
    total: filtered.length,
    limit,
    offset,
    has_next: offset + limit < filtered.length,
  };
}

function assignDocuments(
  state: { knowledgeBases: KnowledgeBaseSummary[]; documents: DocumentSummary[] },
  knowledgeBaseId: string,
  documentIds: string[]
) {
  const knowledgeBase = state.knowledgeBases.find((item) => item.id === knowledgeBaseId);
  if (!knowledgeBase) return;
  for (const document of state.documents) {
    if (!documentIds.includes(document.id)) continue;
    if (document.knowledge_bases.some((item) => item.id === knowledgeBaseId)) continue;
    document.knowledge_bases.push({ id: knowledgeBase.id, name: knowledgeBase.name });
  }
  refreshKnowledgeBaseCounts(state, knowledgeBaseId);
}

function removeDocument(
  state: { knowledgeBases: KnowledgeBaseSummary[]; documents: DocumentSummary[] },
  knowledgeBaseId: string,
  documentId: string
) {
  const document = state.documents.find((item) => item.id === documentId);
  if (!document) return;
  document.knowledge_bases = document.knowledge_bases.filter((item) => item.id !== knowledgeBaseId);
  refreshKnowledgeBaseCounts(state, knowledgeBaseId);
}

function refreshKnowledgeBaseCounts(
  state: { knowledgeBases: KnowledgeBaseSummary[]; documents: DocumentSummary[] },
  knowledgeBaseId: string
) {
  const knowledgeBase = state.knowledgeBases.find((item) => item.id === knowledgeBaseId);
  if (!knowledgeBase) return;
  const documents = state.documents.filter((document) =>
    document.knowledge_bases.some((item) => item.id === knowledgeBaseId)
  );
  knowledgeBase.document_count = documents.length;
  knowledgeBase.indexed_document_count = documents.filter((document) => document.status === "INDEXED").length;
  knowledgeBase.error_document_count = documents.filter((document) => document.status === "ERROR").length;
  knowledgeBase.updated_at = "2026-06-15T00:04:00Z";
}

function makeKnowledgeBase(
  overrides: Partial<KnowledgeBaseSummary> & { id: string; name: string }
): KnowledgeBaseSummary {
  return {
    description: null,
    status: "ACTIVE",
    default_search_mode: "hybrid",
    document_count: 0,
    indexed_document_count: 0,
    error_document_count: 0,
    searchable_chunk_count: 0,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
    archived_at: null,
    ...overrides,
  };
}

function makeDocument(overrides: Partial<DocumentSummary> & { id: string; file_name: string }) {
  return {
    status: "UPLOADED",
    category_name: null,
    content_type: "text/plain",
    file_size_bytes: 128,
    content_sha256: "a".repeat(64),
    duplicate_of_document_id: null,
    uploaded_at: "2026-06-15T00:00:00Z",
    indexed_at: null,
    knowledge_bases: [],
    ...overrides,
  };
}
