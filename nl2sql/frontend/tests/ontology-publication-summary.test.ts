import assert from "node:assert/strict";
import test from "node:test";

import {
  conceptDisplayNames,
  findingTarget,
  partitionFindings,
  summarizeOntologyGraph,
  visibleDifferences,
  type PublicationDefinition,
} from "../src/features/nl2sql/ontology/publicationSummary.ts";

// #1720: 表 3 つと view 2 つのサンプル（sql_assist_sample）を AI が構築した形。
const definitions: PublicationDefinition[] = [
  { kind: "object_type", api_name: "department", name_ja: "部門", properties: ["department_name"], mappings: [{ owner: "ADMIN", object_name: "DEPARTMENT", column_name: "" }] },
  { kind: "object_type", api_name: "employee", name_ja: "従業員", properties: ["employee_name"], mappings: [{ owner: "ADMIN", object_name: "EMPLOYEE", column_name: "" }] },
  { kind: "object_type", api_name: "v_emp_dept", name_ja: "従業員部門ビュー", properties: ["v_emp_dept_department_name"], mappings: [{ owner: "ADMIN", object_name: "V_EMP_DEPT", column_name: "" }] },
  { kind: "property", api_name: "department_name", name_ja: "部門名", object_type: "department" },
  { kind: "property", api_name: "employee_name", name_ja: "従業員名", object_type: "employee" },
  { kind: "property", api_name: "v_emp_dept_department_name", name_ja: "部門名", object_type: "v_emp_dept" },
  { kind: "link_type", api_name: "employee_department", name_ja: "所属", source: "employee", target: "department" },
  { kind: "value_type", api_name: "string_type", name_ja: "文字列型" },
  { kind: "metric", api_name: "headcount", name_ja: "人数" },
];

test("graph の要約はオブジェクト・関係・プロパティを数え、値型を数えない", () => {
  const summary = summarizeOntologyGraph(definitions);
  assert.equal(summary.objectCount, 3);
  assert.equal(summary.linkCount, 1);
  assert.equal(summary.propertyCount, 3);
  assert.equal(summary.otherCount, 1);
  const employee = summary.objects.find(object => object.apiName === "employee");
  assert.deepEqual(employee?.properties, ["従業員名"]);
  assert.deepEqual(employee?.links, ["所属: → 部門"]);
  assert.equal(employee?.physical, "ADMIN.EMPLOYEE");
  const department = summary.objects.find(object => object.apiName === "department");
  assert.deepEqual(department?.links, ["所属: ← 従業員"]);
});

test("同じ名前のプロパティには属するオブジェクトの名前を添える", () => {
  const names = conceptDisplayNames(definitions);
  assert.equal(names.get("department_name"), "部門名（部門）");
  assert.equal(names.get("v_emp_dept_department_name"), "部門名（従業員部門ビュー）");
  assert.equal(names.get("employee_name"), "従業員名");
});

test("差分の一覧は内部の概念（値型）を出さない", () => {
  const shown = visibleDifferences([
    { id: "a", before: null, after: definitions[3] },
    { id: "b", before: null, after: definitions[7] },
    { id: "c", before: definitions[7], after: null },
  ]);
  assert.deepEqual(shown.map(difference => difference.id), ["a"]);
});

test("診断は公開を止めるもの・警告・補うと精度が上がるものに分け、対象の名前を出す", () => {
  const findings = [
    { severity: "error", code: "MAPPING_OUTSIDE_PROFILE", definition_id: "v_emp_dept", field: "mappings", message_ja: "範囲外" },
    { severity: "warning", code: "DEFINITION_CONFLICT", message_ja: "競合" },
    { severity: "info", code: "OBJECT_IDENTITY_REQUIRED", definition_id: "v_emp_dept", field: "primary_key", message_ja: "識別子" },
  ];
  const partition = partitionFindings(findings);
  assert.equal(partition.blocking.length, 1);
  assert.equal(partition.warnings.length, 1);
  assert.equal(partition.improvements.length, 1);
  const names = conceptDisplayNames(definitions);
  assert.equal(findingTarget(findings[2], names), "従業員部門ビュー（v_emp_dept） / primary_key / OBJECT_IDENTITY_REQUIRED");
  assert.equal(findingTarget(findings[1], names), "DEFINITION_CONFLICT");
});
