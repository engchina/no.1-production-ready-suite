import assert from "node:assert/strict";
import test from "node:test";

import { api } from "../src/lib/api.ts";

function settingsResponse(data: unknown, status = 200): Response {
  return new Response(
    JSON.stringify({
      data,
      error_messages: status < 400 ? [] : ["RDF network 操作に失敗しました。"],
      warning_messages: [],
    }),
    { status, headers: { "Content-Type": "application/json" } }
  );
}

test("RDF network 設定保存は PATCH で owner/name/tablespace/options を送信する", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ path: string; method: string; body: unknown }> = [];
  globalThis.fetch = async (input, init) => {
    calls.push({
      path: String(input),
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    return settingsResponse({
      network_owner: "NL2SQL_APP",
      network_name: "NET1",
      tablespace: "RDFTBS",
      options: "MODEL_PARTITIONS=16",
      configured: true,
      mode: "oracle_rdf",
      status: "missing",
      current_oracle_user: "NL2SQL_APP",
      can_apply: true,
      manual_action_required: false,
      message_ja: "RDF network を確認できません。",
      warnings_ja: [],
      metadata: {},
      config_source: "runtime",
    });
  };

  try {
    const result = await api.updateRdfNetworkSettings({
      network_owner: "NL2SQL_APP",
      network_name: "NET1",
      tablespace: "RDFTBS",
      options: "MODEL_PARTITIONS=16",
    });
    assert.equal(result.mode, "oracle_rdf");
    assert.deepEqual(calls, [
      {
        path: "/api/settings/database/rdf-network",
        method: "PATCH",
        body: {
          network_owner: "NL2SQL_APP",
          network_name: "NET1",
          tablespace: "RDFTBS",
          options: "MODEL_PARTITIONS=16",
        },
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("RDF network plan 取得と apply は固定 endpoint と確認 payload を使う", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ path: string; method: string; body: unknown }> = [];
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    calls.push({
      path,
      method: init?.method ?? "GET",
      body: init?.body ? JSON.parse(String(init.body)) : null,
    });
    if (path.endsWith("/plan")) {
      return settingsResponse({
        version: "V001",
        configured: true,
        can_apply: true,
        manual_action_required: false,
        confirmation_phrase: "ADMIN_EXECUTE",
        checksum: "rdf-checksum-001",
        steps: [
          {
            step_no: 1,
            title_ja: "schema-private RDF network を作成",
            sql: "BEGIN SEM_APIS.CREATE_RDF_NETWORK(tablespace_name => :tablespace_name); END;",
            checksum: "rdf-checksum-001",
            status: "pending",
          },
        ],
        warnings_ja: [],
      });
    }
    return settingsResponse({
      status: "applied",
      network: {
        network_owner: "NL2SQL_APP",
        network_name: "NET1",
        tablespace: "RDFTBS",
        options: "",
        configured: true,
        mode: "oracle_rdf",
        status: "ready",
        current_oracle_user: "NL2SQL_APP",
        can_apply: false,
        manual_action_required: false,
        message_ja: "RDF network は利用可能です。",
        warnings_ja: [],
        metadata: { parameter_rows: 1 },
        config_source: "runtime",
      },
    });
  };

  try {
    const plan = await api.getRdfNetworkPlan();
    const applied = await api.applyRdfNetworkPlan({
      checksum: plan.checksum,
      confirmation: plan.confirmation_phrase,
    });
    assert.equal(applied.network.status, "ready");
    assert.deepEqual(calls, [
      {
        path: "/api/settings/database/rdf-network/plan",
        method: "GET",
        body: null,
      },
      {
        path: "/api/settings/database/rdf-network/apply",
        method: "POST",
        body: { checksum: "rdf-checksum-001", confirmation: "ADMIN_EXECUTE" },
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
