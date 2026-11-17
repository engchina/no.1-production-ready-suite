interface ProgressCardLocator {
  evaluate<R>(fn: (node: HTMLElement | SVGElement) => R): Promise<R>;
}

/** 進度の外枠と、回答・操作の配置を実画面から調べる（3製品共通）。 */
export async function progressCardLayout(card: ProgressCardLocator) {
  return card.evaluate(node => {
    const timeline = node.querySelector<HTMLElement>("[data-run-timeline]");
    const list = timeline?.querySelector("ol");
    const footer = node.querySelector("[data-run-timeline-footer]");
    const explore = Array.from(timeline?.querySelectorAll("button") ?? []).find(button => button.textContent?.includes("軌跡を調べる"));
    const exploreBox = explore?.getBoundingClientRect();
    const timelineBox = timeline?.getBoundingClientRect();
    const style = getComputedStyle(node);
    return {
      card: node.hasAttribute("data-run-progress-card"),
      separateFromAnswer: node.closest("[data-chat-answer-card]") === null,
      // 「軌跡を調べる」は一覧の上の右（軌跡の調査の「経過に戻る」と同じ位置。#5360）。
      explorerTopRight: Boolean(list && explore && exploreBox && timelineBox
        && (explore.compareDocumentPosition(list) & Node.DOCUMENT_POSITION_FOLLOWING)
        && Math.abs(exploreBox.right - timelineBox.right) <= 1),
      // 処理の詳細などは一覧の後ろ。
      detailsAfterSteps: Boolean(list && (!footer || (list.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING))),
      bordered: parseFloat(style.borderTopWidth) > 0,
      padded: parseFloat(style.paddingTop) > 0,
      rounded: parseFloat(style.borderTopLeftRadius) > 0,
    };
  });
}
