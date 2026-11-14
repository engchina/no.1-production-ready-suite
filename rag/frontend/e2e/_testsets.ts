import type { Page } from "@playwright/test";
import type { TestSetDraft, TestSetItem } from "../src/lib/testset-api";
import { mockLocalAuth } from "./_helpers";
import { mockEvaluationProfiles, evaluationImportResult } from "./_evaluation-jobs";

export async function mockTestSetPage(page: Page, itemCount = 2) {
  await mockLocalAuth(page);
  await mockEvaluationProfiles(page);
  await page.route("**/api/settings/evaluation-suite", route => route.fulfill({ json: { data: { suite: "standard", thresholds: {}, suites: [], config_source: "runtime" }, error_messages: [], warning_messages: [] } }));
  let saved: TestSetDraft | null = null;
  const inputs: TestSetItem[] = Array.from({ length: itemCount }, (_, index) => ({
    item_id: `item-${index}`, source_job_id: index % 2 ? "job-two" : "job-one", source_case_id: "row-2", source_pass: 0,
    question: "合成の同じ質問", standard_answer: "", evidence: [], required_answer_points: [],
    answer_process: "", confirmed: false, source_notice: null,
    original: { question: "合成の同じ質問", standard_answer: "", evidence: [], required_answer_points: [], answer_process: "" },
    reference_answer: "未確認の合成回答", reference_evidence: [],
  }));
  await page.route("**/api/evaluation/testsets**", async route => {
    const path = new URL(route.request().url()).pathname.split("/testsets")[1] ?? "";
    const method = route.request().method();
    const reply = (data: unknown, status = 200) => route.fulfill({ status, json: { data, error_messages: [], warning_messages: [] } });
    if (path === "/sources") return reply(["job-one", "job-two"].map(job_id => ({ job_id, created_at: "2026-11-01T00:00:00Z", finished_at: "2026-11-01T00:01:00Z", total_cases: inputs.filter(item => item.source_job_id === job_id).length })));
    if (path === "/sources/items") return reply(inputs.filter(item => route.request().postDataJSON().job_ids.includes(item.source_job_id)));
    if (path === "/import") return reply(inputs.map(value => ({ ...value, source_kind: "excel", standard_answer: "合成の標準回答", reference_answer: "" })));
    if (path.endsWith("/prepare") && saved) { const confirmed = saved.items.filter(value => value.confirmed); const value = evaluationImportResult(); return reply({ ...value, row_count: confirmed.length, cases: confirmed.map((item, i) => ({ id: `row-${i + 2}`, query: item.question, standard_answer: item.standard_answer, relevant_document_ids: [] })), rows: confirmed.map((item, i) => ({ ...value.rows[0], row: i + 2, case_id: `row-${i + 2}`, question: item.question, standard_answer_preview: item.standard_answer })), source_workbook_id: "workbook-synthetic", search_answer_profile_id: route.request().postDataJSON().search_answer_profile_id }); }
    if (!path && method === "GET") return reply(saved ? [{ draft_id: saved.draft_id, name: saved.name, revision: saved.revision, updated_at: saved.updated_at, item_count: saved.items.length, confirmed_count: saved.items.filter(value => value.confirmed).length }] : []);
    if (!path && method === "POST") {
      const body = route.request().postDataJSON();
      saved = { draft_id: "draft-a", name: body.name, revision: 1, created_at: "2026-11-01T00:02:00Z", updated_at: "2026-11-01T00:02:00Z", items: body.source_kind && body.source_kind !== "evaluation" ? body.items.map((value: object, index: number) => ({ ...inputs[0], ...value, item_id: `manual-${index}`, source_kind: body.source_kind, reference_answer: "", confirmed: false })) : inputs.filter(value => body.item_ids.includes(value.item_id)) };
      return reply(saved, 201);
    }
    if (path === "/draft-a" && method === "GET") return reply(saved);
    if (path === "/draft-a" && method === "PUT" && saved) {
      const body = route.request().postDataJSON();
      saved = { ...saved, ...body, revision: saved.revision + 1, items: body.items.map((value: TestSetItem) => {
        const previous = saved?.items.find(entry => entry.item_id === value.item_id);
        const base: TestSetItem = previous ?? { ...inputs[0], source_kind: "manual", source_job_id: "", source_case_id: "", reference_answer: "", reference_evidence: [], original: { question: value.question, standard_answer: value.standard_answer, evidence: value.evidence, required_answer_points: value.required_answer_points, answer_process: value.answer_process } };
        return { ...base, ...value, confirmed: body.confirm_item_ids?.includes(value.item_id) || value.confirmed };
      }) };
      return reply(saved);
    }
    if (path.endsWith("export.xlsx")) return route.fulfill({ body: Buffer.from("synthetic-download"), contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers: { "content-disposition": 'attachment; filename="evaluation_testset.xlsx"' } });
    return route.fulfill({ status: 404, json: { data: null, error_messages: [{ message: "見つかりません。" }], warning_messages: [] } });
  });
  await page.route("**/api/evaluation/models", route => route.fulfill({ json: { data: [{ model_id: "synthetic-model", display_name: "合成モデル", kind: "text" }], error_messages: [], warning_messages: [] } }));
  return { getSaved: () => saved, setSaved: (value: TestSetDraft) => { saved = value; } };
}
