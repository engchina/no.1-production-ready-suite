/** 読み込み済みの図のラベルを照合する。検索APIへ送る質問の変換には使わない。 */
export function normalizeGraphSearch(text: string): string {
  return text.normalize("NFKC").toLowerCase();
}
