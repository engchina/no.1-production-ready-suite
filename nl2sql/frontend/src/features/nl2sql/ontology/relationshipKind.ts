import { t } from "../../../lib/i18n";

const EDGE_KIND_LABEL_KEYS = {
  contains: "nl2sql.ontology.edgeKind.contains",
  foreign_key: "nl2sql.ontology.edgeKind.foreign_key",
  link_type: "nl2sql.ontology.edgeKind.link_type",
  business_relationship: "nl2sql.ontology.edgeKind.business_relationship",
  maps_to: "nl2sql.ontology.edgeKind.maps_to",
  lineage: "nl2sql.ontology.edgeKind.lineage",
  uses: "nl2sql.ontology.edgeKind.uses",
  joins: "nl2sql.ontology.edgeKind.joins",
  filters: "nl2sql.ontology.edgeKind.filters",
  groups_by: "nl2sql.ontology.edgeKind.groups_by",
  sorts_by: "nl2sql.ontology.edgeKind.sorts_by",
  is_a: "nl2sql.ontology.edgeKind.is_a",
  domain: "nl2sql.ontology.edgeKind.domain",
  range: "nl2sql.ontology.edgeKind.range",
  instance_of: "nl2sql.ontology.edgeKind.instance_of",
  has_value: "nl2sql.ontology.edgeKind.has_value",
  governs: "nl2sql.ontology.edgeKind.governs",
} as const;

/**
 * 関係（辺）の種類の表示名。辺の種類（`OntologyEdgeKind`）の辞書で引き、ノードの種類の辞書は使わない（#5908）。
 * 種類の無い辺は関係の名前（`fallback`）、それも無ければ既定の「関係」。
 */
export function ontologyRelationshipKindLabel(kind: string, fallback = ""): string {
  const key = EDGE_KIND_LABEL_KEYS[kind.trim().toLocaleLowerCase() as keyof typeof EDGE_KIND_LABEL_KEYS];
  if (key) return t(key);
  return fallback.trim() || t("ontologyPlayground.inspector.edgeKindFallback");
}
