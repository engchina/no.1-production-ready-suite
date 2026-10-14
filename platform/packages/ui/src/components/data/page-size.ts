/** 一覧のページングの既定の 1 ページの件数（10 件）。手書きの PAGE_SIZE を作らず、この定数を使う。 */
export const DEFAULT_PAGE_SIZE = 10;

/**
 * 1 ページの件数（表示件数）の既定の選択肢（#1891）。既定は 10 件。
 * Carbon の advanced pagination・Material のデータ表と同じく、少ない段から 100 件までを選べる。
 */
export const PAGE_SIZE_OPTIONS = [DEFAULT_PAGE_SIZE, 25, 50, 100] as const;
