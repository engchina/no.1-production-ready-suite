/**
 * 設定・入力フォームの読みやすい最大幅。
 * NL2SQL は全画面が wide（#566）のため、1 行 1 入力のフォームは広い画面で入力欄と説明文が 2,000px 超まで伸びる。
 * ページ（PageHeader / PageBody）とカードは wide のまま左端をそろえ、カード内のフォーム部分だけを
 * 共有トークン `--content-max-width`（wide 化前のページ計測幅）で止める。一覧・表・2 ペインには付けない。
 */
export const READABLE_FORM_WIDTH = "max-w-[var(--content-max-width)]";
