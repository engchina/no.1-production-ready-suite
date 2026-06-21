import { expect, type Page, test } from "@playwright/test";
import { expectNoPageOverflow, mockDatabaseReady } from "./_helpers";

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

test.beforeEach(async ({ page }) => {
  await mockDatabaseReady(page);
  await page.route("**/api/auth/me", async (route) => {
    await route.fulfill({ json: authStatus });
  });
});

test("知識ベース管理で作成、文書追加、文書解除、アーカイブができる", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);

  await page.goto("/knowledge-bases");

  // 一覧は list 専用(行は詳細ページへのリンク)。文書管理は詳細ページへ移設済み。
  await expect(page.getByRole("heading", { name: "知識ベース管理" })).toBeVisible();
  await expect(page.getByRole("link", { name: "社内規程" })).toBeVisible();
  await expectNoPageOverflow(page);

  // 作成すると新 KB の詳細ページへ自動遷移する。
  await page.getByRole("textbox", { name: "名前", exact: true }).fill("設計資料");
  await page.getByRole("textbox", { name: "説明", exact: true }).fill("設計レビュー用の資料");
  await page.getByRole("button", { name: "作成" }).click();

  // 作成成功 = 新 KB 詳細ページへの遷移 + 見出しで担保(作成トーストはナビと競合し
  // 自動消滅するため、ここでは判定しない)。
  await expect(page).toHaveURL(/\/knowledge-bases\/kb-2$/);
  await expect(page.getByRole("heading", { name: "設計資料", level: 1 })).toBeVisible();

  // 詳細ページで文書を追加する。
  await page.getByRole("combobox", { name: "文書を追加" }).click();
  await page.getByRole("option", { name: "guide.txt" }).click();
  await page.getByRole("button", { name: "追加" }).click();

  await expect(page.getByText("文書を知識ベースに追加しました。").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "guide.txt" })).toBeVisible();

  // 詳細ページで文書を外す。
  const assignedDocument = page.locator("li").filter({ hasText: "guide.txt" });
  await assignedDocument.getByRole("button", { name: "外す" }).click();
  const removeDialog = page.getByRole("alertdialog", { name: "所属から外しますか？" });
  await expect(removeDialog).toBeVisible();
  await removeDialog.getByRole("button", { name: "外す" }).click();

  await expect(page.getByText("文書を知識ベースから外しました。").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "guide.txt" })).toHaveCount(0);

  // 一覧へ戻ってアーカイブする。
  await page.goto("/knowledge-bases");
  const createdRow = page.locator("tr").filter({ hasText: "設計資料" });
  await createdRow.getByRole("button", { name: "アーカイブ" }).click();
  const archiveDialog = page.getByRole("alertdialog", { name: "知識ベースをアーカイブしますか？" });
  await expect(archiveDialog).toBeVisible();
  await archiveDialog.getByRole("button", { name: "アーカイブ" }).click();

  await expect(page.getByText("知識ベースをアーカイブしました。").first()).toBeVisible();
  // 既定 ACTIVE フィルタなので、アーカイブ済みは一覧から消える。
  await expect(page.getByRole("link", { name: "設計資料" })).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test("狭い画面幅(375px)でもページ全体が横スクロール(崩れ)しない", async ({ page }) => {
  const state = createKnowledgeBaseState();
  await mockKnowledgeBaseApi(page, state);

  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto("/knowledge-bases");

  await expect(page.getByRole("heading", { name: "知識ベース管理" })).toBeVisible();
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

function emptyAdapterConfig() {
  return {
    version: 1,
    ingestion: {
      preprocess_profile: null,
      parser_adapter_backend: null,
      parser_docling_enabled: null,
      parser_marker_enabled: null,
      parser_unstructured_enabled: null,
      chunking_strategy: null,
      chunk_size: null,
      chunk_overlap: null,
      chunk_child_size: null,
      chunk_sentence_window_size: null,
      chunk_min_chars: null,
      graph_profile: null,
      field_extraction_enabled: null,
      asset_summary_enabled: null,
      navigation_summary_enabled: null,
    },
    query: {
      retrieval_strategy: null,
      post_retrieval_pipeline: null,
      generation_profile: null,
      guardrail_policy: null,
      vector_index_profile: null,
      evaluation_suite: null,
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
    const documents = knowledgeBaseId
      ? state.documents.filter((document) =>
          document.knowledge_bases.some((knowledgeBase) => knowledgeBase.id === knowledgeBaseId)
        )
      : state.documents;
    await route.fulfill({
      json: {
        data: {
          items: documents,
          total: documents.length,
          limit: Number(url.searchParams.get("limit") ?? 50),
          offset: Number(url.searchParams.get("offset") ?? 0),
          has_next: false,
        },
        error_messages: [],
        warning_messages: [],
      },
    });
  });
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
