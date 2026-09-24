export function scrollFocusedControlIntoView(
  element: HTMLElement,
  { focus = false }: { focus?: boolean } = {}
): void {
  const reducedMotion =
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  element.scrollIntoView({
    block: "center",
    inline: "nearest",
    behavior: reducedMotion ? "auto" : "smooth",
  });
  if (focus) {
    element.focus({ preventScroll: true });
  }
}
