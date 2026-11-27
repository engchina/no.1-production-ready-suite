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
  method_tasks?: string[];
}
export interface FileReviewAction {
  revision: number;
  task: string;
  action: "answer" | "discover" | "reset" | "restore" | "back";
  selected: string[];
}
export const FILE_REVIEW_JA = {
  "fileReview.question": "この回答で質問は解決しましたか？",
  "fileReview.helpful": "解決した",
  "fileReview.notHelpful": "未解決",
  "fileReview.unrated": "未評価",
  "fileReview.details": "理由と修正した回答を記録（任意）",
  "fileReview.title": "資料を選んで再検索",
  "fileReview.task": "見直す検索を選ぶ",
  "fileReview.candidates": "回答に使う資料を選ぶ",
  "fileReview.method": "今回の資料で質問に答えられそうですか？",
  "fileReview.only": "選んだ資料だけで答え直す",
  "fileReview.mix": "選んだ資料を残し、ほかも探す",
  "fileReview.replace": "今回の資料を外して探し直す",
  "fileReview.replaceNew": "前回の選択以外を入れ替える",
  "fileReview.given": "AI に渡した資料",
  "fileReview.selected": "選択した資料",
  "fileReview.remove": "{name}を外す",
  "fileReview.found": "候補ファイル",
  "fileReview.filename": "ファイル名で絞り込み",
  "fileReview.placeholder": "ファイル名の一部を入力",
  "fileReview.clear": "絞り込みを解除",
  "fileReview.all": "全 {total} 件",
  "fileReview.showing": "表示 {shown}/{total} 件",
  "fileReview.morePages": "{pages}、ほか {count} ページ",
  "fileReview.locations": "{count} 箇所",
  "fileReview.cited": "{count} 箇所を引用",
  "fileReview.noCites": "引用なし",
  "fileReview.used": "回答で引用",
  "fileReview.original": "原文",
  "fileReview.back": "戻る",
  "fileReview.close": "閉じる",
  "fileReview.confirm": "選んだ資料だけで再回答",
  "fileReview.keepFind": "選んだ資料を残して、ほかの候補を探す",
  "fileReview.replaceFind": "今回の資料を除外して、ほかの候補を探す",
  "fileReview.find": "候補を探す",
  "fileReview.more": "ほかの候補を探す",
  "fileReview.reset": "この質問を最初から探す",
  "fileReview.manage": "除外した資料（{count} 件）",
  "fileReview.restore": "候補に戻す",
  "fileReview.selectionCount": "{scope} · 選択 {keep} 件",
  "fileReview.exclusionCount": "今回除外 {remove} 件 · 累計除外 {total} 件",
  "fileReview.exclusionWithHidden": "今回除外 {remove} 件（うち非表示 {hidden} 件） · 累計除外 {total} 件",
  "fileReview.showHidden": "全件表示",
  "fileReview.none": "新しい候補はありません。",
  "fileReview.noMatch": "一致するファイルはありません。",
  "fileReview.version": "回答 {version}",
  "fileReview.loading": "回答と資料を読み込んでいます",
  "fileReview.discovering": "ほかの候補を検索しています",
  "fileReview.answering": "選んだ資料で回答を作成しています",
  "fileReview.reading": "原文を読み込んでいます",
  "fileReview.restoring": "資料の選択を更新しています",
  "fileReview.restarting": "この質問の資料を最初から検索しています",
  "fileReview.returning": "検索前の選択に戻しています",
  "fileReview.resetDescription": "この質問の資料の選択と除外をすべて解除し、もう一度検索します。",
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

function pageRange(hit: ReviewCitation) {
  const start = page(hit);
  if (!Number.isSafeInteger(start) || start < 1) return null;
  const end = Number(locator(hit).page_end);
  return { start, end: Number.isSafeInteger(end) && end >= start ? end : start };
}

function rangeText({ start, end }: { start: number; end: number }) {
  return start === end ? String(start) : `${start}–${end}`;
}

export function passagePageLabel(hit: ReviewCitation) {
  const range = pageRange(hit);
  return range ? "p." + rangeText(range) : "";
}

/** 重なる頁と隣接する頁だけをまとめる。間の未命中の頁は含めない。 */
function filePages(hits: ReviewCitation[]) {
  const ranges = hits.map(pageRange).filter(range => range !== null).sort((a, b) => a.start - b.start);
  const merged: typeof ranges = [];
  for (const range of ranges) {
    const previous = merged.at(-1);
    if (previous && range.start <= previous.end + 1) previous.end = Math.max(previous.end, range.end);
    else merged.push(range);
  }
  const displayed = merged.slice(0, 3).map(rangeText);
  return {
    label: displayed.length ? "p." + displayed.join("、") : "",
    remaining: merged.slice(displayed.length).reduce((count, range) => count + range.end - range.start + 1, 0),
  };
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
    pages: filePages(hits),
  }));
}
