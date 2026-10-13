// 入力が SQL の文（SELECT / WITH の 1 文）か（#1715）。backend の tests/test_nl2sql_requirement_failures.py と
// 同じ例で、画面と backend の判定がずれないことを確かめる。
import assert from "node:assert/strict";
import test from "node:test";

import { isSqlStatementInput } from "../src/features/nl2sql/sqlInput.ts";

const SQL_INPUTS = [
  "select * from employee",
  "SELECT * FROM EMPLOYEE;",
  "  select e.* from employee e where e.name = 'それ; は' ;  ",
  "-- 社員の一覧\nselect * from employee",
  "/* 社員 */ select count(*) from employee -- 件数\n",
  'select "社員名" from "社員"',
  "with t as (select * from employee) select * from t",
  "WITH dept_avg (dept, avg_sal) AS (SELECT 1, 2 FROM dual) SELECT * FROM dept_avg;",
  "select\n  name\nfrom\n  employee\n;;",
];

const NOT_SQL_INPUTS = [
  "",
  "社員の一覧を出して",
  "select * from employee の件数を出して",
  "selectの使い方",
  "select * from employee; delete from employee",
  "select * from employee where name = 'abc",
  "/* 閉じていない select * from employee",
  "select the top customers",
  "delete from employee",
  "update employee set salary = 0",
  "with 社員 as (select 1 from dual) select * from 社員",
  "with the employees from tokyo",
];

test("SELECT / WITH の 1 文は SQL の文として扱う", () => {
  for (const input of SQL_INPUTS) {
    assert.equal(isSqlStatementInput(input), true, input);
  }
});

test("自然言語の質問・2 つの文・閉じていない文字列は SQL の文として扱わない", () => {
  for (const input of NOT_SQL_INPUTS) {
    assert.equal(isSqlStatementInput(input), false, input);
  }
});
