import { request, requestFile } from "./api";

export interface TestSetFields {
  question: string;
  standard_answer: string;
  evidence: string[];
  required_answer_points: string[];
  response_decision: "answer" | "clarify" | "hold";
  answer_process: string;
}
export interface TestSetItem extends TestSetFields {
  item_id: string;
  confirmed: boolean;
  source_job_id: string;
  source_case_id: string;
  source_pass: number;
  original: TestSetFields;
  reference_answer: string;
  reference_evidence: Record<string, unknown>[];
  source_notice: string | null;
}
export interface TestSetDraft {
  draft_id: string;
  name: string;
  revision: number;
  created_at: string;
  updated_at: string;
  items: TestSetItem[];
}
export interface TestSetSummary {
  draft_id: string;
  name: string;
  revision: number;
  updated_at: string;
  item_count: number;
  confirmed_count: number;
}
export interface TestSetSource {
  job_id: string;
  created_at: string;
  finished_at: string | null;
  total_cases: number;
}
const ROOT = "/api/evaluation/testsets";
const json = (method: string, value: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(value),
});
export const testsetApi = {
  list: () => request<TestSetSummary[]>(ROOT),
  sources: () => request<TestSetSource[]>(`${ROOT}/sources`),
  candidates: (job_ids: string[]) => request<TestSetItem[]>(`${ROOT}/sources/items`, json("POST", { job_ids })),
  create: (name: string, job_ids: string[], item_ids: string[]) => request<TestSetDraft>(ROOT, json("POST", { name, job_ids, item_ids })),
  get: (id: string) => request<TestSetDraft>(`${ROOT}/${encodeURIComponent(id)}`),
  save: (draft: TestSetDraft) => request<TestSetDraft>(`${ROOT}/${encodeURIComponent(draft.draft_id)}`, json("PUT", {
    name: draft.name, revision: draft.revision,
    items: draft.items.map(({ item_id, question, standard_answer, evidence, required_answer_points, response_decision, answer_process, confirmed }) => ({
      item_id, question, standard_answer, evidence, required_answer_points, response_decision, answer_process, confirmed,
    })),
  })),
  download: (draft: TestSetDraft) => requestFile(`${ROOT}/${encodeURIComponent(draft.draft_id)}/export.xlsx?revision=${draft.revision}`, "evaluation_testset.xlsx"),
};
