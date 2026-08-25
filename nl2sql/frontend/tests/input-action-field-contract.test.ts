import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../src/components/ui/input-action-field.tsx", import.meta.url),
  "utf8"
);

test("InputActionField keeps label, field errors, and action layout centralized", () => {
  assert.match(source, /export interface InputActionFieldProps/u);
  assert.match(source, /export interface InputActionFieldAction/u);
  assert.match(source, /<FieldLabel[\s\S]*htmlFor=\{id\}[\s\S]*label=\{label\}/u);
  assert.match(source, /aria-describedby=\{inputDescribedBy\}/u);
  assert.match(source, /<FieldError id=\{errorId\} message=\{error\} \/>/u);
  assert.match(source, /<FieldError id=\{actionErrorId\} message=\{actionError\} \/>/u);
});

test("InputActionField uses the shared Button and 44px input/action sizing", () => {
  assert.match(source, /import \{ Button, type ButtonProps \} from "\.\/button"/u);
  assert.match(source, /grid min-w-0 gap-2 sm:grid-cols-\[minmax\(0,1fr\)_auto\]/u);
  assert.match(source, /"h-11 w-full min-h-\[44px\] rounded-md border/u);
  assert.match(
    source,
    /<Button[\s\S]*size="lg"[\s\S]*className=\{cn\("h-11 w-full whitespace-nowrap min-h-\[44px\]"/u
  );
  assert.match(source, /min-h-\[44px\]/u);
  assert.match(source, /Button spec/u);
});
