import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

import type { OntologyGraph } from "../src/features/nl2sql/ontology/types.ts";

const require = createRequire(import.meta.url);
const {
  ontologyGraphForViewMode,
  ontologyGraphWithDetailVisibility,
} = require("../src/features/nl2sql/ontology/graphView.ts") as typeof import("../src/features/nl2sql/ontology/graphView.ts");

const graph: OntologyGraph = {
  nodes: [
    { id: "customer", kind: "business_entity", business_name_ja: "顧客" },
    { id: "orders", kind: "business_entity", business_name_ja: "注文" },
    {
      id: "orders-table",
      kind: "table",
      business_name_ja: "注文表",
      metadata: { owner: "APP", object_name: "ORDERS" },
    },
    {
      id: "orders-amount",
      kind: "column",
      business_name_ja: "注文金額",
      technical_name: "APP.ORDERS.AMOUNT",
      metadata: { owner: "APP", object_name: "ORDERS", column_name: "AMOUNT" },
    },
    { id: "outside", kind: "business_entity", business_name_ja: "社外秘" },
  ],
  edges: [
    {
      id: "customer-orders",
      source_node_id: "customer",
      target_node_id: "orders",
      relationship_name_ja: "注文する",
    },
    {
      id: "orders-maps",
      kind: "maps_to",
      source_node_id: "orders",
      target_node_id: "orders-table",
      relationship_name_ja: "物理マッピング",
    },
    {
      id: "orders-column",
      source_node_id: "orders-table",
      target_node_id: "orders-amount",
      relationship_name_ja: "列",
    },
    {
      id: "outside-edge",
      source_node_id: "outside",
      target_node_id: "customer",
      relationship_name_ja: "参照",
    },
  ],
};

test("grounding view keeps highlighted nodes, highlighted edges, and one-hop context", () => {
  const focused = ontologyGraphForViewMode(graph, "grounding", ["orders"], ["customer-orders"]);

  assert.deepEqual(
    focused.nodes.map((node) => node.id).sort(),
    ["customer", "orders", "orders-table"].sort()
  );
  assert.deepEqual(
    focused.edges.map((edge) => edge.id).sort(),
    ["customer-orders", "orders-maps"].sort()
  );
});

test("detail visibility hides columns unless explicitly expanded or forced", () => {
  const collapsed = ontologyGraphWithDetailVisibility(graph, false, new Set());
  assert.equal(collapsed.nodes.some((node) => node.id === "orders-amount"), false);

  const forced = ontologyGraphWithDetailVisibility(graph, false, new Set(["orders-amount"]));
  assert.equal(forced.nodes.some((node) => node.id === "orders-amount"), true);

  const expanded = ontologyGraphWithDetailVisibility(graph, true, new Set());
  assert.equal(expanded.nodes.some((node) => node.id === "orders-amount"), true);
});

test("physical ER view excludes business-only nodes", () => {
  const physical = ontologyGraphForViewMode(graph, "physical_er", [], []);
  assert.deepEqual(
    physical.nodes.map((node) => node.id).sort(),
    ["orders-amount", "orders-table"].sort()
  );
});
