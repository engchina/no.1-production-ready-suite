import assert from "node:assert/strict";
import test from "node:test";

import { copyTextToClipboard } from "../src/lib/clipboard.ts";

const ORIGINAL_DOCUMENT = Object.getOwnPropertyDescriptor(globalThis, "document");
const ORIGINAL_NAVIGATOR = Object.getOwnPropertyDescriptor(globalThis, "navigator");
const ORIGINAL_IS_SECURE_CONTEXT = Object.getOwnPropertyDescriptor(globalThis, "isSecureContext");

function restoreClipboardGlobals() {
  restoreGlobalProperty("document", ORIGINAL_DOCUMENT);
  restoreGlobalProperty("navigator", ORIGINAL_NAVIGATOR);
  restoreGlobalProperty("isSecureContext", ORIGINAL_IS_SECURE_CONTEXT);
}

function restoreGlobalProperty(name: string, descriptor: PropertyDescriptor | undefined) {
  if (descriptor) {
    Object.defineProperty(globalThis, name, descriptor);
  } else {
    Reflect.deleteProperty(globalThis, name);
  }
}

test("非 secure context では legacy copy fallback を使う", async (t) => {
  t.after(restoreClipboardGlobals);
  let selected = false;
  let selectionEnd = -1;
  let copied = false;
  let focusRestored = false;
  const textarea = {
    value: "",
    style: {},
    setAttribute() {},
    focus() {},
    select() {
      selected = true;
    },
    setSelectionRange(_start: number, end: number) {
      selectionEnd = end;
    },
  };
  const activeElement = {
    focus() {
      focusRestored = true;
    },
  };
  const fakeDocument = {
    activeElement,
    body: {
      appendChild(node: typeof textarea) {
        assert.equal(node, textarea);
      },
      removeChild(node: typeof textarea) {
        assert.equal(node, textarea);
      },
    },
    createElement(tagName: string) {
      assert.equal(tagName, "textarea");
      return textarea;
    },
    execCommand(command: string) {
      assert.equal(command, "copy");
      assert.equal(textarea.value, "copy over http");
      copied = true;
      return true;
    },
  };

  Object.defineProperty(globalThis, "isSecureContext", { configurable: true, value: false });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { clipboard: { writeText: () => Promise.reject(new Error("insecure")) } },
  });
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: fakeDocument as unknown as Document,
  });

  await copyTextToClipboard("copy over http");

  assert.equal(selected, true);
  assert.equal(selectionEnd, "copy over http".length);
  assert.equal(copied, true);
  assert.equal(focusRestored, true);
});

test("secure context では Clipboard API を優先する", async (t) => {
  t.after(restoreClipboardGlobals);
  let copiedValue = "";
  let legacyCopyCount = 0;

  Object.defineProperty(globalThis, "isSecureContext", { configurable: true, value: true });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      clipboard: {
        writeText: async (value: string) => {
          copiedValue = value;
        },
      },
    },
  });
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { execCommand: () => ++legacyCopyCount > 0 } as unknown as Document,
  });

  await copyTextToClipboard("secure copy");

  assert.equal(copiedValue, "secure copy");
  assert.equal(legacyCopyCount, 0);
});

test("secure context の Clipboard API 失敗は既存のエラー表示へ渡す", async (t) => {
  t.after(restoreClipboardGlobals);
  let legacyCopyCount = 0;

  Object.defineProperty(globalThis, "isSecureContext", { configurable: true, value: true });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      clipboard: {
        writeText: async () => {
          throw new Error("clipboard denied");
        },
      },
    },
  });
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { execCommand: () => ++legacyCopyCount > 0 } as unknown as Document,
  });

  await assert.rejects(copyTextToClipboard("secure copy"), /clipboard denied/u);
  assert.equal(legacyCopyCount, 0);
});
