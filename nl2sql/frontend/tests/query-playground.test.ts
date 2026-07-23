import assert from "node:assert/strict";
import test from "node:test";

import { answerOntologyQuestion } from "../src/features/nl2sql/ontology/queryPlayground.ts";
import type { OntologyGraph } from "../src/features/nl2sql/ontology/types.ts";

const graph: OntologyGraph = {
  nodes: [
    {
      id: "customer",
      kind: "business_entity",
      business_name_ja: "顧客",
      description_ja: "商品を購入する主体。",
      aliases: ["得意先", "カスタマー"],
      technical_name: "APP.CUSTOMERS",
    },
    {
      id: "order",
      kind: "business_entity",
      business_name_ja: "注文",
      aliases: ["受注"],
      technical_name: "APP.ORDERS",
    },
    {
      id: "order_item",
      kind: "business_entity",
      business_name_ja: "注文明細",
      technical_name: "APP.ORDER_ITEMS",
    },
    {
      id: "sales_total",
      kind: "metric",
      business_name_ja: "売上合計",
      description_ja: "注文金額の合計。",
    },
  ],
  edges: [
    {
      id: "edge_customer_order",
      kind: "business_relationship",
      source_node_id: "customer",
      target_node_id: "order",
      relationship_name_ja: "注文する",
      cardinality: "one_to_many",
    },
    {
      id: "edge_order_item",
      kind: "business_relationship",
      source_node_id: "order",
      target_node_id: "order_item",
      relationship_name_ja: "明細を含む",
      cardinality: "one_to_many",
    },
    {
      id: "edge_order_metric",
      kind: "maps_to",
      source_node_id: "sales_total",
      target_node_id: "order",
      relationship_name_ja: "集計対象",
    },
  ],
};

test("定義質問はエンティティ 1 件をハイライトする", () => {
  const result = answerOntologyQuestion(graph, "顧客とは何ですか?");
  assert.equal(result.stage, "entity_definition");
  assert.deepEqual(result.highlightNodeIds, ["customer"]);
  assert.ok(result.explanationJa.includes("商品を購入する主体"));
});

test("日本語 alias(得意先)でも一致する", () => {
  const result = answerOntologyQuestion(graph, "得意先について教えて");
  assert.deepEqual(result.highlightNodeIds, ["customer"]);
});

test("一覧質問は list_all になる", () => {
  const result = answerOntologyQuestion(graph, "注文の一覧を見せて");
  assert.equal(result.stage, "list_all");
  assert.deepEqual(result.highlightNodeIds, ["order"]);
});

test("2 エンティティは直接辺をハイライトする", () => {
  const result = answerOntologyQuestion(graph, "顧客と注文の関係は?");
  assert.equal(result.stage, "relationship");
  assert.ok(result.highlightNodeIds.includes("customer"));
  assert.ok(result.highlightNodeIds.includes("order"));
  assert.deepEqual(result.highlightEdgeIds, ["edge_customer_order"]);
  assert.ok(result.explanationJa.includes("注文する"));
});

test("直接辺がない 2 エンティティは 1-hop 経由で結ぶ", () => {
  const result = answerOntologyQuestion(graph, "顧客と注文明細のつながりは?");
  assert.equal(result.stage, "relationship");
  assert.deepEqual([...result.highlightNodeIds].sort(), ["customer", "order", "order_item"]);
  assert.deepEqual(
    [...result.highlightEdgeIds].sort(),
    ["edge_customer_order", "edge_order_item"]
  );
});

test("最長一致で「注文明細」を「注文」より優先する", () => {
  const result = answerOntologyQuestion(graph, "注文明細とは?");
  assert.deepEqual(result.highlightNodeIds, ["order_item"]);
});

test("属性質問は指標ノードと接続辺をハイライトする", () => {
  const result = answerOntologyQuestion(graph, "注文の売上合計は?");
  assert.equal(result.stage, "property");
  assert.deepEqual(result.highlightNodeIds, ["order", "sales_total"]);
  assert.deepEqual(result.highlightEdgeIds, ["edge_order_metric"]);
});

test("一致しない質問は no_match と候補を返す", () => {
  const result = answerOntologyQuestion(graph, "天気はどうですか?");
  assert.equal(result.stage, "no_match");
  assert.deepEqual(result.highlightNodeIds, []);
  assert.ok(result.suggestionsJa.includes("顧客"));
});

test("空質問は no_match", () => {
  assert.equal(answerOntologyQuestion(graph, "  ").stage, "no_match");
});
