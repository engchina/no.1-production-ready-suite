interface ProgressCardLocator {
  evaluate<R>(fn: (node: HTMLElement | SVGElement) => R): Promise<R>;
}

/** 進度の外枠と、回答・操作の配置を実画面から調べる（3製品共通）。 */
export async function progressCardLayout(card: ProgressCardLocator) {
  return card.evaluate(node => {
    const list = node.querySelector("[data-run-timeline] ol");
    const footer = node.querySelector("[data-run-timeline-footer]");
    const style = getComputedStyle(node);
    return {
      card: node.hasAttribute("data-run-progress-card"),
      separateFromAnswer: node.closest("[data-chat-answer-card]") === null,
      actionsAfterSteps: Boolean(list && footer && (list.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING)),
      visibleFooter: Boolean(footer && footer.getBoundingClientRect().height > 0),
      bordered: parseFloat(style.borderTopWidth) > 0,
      padded: parseFloat(style.paddingTop) > 0,
      rounded: parseFloat(style.borderTopLeftRadius) > 0,
    };
  });
}
