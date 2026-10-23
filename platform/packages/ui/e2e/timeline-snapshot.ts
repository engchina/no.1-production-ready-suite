interface TimelineContainer {
  evaluate<R>(fn: (node: HTMLElement | SVGElement) => R): Promise<R>;
}

/** 操作画面と履歴の工程の内容・階層・状態・時間を、表示順のまま比較する。 */
export async function timelineSnapshot(container: TimelineContainer) {
  return container.evaluate((node) => Array.from(node.querySelectorAll("[data-run-timeline-step]"), (row) => ({
    level: Number(row.getAttribute("data-level") ?? 0),
    status: row.getAttribute("data-status"),
    text: (row.firstElementChild as HTMLElement).innerText.replace(/\s+/g, " ").trim(),
  })));
}
