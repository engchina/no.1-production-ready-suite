/**
 * Wiki の図の操作の決め（#5922）。利用者の判断（2026-11-27）で決めた値を 1 か所に置き、変えるときはここだけを直す。
 *
 * - `sampleDocuments`: ナレッジベース詳細の図を開いたときに出す、参照の多い文書の数（少なければすべて）。
 * - `expandBatch`: 1 回の展開で足す隣接の上限（超える分は「さらに展開」）。
 * - `resultPage`: 検索の結果・一覧の 1 ページの既定の件数（3 製品の一覧の既定）。
 * - `knowledgeBaseSearch`: ナレッジベース詳細の図内検索の結果を今の図に足す（add）か置き換える（replace）か。
 * - `wikiSearch`: Wiki 検索の新しい検索で図を置き換えるか。
 * - `activate`: ダブルクリックと Enter で隣接を展開する（expand）か詳細を開く（details）か。
 */
export interface WikiGraphSettings {
  sampleDocuments: number;
  expandBatch: number;
  resultPage: number;
  knowledgeBaseSearch: "add" | "replace";
  wikiSearch: "add" | "replace";
  activate: "expand" | "details";
}

export const WIKI_GRAPH_SETTINGS: Readonly<WikiGraphSettings> = {
  sampleDocuments: 25,
  expandBatch: 25,
  resultPage: 25,
  knowledgeBaseSearch: "add",
  wikiSearch: "replace",
  activate: "expand",
};

export const WIKI_PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const;
