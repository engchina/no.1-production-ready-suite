/** 製品の画面で同じ回答・資料・版の推移を確かめる合成データ。 */
import type { FileReviewState } from "../src/components/feedback/file-review-model";

export const reviewQuestion = "操作 A の順序と確認方法を教えてください。";
export const firstAnswer = "操作 A は設定画面から開始します。確認方法は資料から特定できませんでした。";
export const nextAnswer = "操作 A は設定画面から開始し、保存後に確認画面で結果を確認します。";
function hit(id: string, file: string, n: number, p: number, text: string, cited = false) {
  return {
    document_id: id,
    chunk_id: id + ":" + n,
    file_name: file,
    score: 0.8,
    rerank_score: 0.85,
    text,
    excerpt: text,
    cited,
    used_in_answer: cited,
    metadata: { page_number: p, evidence_model_used: cited, evidence_retrieval_rank: n },
    locator: { page_start: p, source_line_start: n },
    section_title: "操作 A",
  };
}
export const originalHits = [
  hit("doc-a", "操作ガイド A.pdf", 2, 2, "設定画面から操作 A を開始します。", true),
  hit("doc-a", "操作ガイド A.pdf", 1, 2, "設定画面を開きます。"),
  hit("doc-a", "操作ガイド A.pdf", 3, 5, "保存した画面に戻ります。"),
  hit("doc-b", "一覧 B.txt", 1, 1, "画面 B の項目を一覧表示します。"),
];
const freshHits = [
  hit("doc-c", "確認手順 C.pdf", 1, 1, "保存後、確認画面で結果を確認します。"),
  hit("doc-d", "資料 D.txt", 1, 1, "画面 D の入力項目です。"),
];
export function reviewSnapshots(id: string, task: string) {
  const source = { title: reviewQuestion, trace_id: id, citations: originalHits, answer: firstAnswer };
  const initial: FileReviewState = {
    revision: 0,
    kept: {},
    seen: ["doc-a", "doc-b"],
    excluded: {},
    pending: {},
    versions: [{ id, answer: firstAnswer, sources: { [task]: source }, rating: null }],
  };
  const unresolved: FileReviewState = {
    ...initial,
    versions: [{ ...initial.versions[0], rating: "not_helpful" }],
  };
  const pending: FileReviewState = {
    ...unresolved,
    revision: 1,
    kept: { [task]: ["doc-a"] },
    excluded: { "doc-b": "一覧 B.txt" },
    seen: ["doc-a", "doc-b", "doc-c", "doc-d"],
    pending: { [task]: { ...source, answer: "", citations: [...originalHits.slice(0, 3), ...freshHits] } },
  };
  const hits = [
    ...originalHits.slice(0, 3),
    {
      ...freshHits[0],
      cited: true,
      used_in_answer: true,
      metadata: { ...freshHits[0].metadata, evidence_model_used: true },
    },
  ];
  const next: FileReviewState = {
    ...pending,
    revision: 2,
    kept: { [task]: ["doc-a", "doc-c"] },
    excluded: { ...pending.excluded, "doc-d": "資料 D.txt" },
    pending: {},
    versions: [
      ...unresolved.versions,
      {
        id: "answer-2",
        answer: nextAnswer,
        sources: { [task]: { ...source, answer: nextAnswer, trace_id: "answer-2", citations: hits } },
        rating: null,
      },
    ],
  };
  return { initial, unresolved, pending, next };
}
