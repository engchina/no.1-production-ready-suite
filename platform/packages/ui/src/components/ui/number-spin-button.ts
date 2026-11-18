/**
 * `type="number"` の欄でブラウザの上下の矢印（spin button）が取る幅（px。#5402）。
 *
 * 矢印は見えていないときも幅を取り、placeholder・値の表示の幅を狭める。DOM（`clientWidth` など）からは測れないため、
 * placeholder の切れの検出（部品の `placeholder-fit` と e2e の `findClippedPlaceholders`）はこの値を差し引く。
 * 依存の無い file に置き、React を持たない e2e の helper からも import する。
 *
 * ponytail: Chromium で実測した固定の値（幅 200px の text と number の欄で placeholder の右端が 16px 違う）。
 * 他のブラウザの矢印の幅は違いうる。ずれが問題になったら、ブラウザごとの値か描いた結果での測り方にする。
 */
export const NUMBER_SPIN_BUTTON_WIDTH_PX = 16;

/** 欄が上下の矢印の幅を取るか（`appearance: textfield` で矢印を消した欄は取らない）。 */
export function reservesSpinButton(type: string, appearance: string): boolean {
  return type === "number" && appearance !== "textfield";
}
