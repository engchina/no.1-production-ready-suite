import assert from "node:assert/strict";
import test from "node:test";

import {
  profileSummaryPageFromLegacyList,
  sortProfileSummariesForDisplay,
} from "../src/features/nl2sql/profileListState.ts";
import type { Nl2SqlProfile, ProfileSummary } from "../src/features/nl2sql/types.ts";

function profile(patch: Partial<Nl2SqlProfile>): Nl2SqlProfile {
  return {
    id: "profile",
    name: "PROFILE",
    category: "DEFAULT",
    description: "",
    allowed_tables: [],
    allowed_views: [],
    glossary: {},
    sql_rules: [],
    default_row_limit: 100,
    safety_policy: "select_only",
    few_shot_examples: [],
    select_ai_config: {
      profile_name: "PROFILE",
      region: "ap-osaka-1",
      model: "cohere.command-r-plus",
      embedding_model: "cohere.embed-v4.0",
      max_tokens: 32000,
      enforce_object_list: true,
      comments: true,
      annotations: false,
      constraints: false,
      role: "",
      additional_instructions: "",
    },
    archived: false,
    ...patch,
  };
}

function summary(patch: Partial<ProfileSummary>): ProfileSummary {
  return {
    id: "profile",
    name: "PROFILE",
    category: "DEFAULT",
    description: "",
    archived: false,
    allowed_table_count: 0,
    allowed_view_count: 0,
    glossary_count: 0,
    few_shot_count: 0,
    version: 1,
    etag: "",
    updated_at: "",
    ...patch,
  };
}

test("profile display sorting does not mutate query data", () => {
  const source = [
    summary({ id: "emp", name: "PROFILE_EMP" }),
    summary({ id: "dept", name: "PROFILE_DEPT" }),
  ];
  const originalOrder = source.map((item) => item.id);

  const sorted = sortProfileSummariesForDisplay(source, { key: "name", direction: "asc" });

  assert.deepEqual(source.map((item) => item.id), originalOrder);
  assert.deepEqual(sorted.map((item) => item.id), ["dept", "emp"]);
  assert.notEqual(sorted, source);
});

test("legacy profile summary fallback filters active profiles by name and category", () => {
  const page = profileSummaryPageFromLegacyList(
    [
      profile({ id: "dept", name: "PROFILE_DEPT", category: "DEPT" }),
      profile({ id: "emp", name: "PROFILE_EMP", category: "EMPLOYEE" }),
      profile({ id: "archived", name: "PROFILE_OLD_DEPT", category: "DEPT", archived: true }),
    ],
    "dept"
  );

  assert.equal(page.total, 1);
  assert.deepEqual(
    page.items.map((item) => item.id),
    ["dept"]
  );
  assert.equal(page.next_cursor, null);
});
