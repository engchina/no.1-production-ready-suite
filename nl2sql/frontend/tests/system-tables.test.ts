import assert from "node:assert/strict";
import test from "node:test";

import {
  systemTableControlsBusy,
  systemTableOperationMessageKey,
  systemTableStatusLabelKey,
} from "../src/lib/system-tables.ts";

test("system table status keys cover all four schema states", () => {
  assert.deepEqual(
    (["missing", "partial", "outdated", "ready"] as const).map(
      systemTableStatusLabelKey
    ),
    [
      "settings.database.systemTables.status.missing",
      "settings.database.systemTables.status.partial",
      "settings.database.systemTables.status.outdated",
      "settings.database.systemTables.status.ready",
    ]
  );
});

test("system table operations use stable success message keys", () => {
  assert.equal(
    systemTableOperationMessageKey("no_op"),
    "settings.database.systemTables.operation.no_op"
  );
  assert.equal(
    systemTableOperationMessageKey("recreated"),
    "settings.database.systemTables.operation.recreated"
  );
});

test("local mutation and remote lease both disable related controls", () => {
  assert.equal(systemTableControlsBusy(false, "idle"), false);
  assert.equal(systemTableControlsBusy(true, "idle"), true);
  assert.equal(systemTableControlsBusy(false, "running"), true);
  assert.equal(systemTableControlsBusy(false, "failed"), false);
});
