/**
 * 実行の詳細の事実の値（`RunFactList`）に出す識別子（モデルの ID・実行 ID・日時）の文字。共有の等幅の書体
 * （`--font-mono`）の 12px（`--font-size-xs`）で、`TextareaField` の `monospace`・コードブロックと同じ大きさ。
 * 値の全体が識別子の事実（`mono`）も、本文の値の中に置く識別子（「モデルごとのトークン数」のモデルの ID）も
 * この 1 つを使い、行ごとに大きさが変わらないようにする（#4605）。
 */
export const RUN_FACT_IDENTIFIER_CLASS = "font-mono text-xs";
