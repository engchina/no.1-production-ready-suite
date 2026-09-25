export async function copyTextToClipboard(value: string): Promise<void> {
  const clipboard = globalThis.navigator?.clipboard;
  if (globalThis.isSecureContext && typeof clipboard?.writeText === "function") {
    await clipboard.writeText(value);
    return;
  }
  if (copyTextWithLegacySelection(value)) return;
  throw new Error("Clipboard write is not available in this browser context.");
}

function copyTextWithLegacySelection(value: string): boolean {
  if (
    typeof document === "undefined" ||
    !document.body ||
    typeof document.execCommand !== "function"
  ) {
    return false;
  }

  const activeElement = document.activeElement;
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "");
  textarea.setAttribute("aria-hidden", "true");
  Object.assign(textarea.style, {
    position: "fixed",
    top: "0",
    left: "0",
    width: "1px",
    height: "1px",
    padding: "0",
    border: "0",
    opacity: "0",
    pointerEvents: "none",
  });

  document.body.appendChild(textarea);
  try {
    textarea.focus({ preventScroll: true });
    textarea.select();
    textarea.setSelectionRange(0, value.length);
    return document.execCommand("copy");
  } finally {
    document.body.removeChild(textarea);
    restoreFocus(activeElement);
  }
}

function restoreFocus(element: Element | null): void {
  const focus = (element as { focus?: (options?: FocusOptions) => void } | null)?.focus;
  if (typeof focus !== "function") return;
  try {
    focus.call(element, { preventScroll: true });
  } catch {
    focus.call(element);
  }
}
