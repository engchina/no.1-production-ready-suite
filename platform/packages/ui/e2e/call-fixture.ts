/** 呼び出しの詳細の合成データ（3 製品で同じ契約を確認）。 */
export const syntheticCallDetails = {
  visibility: "owner",
  input: { status: "recorded", text: JSON.stringify({ model: "synthetic-model", input: [{ role: "system", content: "synthetic-system-instructions" }, { role: "user", content: "synthetic-call-input" }] }) },
  output: { status: "recorded", text: JSON.stringify({ model: "synthetic-model", output: [{ type: "reasoning", content: [{ type: "reasoning_text", text: "synthetic-call-thinking" }] }, { type: "message", role: "assistant", content: [{ type: "output_text", text: "synthetic-call-output" }] }], usage: { input_tokens: 10, output_tokens: 20 } }) },
  schema: { status: "truncated", text: '{"type":"object","title":"synthetic-call-schema"}' },
};

/** 工程の本文と時刻の境界を確認する合成の詳細。 */
export const syntheticStageDetails = {
  fields: [{label: "目的", value: "合成の工程"}, {label: "結果", value: "2 件の資料を参照しました"}, {label: "影響", value: "参照資料で回答を作ります"}],
  lines: ["synthetic-stage-detail", "合成の項目 B"],
};

/** 一覧のpreviewより後ろにある末尾まで、実画面の分段読取を検査する。 */
export function syntheticFullCallBody() {
  const text = JSON.stringify({model: "synthetic-model", input: [{role: "user", content: "合成の入力。".repeat(12000) + "全文の末尾"}]});
  const size = 8192;
  const count = Math.ceil(text.length / size);
  const reference = {body_id: "a".repeat(32), sha256: "b".repeat(64), character_count: text.length, byte_count: new TextEncoder().encode(text).length, part_count: count, visibility: "owner"};
  const parts = Array.from({length: count}, (_, index) => ({index, start: index * size, end: Math.min((index + 1) * size, text.length), text: text.slice(index * size, (index + 1) * size), sha256: "c".repeat(64)}));
  return {
    call: {...syntheticCallDetails, input: {status: "recorded", text: text.slice(0, 64000), body_reference: reference, preview_only: true}},
    page: (first: number) => ({reference, status: "complete", failure_code: null,
      parts: parts.slice(first, first + 8), next_part: first + 8 < count ? first + 8 : null}),
  };
}
