/** RAG と Agent が返す資料選択の契約。根拠の表示には製品ごとのカードを使う。 */
export type AnswerRating = "helpful" | "not_helpful";
export interface ReviewCitation {
  document_id: string;
  chunk_id: string;
  file_name?: string | null;
  text?: string;
  excerpt?: string;
  truncated?: boolean;
  metadata?: Record<string, unknown>;
  locator?: Record<string, unknown> | null;
  retrieval_rank?: number | null;
  used_in_answer?: boolean;
  cited?: boolean;
}
export interface ReviewSource {
  title: string;
  trace_id: string;
  citations: ReviewCitation[];
  answer: string;
  notice?: string | null;
}
export interface ReviewVersion {
  id: string;
  answer: string;
  notice?: string | null;
  sources: Record<string, ReviewSource>;
  rating: AnswerRating | null;
}
export interface FileReviewState {
  revision: number;
  versions: ReviewVersion[];
  kept: Record<string, string[]>;
  seen: string[];
  excluded: Record<string, string>;
  pending: Record<string, ReviewSource>;
}
export interface FileReviewAction {
  revision: number;
  task: string;
  action: "answer" | "discover" | "reset" | "restore";
  selected: string[];
}
export const FILE_REVIEW_JA = {
  "fileReview.question": "この回答で質問は解決しましたか？",
  "fileReview.helpful": "解決した",
  "fileReview.notHelpful": "未解決",
  "fileReview.unrated": "未評価",
  "fileReview.title": "資料を選んで再検索",
  "fileReview.task": "見直す検索を選ぶ",
  "fileReview.candidates": "回答に使う資料を選ぶ",
  "fileReview.method": "今回の資料で質問に答えられそうですか？",
  "fileReview.only": "選んだ資料だけで答え直す",
  "fileReview.mix": "選んだ資料を残し、ほかも探す",
  "fileReview.replace": "今回の資料を外して探し直す",
  "fileReview.replaceNew": "前回の選択以外を入れ替える",
  "fileReview.given": "AI に渡した資料（{count} 件）",
  "fileReview.found": "候補ファイル（{count} 件）",
  "fileReview.filename": "ファイル名で絞り込み",
  "fileReview.placeholder": "ファイル名の一部を入力",
  "fileReview.clear": "絞り込みを解除",
  "fileReview.showing": "{shown} 件表示 ／ 全 {total} 件",
  "fileReview.locations": "{count} 箇所",
  "fileReview.cited": "{count} 箇所を引用",
  "fileReview.noCites": "引用なし",
  "fileReview.used": "回答で引用",
  "fileReview.original": "原文",
  "fileReview.back": "戻る",
  "fileReview.close": "閉じる",
  "fileReview.confirm": "選んだ資料だけで再回答",
  "fileReview.keepFind": "選択した{count}件を残して、ほかの候補を探す",
  "fileReview.replaceFind": "今回の資料を除外して、ほかの候補を探す",
  "fileReview.find": "候補を探す",
  "fileReview.more": "ほかの候補を探す",
  "fileReview.reset": "この質問を最初から探す",
  "fileReview.manage": "除外した資料（{count} 件）",
  "fileReview.restore": "候補に戻す",
  "fileReview.counts": "残す {keep} 件 ／ 今回外す {remove} 件 ／ 累計除外 {total} 件",
  "fileReview.showHidden": "全件表示（除外予定 {count} 件が非表示）",
  "fileReview.none": "新しい候補はありません。",
  "fileReview.noMatch": "一致するファイルはありません。",
  "fileReview.version": "回答 {version}",
  "fileReview.error": "操作を完了できませんでした。",
  "fileReview.retry": "再読み込み",
} as const;
export type ReviewText = (key: keyof typeof FILE_REVIEW_JA, params?: Record<string, string | number>) => string;

export function cited(hit: ReviewCitation) {
  return hit.cited ?? hit.used_in_answer ?? hit.metadata?.evidence_model_used === true;
}
export function locator(hit: ReviewCitation) {
  return hit.locator ?? hit.metadata ?? {};
}
export function page(hit: ReviewCitation) {
  const loc = locator(hit);
  return Number(loc.page_start ?? loc.page_number ?? loc.page) || 0;
}
export function position(hit: ReviewCitation) {
  const loc = locator(hit);
  return (
    Number(
      loc.source_line_start ??
        loc.line_start ??
        loc.row_start ??
        loc.char_start ??
        loc.chunk_index ??
        hit.chunk_id.match(/:(\d+)$/)?.[1],
    ) || 0
  );
}
export function files(citations: ReviewCitation[]) {
  const groups = new Map<string, ReviewCitation[]>();
  const unique = new Set<string>();
  for (const hit of citations) {
    const key = JSON.stringify([hit.document_id, hit.chunk_id]);
    if (unique.has(key)) continue;
    unique.add(key);
    const group = groups.get(hit.document_id);
    if (group) group.push(hit);
    else groups.set(hit.document_id, [hit]);
  }
  return [...groups].map(([id, hits]) => ({
    id,
    hits,
    name: hits[0].file_name || id,
    excerpt: [...hits].sort(
      (a, b) =>
        Number(cited(b)) - Number(cited(a)) ||
        Number(a.retrieval_rank ?? a.metadata?.evidence_retrieval_rank ?? Infinity) -
          Number(b.retrieval_rank ?? b.metadata?.evidence_retrieval_rank ?? Infinity),
    )[0],
    pages: [...new Set(hits.map(page).filter(Boolean))].sort((a, b) => a - b),
  }));
}
