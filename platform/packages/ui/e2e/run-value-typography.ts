/** 実際の製品画面に表示された技術時刻の契約。表示値や時間帯は書き換えない。 */
export async function expectRunValueTypography(inspector: {
  evaluate<R>(fn: (element: SVGElement | HTMLElement) => R): Promise<R>;
}) {
  const result = await inspector.evaluate(root => {
    const times = Array.from(root.querySelectorAll("[data-trace-timing] time"));
    const duration = root.querySelector("[data-trace-duration]");
    return {
      times: times.map(element => ({
        font: getComputedStyle(element).fontFamily,
        mono: element.closest("dd")?.classList.contains("font-mono"),
        labelMono: element.closest("dd")?.previousElementSibling?.classList.contains("font-mono"),
      })),
      summary: Array.from(document.querySelectorAll("[data-run-fact]")).map(row => ({ id: row.getAttribute("data-run-fact"), dateTime: row.querySelector("time")?.getAttribute("datetime"), mono: row.querySelector("dd")?.classList.contains("font-mono"), tabular: row.querySelector("dd")?.classList.contains("tnum") })),
      numeric: duration ? { font: getComputedStyle(duration).fontFamily, body: getComputedStyle(document.documentElement).fontFamily, variant: getComputedStyle(duration).fontVariantNumeric } : null,
    };
  });
  for (const fact of result.summary) {
    if (fact.id === "startedAt" || fact.id === "finishedAt") {
      if (fact.mono !== Boolean(fact.dateTime)) throw new Error("概要の実時刻と欠測文言の書体が不一致です。");
    } else if (["kind", "executor", "owner", "target", "engine", "errorStage", "rating", "comment"].includes(fact.id ?? "")) {
      if (fact.mono || fact.tabular) throw new Error("概要の通常文字に技術値の書体が付いています。");
    }
  }
  if (result.times.length !== 2) throw new Error("合成の開始・終了時刻が2件必要です。");
  for (const time of result.times) {
    if (!time.mono || !time.font.includes("Google Sans Code") || time.labelMono) throw new Error("実時刻だけを共通の等幅書体にする必要があります。");
  }
  if (!result.numeric || result.numeric.font !== result.numeric.body || !result.numeric.variant.includes("tabular-nums")) throw new Error("所要時間は本文書体と等幅数字にする必要があります。");
}
