import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiSource = readFileSync(new URL("../src/lib/api.ts", import.meta.url), "utf8");
const databaseSettingsSource = readFileSync(
  new URL("../src/components/settings/DatabaseSettingsClient.tsx", import.meta.url),
  "utf8"
);
const settingsE2eSource = readFileSync(
  new URL("./e2e/nl2sql-system-settings.spec.ts", import.meta.url),
  "utf8"
);
const backendSettingsSchemaSource = readFileSync(
  new URL("../../backend/app/schemas/settings.py", import.meta.url),
  "utf8"
);

test("database connection test status matches backend schema", () => {
  assert.match(
    backendSettingsSchemaSource,
    /DatabaseConnectionTestStatus = Literal\["success", "failed"\]/u
  );
  assert.match(
    apiSource,
    /export type DatabaseConnectionTestStatus = "success" \| "failed";/u
  );
  assert.doesNotMatch(databaseSettingsSource, /status === "skipped"/u);
  assert.doesNotMatch(settingsE2eSource, /status:\s*"skipped"/u);
});
