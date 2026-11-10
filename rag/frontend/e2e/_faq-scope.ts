import type { Page, Route } from "@playwright/test";

import { mockDatabaseReady, mockLocalAuth } from "./_helpers";

// 承認済み FAQ の対象のナレッジベース・根拠の状態・承認者（#4621）の画面の mock。内容は合成。

export const envelope = (data: unknown) => ({ data, error_messages: [], warning_messages: [] });

const summary = {
  id: "bv-1",
  name: "プロファイル A",
  description: null,
  status: "ACTIVE",
  knowledge_base_count: 2,
  created_at: "2026-09-25T00:00:00Z",
  updated_at: "2026-09-25T00:00:00Z",
  archived_at: null,
};

const detail = {
  ...summary,
  config: { version: 1, knowledge_base_ids: ["kb-1", "kb-2"], query: { answer_engine: "grounded" } },
  knowledge_bases: [
    { id: "kb-1", name: "ナレッジベース A" },
    { id: "kb-2", name: "ナレッジベース B" },
  ],
};

export const knowledgeBases = [
  { id: "kb-1", name: "ナレッジベース A", in_profile: true },
  { id: "kb-2", name: "ナレッジベース B", in_profile: true },
  { id: "kb-3", name: "ナレッジベース C", in_profile: false },
];

const base = { alternate_questions: [], negative_questions: [], status: "approved", source_document_ids: [] };

export const faqRecords = [
  {
    ...base,
    id: "faq-1",
    question: "合成の設定 A はどこで変えますか？",
    answer: "合成の画面 A の設定タブで変えます。",
    target_knowledge_base_ids: ["kb-1", "kb-2"],
    source_document_ids: ["doc-1"],
    approved_by: "管理者 A",
    approved_at: "2026-11-01T02:30:00Z",
    review_reasons: [],
    source_updated: false,
  },
  {
    ...base,
    id: "faq-2",
    question: "合成の手続き B の期限は？",
    answer: "合成の手続き B は申請から 5 日以内です。",
    target_knowledge_base_ids: ["kb-1", "kb-3"],
    approved_by: null,
    approved_at: null,
    review_reasons: ["knowledge_base_removed"],
    source_updated: false,
  },
  {
    ...base,
    id: "faq-3",
    question: "合成の帳票 C の出力方法は？",
    answer: "合成の帳票 C は一覧の出力ボタンから出します。",
    target_knowledge_base_ids: ["kb-2"],
    source_document_ids: ["doc-2"],
    approved_by: "管理者 B",
    approved_at: "2026-10-20T09:00:00Z",
    review_reasons: [],
    source_updated: true,
  },
];

export function faqList(records: unknown[] = faqRecords, settings: Record<string, unknown> = {}) {
  return {
    search_answer_profile_id: "bv-1",
    records,
    enabled: true,
    direct_answer_enabled: false,
    direct_answer_min_score: 0.92,
    knowledge_bases: knowledgeBases,
    ...settings,
  };
}

export async function mockFaqScopeApi(
  page: Page,
  { records = faqRecords as unknown[] }: { records?: unknown[] } = {},
) {
  await mockDatabaseReady(page);
  await mockLocalAuth(page);
  await page.route("**/api/knowledge-bases**", (route) =>
    route.fulfill({ json: envelope({ items: [], total: 0, limit: 50, offset: 0, has_next: false }) }),
  );
  await page.route("**/api/search-answer-profiles**", async (route: Route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/approved-faq") && route.request().method() === "GET") {
      return route.fulfill({ json: envelope(faqList(records)) });
    }
    if (path.endsWith("/domain-keywords")) {
      return route.fulfill({ json: envelope({ search_answer_profile_id: "bv-1", keywords: [] }) });
    }
    if (path.endsWith("/runtime-knowledge")) {
      return route.fulfill({ json: envelope({ search_answer_profile_id: "bv-1", terms: [], rules: [] }) });
    }
    if (path.endsWith("/bv-1")) {
      return route.fulfill({ json: envelope(detail) });
    }
    return route.fulfill({
      json: envelope({ items: [summary], total: 1, limit: 50, offset: 0, has_next: false }),
    });
  });
}
