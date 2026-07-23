import type { OntologyEdge, OntologyGraph, OntologyNode } from "./types";

/**
 * 決定論 NL Query Playground(Ontology-Playground の queryEngine.ts の日本語移植)。
 * LLM を呼ばず、質問文をオントロジーのノード/エッジへ段階マッチングして
 * ハイライト対象と説明文を返す。SQL は生成しない(可視化・デバッグ用途)。
 */

export type PlaygroundStage =
  | "entity_definition"
  | "list_all"
  | "relationship"
  | "property"
  | "no_match";

export interface PlaygroundResult {
  stage: PlaygroundStage;
  highlightNodeIds: string[];
  highlightEdgeIds: string[];
  explanationJa: string;
  matchedEntityNames: string[];
  suggestionsJa: string[];
}

interface NameVariant {
  node: OntologyNode;
  name: string;
}

const ENTITY_KINDS = new Set(["business_entity", "business_event", "table", "view"]);
const ATTRIBUTE_KINDS = new Set(["property", "metric", "business_term", "column", "enum_value"]);
const LIST_PATTERNS = [/一覧/, /すべて/, /全て/, /リスト/, /list/i, /show me all/i];
const DEFINITION_PATTERNS = [/とは/, /について/, /何ですか/, /どういう/, /what is/i];

export function normalizeQuestion(question: string): string {
  return question.normalize("NFKC").toLowerCase().trim();
}

function nameVariants(nodes: OntologyNode[], kinds: Set<string>): NameVariant[] {
  const variants: NameVariant[] = [];
  for (const node of nodes) {
    if (!kinds.has(node.kind)) continue;
    const names = [node.business_name_ja, ...(node.aliases ?? []), node.technical_name ?? ""];
    for (const name of names) {
      const normalized = normalizeQuestion(name);
      if (normalized.length >= 2) variants.push({ node, name: normalized });
    }
  }
  // 最長一致優先(「注文明細」を「注文」より先に消費する)
  return variants.sort((a, b) => b.name.length - a.name.length);
}

/** 質問文からノード名を最長一致で消費しつつ、一致ノードを重複なく集める。 */
function consumeMatches(
  question: string,
  variants: NameVariant[]
): { matched: OntologyNode[]; remaining: string } {
  const matched: OntologyNode[] = [];
  const seen = new Set<string>();
  let remaining = question;
  for (const variant of variants) {
    if (!remaining.includes(variant.name)) continue;
    remaining = remaining.split(variant.name).join(" ");
    if (!seen.has(variant.node.id)) {
      seen.add(variant.node.id);
      matched.push(variant.node);
    }
  }
  return { matched, remaining };
}

function edgesTouching(edges: OntologyEdge[], nodeIds: Set<string>): OntologyEdge[] {
  return edges.filter(
    (edge) => nodeIds.has(edge.source_node_id) && nodeIds.has(edge.target_node_id)
  );
}

function relationshipLabel(edge: OntologyEdge): string {
  const cardinality = edge.cardinality && edge.cardinality !== "unknown" ? `(${edge.cardinality})` : "";
  return `${edge.relationship_name_ja}${cardinality}`;
}

export function answerOntologyQuestion(
  graph: OntologyGraph,
  question: string
): PlaygroundResult {
  const normalized = normalizeQuestion(question);
  const entityVariants = nameVariants(graph.nodes, ENTITY_KINDS);
  const suggestions = [
    ...new Set(
      graph.nodes
        .filter((node) => node.kind === "business_entity" || node.kind === "business_event")
        .map((node) => node.business_name_ja)
    ),
  ].slice(0, 5);

  if (!normalized) {
    return {
      stage: "no_match",
      highlightNodeIds: [],
      highlightEdgeIds: [],
      explanationJa: "質問を入力してください。",
      matchedEntityNames: [],
      suggestionsJa: suggestions,
    };
  }

  const { matched, remaining } = consumeMatches(normalized, entityVariants);

  if (matched.length === 0) {
    return {
      stage: "no_match",
      highlightNodeIds: [],
      highlightEdgeIds: [],
      explanationJa: "質問に一致するエンティティが見つかりませんでした。",
      matchedEntityNames: [],
      suggestionsJa: suggestions,
    };
  }

  const matchedNames = matched.map((node) => node.business_name_ja);

  if (matched.length >= 2) {
    // 関係辿り: 直接辺 → なければ 1-hop(中継ノード経由)
    const matchedIds = new Set(matched.map((node) => node.id));
    const direct = edgesTouching(graph.edges, matchedIds);
    if (direct.length > 0) {
      return {
        stage: "relationship",
        highlightNodeIds: [...matchedIds],
        highlightEdgeIds: direct.map((edge) => edge.id),
        explanationJa:
          `「${matchedNames.join("」と「")}」は ` +
          `${direct.map(relationshipLabel).join("、")} で結ばれています。`,
        matchedEntityNames: matchedNames,
        suggestionsJa: [],
      };
    }
    const [first, second] = matched;
    for (const node of graph.nodes) {
      if (node.id === first.id || node.id === second.id) continue;
      const viaFirst = graph.edges.filter(
        (edge) =>
          (edge.source_node_id === first.id && edge.target_node_id === node.id) ||
          (edge.target_node_id === first.id && edge.source_node_id === node.id)
      );
      const viaSecond = graph.edges.filter(
        (edge) =>
          (edge.source_node_id === second.id && edge.target_node_id === node.id) ||
          (edge.target_node_id === second.id && edge.source_node_id === node.id)
      );
      if (viaFirst.length > 0 && viaSecond.length > 0) {
        return {
          stage: "relationship",
          highlightNodeIds: [first.id, node.id, second.id],
          highlightEdgeIds: [viaFirst[0].id, viaSecond[0].id],
          explanationJa:
            `「${first.business_name_ja}」と「${second.business_name_ja}」は` +
            `「${node.business_name_ja}」を経由してつながります。`,
          matchedEntityNames: matchedNames,
          suggestionsJa: [],
        };
      }
    }
    return {
      stage: "relationship",
      highlightNodeIds: [...matchedIds],
      highlightEdgeIds: [],
      explanationJa:
        `「${matchedNames.join("」と「")}」の間に承認済みの関係が見つかりませんでした。`,
      matchedEntityNames: matchedNames,
      suggestionsJa: [],
    };
  }

  const entity = matched[0];
  const connectedEdges = graph.edges.filter(
    (edge) => edge.source_node_id === entity.id || edge.target_node_id === entity.id
  );

  // 属性質問: 残り文にプロパティ/指標/用語名が含まれるか
  const attributeVariants = nameVariants(graph.nodes, ATTRIBUTE_KINDS);
  const attribute = consumeMatches(remaining, attributeVariants).matched[0];
  if (attribute) {
    const connecting = connectedEdges.filter(
      (edge) => edge.source_node_id === attribute.id || edge.target_node_id === attribute.id
    );
    return {
      stage: "property",
      highlightNodeIds: [entity.id, attribute.id],
      highlightEdgeIds: connecting.map((edge) => edge.id),
      explanationJa:
        `「${attribute.business_name_ja}」は「${entity.business_name_ja}」に関連する` +
        `${attribute.kind === "metric" ? "指標" : "属性・用語"}です。` +
        (attribute.description_ja ? ` ${attribute.description_ja}` : ""),
      matchedEntityNames: matchedNames,
      suggestionsJa: [],
    };
  }

  if (LIST_PATTERNS.some((pattern) => pattern.test(normalized))) {
    return {
      stage: "list_all",
      highlightNodeIds: [entity.id],
      highlightEdgeIds: [],
      explanationJa: `「${entity.business_name_ja}」の一覧照会に対応するエンティティです。`,
      matchedEntityNames: matchedNames,
      suggestionsJa: [],
    };
  }

  const isDefinition = DEFINITION_PATTERNS.some((pattern) => pattern.test(normalized));
  return {
    stage: "entity_definition",
    highlightNodeIds: [entity.id],
    highlightEdgeIds: isDefinition ? [] : connectedEdges.map((edge) => edge.id),
    explanationJa:
      `「${entity.business_name_ja}」${entity.description_ja ? `: ${entity.description_ja}` : "に対応するエンティティです。"}` +
      (connectedEdges.length > 0 ? ` 関係が ${connectedEdges.length} 件あります。` : ""),
    matchedEntityNames: matchedNames,
    suggestionsJa: [],
  };
}
