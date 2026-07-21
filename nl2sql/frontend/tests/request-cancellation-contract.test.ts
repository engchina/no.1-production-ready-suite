import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { extname } from "node:path";
import test from "node:test";

const sourceRoot = new URL("../src/", import.meta.url);

function sourceFiles(directory: URL): URL[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const child = new URL(`${entry.name}${entry.isDirectory() ? "/" : ""}`, directory);
    if (entry.isDirectory()) return sourceFiles(child);
    return [".ts", ".tsx"].includes(extname(entry.name)) ? [child] : [];
  });
}

test("API helpers propagate AbortSignal", () => {
  const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");

  for (const helper of ["apiGet", "apiGetWithMetadata", "apiPost", "apiPatch", "apiDelete"]) {
    const declaration = new RegExp(
      String.raw`function ${helper}[\s\S]*?options: ApiRequestOptions = \{\}[\s\S]*?signal: options\.signal`,
      "u"
    );
    assert.match(apiSource, declaration, `${helper} must accept and forward signal`);
  }
});

test("direct API calls in useEffect include cancellation", () => {
  const violations: string[] = [];
  const apiCall = /\b(?:apiGet|apiPost|apiPatch|apiDelete|apiFetch|securityApi\.|api\.)/u;
  const cancellationGuard = /\b(?:AbortController|useRequestScope|signal)\b/u;

  for (const file of sourceFiles(sourceRoot)) {
    if (!file.pathname.endsWith(".tsx")) continue;
    const source = readFileSync(file, "utf8");
    const lines = source.split("\n");
    for (let index = 0; index < lines.length; index += 1) {
      if (!lines[index].includes("useEffect(")) continue;
      const block: string[] = [];
      for (let cursor = index; cursor < Math.min(lines.length, index + 120); cursor += 1) {
        block.push(lines[cursor]);
        if (
          /^\s*\}\s*,\s*\[/u.test(lines[cursor]) ||
          /^\s*\},\s*\[/u.test(lines[cursor])
        ) {
          break;
        }
      }
      const body = block.join("\n");
      if (apiCall.test(body) && !cancellationGuard.test(body)) {
        violations.push(file.pathname.replace(sourceRoot.pathname, "src/"));
      }
    }
  }

  assert.deepEqual([...new Set(violations)].sort(), []);
});
