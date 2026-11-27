import assert from "node:assert/strict";
import test from "node:test";
import { ontologyRelationshipKindLabel } from "../src/features/nl2sql/ontology/relationshipKind.ts";

// 辺の種類はノードの種類の辞書ではなく辺の種類の辞書で引く（#5908）。
test("関係の種類は辺の種類の表示名にし、既定の「関係」にしない", () => {
  for (const kind of ["contains", "foreign_key", "maps_to", "business_relationship", "lineage", "is_a", "has_value", "governs"]) {
    assert.notEqual(ontologyRelationshipKindLabel(kind), "関係", kind);
  }
  assert.equal(ontologyRelationshipKindLabel("foreign_key"), "外部キー");
  assert.equal(ontologyRelationshipKindLabel(" Contains "), "含む");
});

test("種類の無い・知らない辺は関係の名前、それも無ければ既定の文言", () => {
  assert.equal(ontologyRelationshipKindLabel("join", "合成の参照"), "合成の参照");
  assert.equal(ontologyRelationshipKindLabel(""), "関係");
});
