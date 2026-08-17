import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const actionResultRegionSource = readFileSync(
  new URL("../src/components/ActionResultRegion.tsx", import.meta.url),
  "utf8",
);
const buttonSource = readFileSync(
  new URL("../src/components/ui/button.tsx", import.meta.url),
  "utf8",
);

test("action result region keeps local actions from forcing page-top scroll", () => {
  assert.match(actionResultRegionSource, /export interface ActionResultRegionProps/u);
  assert.match(actionResultRegionSource, /preserveHeight = true/u);
  assert.match(actionResultRegionSource, /scrollPolicy = "nearest-on-complete"/u);
  assert.match(actionResultRegionSource, /setReservedMinHeight/u);
  assert.match(actionResultRegionSource, /minHeight: reservedMinHeight/u);
});

test("action result region does not render execution timing inside results", () => {
  assert.doesNotMatch(actionResultRegionSource, /TimedLoadingState/u);
  assert.doesNotMatch(actionResultRegionSource, /ProcessingIndicator/u);
  assert.match(actionResultRegionSource, /hasError \|\| hasChildren \|\| \(loading && reservedMinHeight > 0\)/u);
  assert.match(actionResultRegionSource, /aria-busy=\{loading \? "true" : undefined\}/u);
  assert.match(actionResultRegionSource, /\{loading \? null : hasError \?/u);
});

test("action result region can attach recovery actions to local errors", () => {
  assert.match(actionResultRegionSource, /errorAction\?: ReactNode/u);
  assert.match(actionResultRegionSource, /<Banner severity="danger" action=\{errorAction\}>/u);
});

test("action result region uses minimal result/error scroll guidance", () => {
  assert.match(actionResultRegionSource, /operation\.userScrolled/u);
  assert.match(actionResultRegionSource, /scrollIntoView\(\{/u);
  assert.match(actionResultRegionSource, /block: "nearest"/u);
  assert.match(actionResultRegionSource, /inline: "nearest"/u);
  assert.match(actionResultRegionSource, /prefers-reduced-motion: reduce/u);
});

test("app button defaults to non-submit actions unless explicitly overridden", () => {
  assert.match(buttonSource, /type = "button"/u);
  assert.match(buttonSource, /<BaseButton\s+type=\{type\}/u);
  assert.match(buttonSource, /export type ButtonProps = BaseButtonProps/u);
});

test("app button variants restore safe Japanese text line height", () => {
  assert.match(buttonSource, /BUTTON_TEXT_LAYOUT_CLASSNAME = "leading-5"/u);
  assert.match(
    buttonSource,
    /cn\(sharedButtonVariants\(options\), BUTTON_TEXT_LAYOUT_CLASSNAME\)/u
  );
  assert.match(
    buttonSource,
    /className=\{cn\(BUTTON_TEXT_LAYOUT_CLASSNAME, className\)\}/u
  );
  assert.doesNotMatch(buttonSource, /\bleading-none\b/u);
});
