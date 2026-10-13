import type { OntologyFinding } from "./types";

/** 公開前の確認で受け取る業務定義（backend の 13 分類の定義をそのまま JSON にしたもの）。 */
export type PublicationDefinition = Record<string, unknown>;

export interface PublicationDifference {
  id: string;
  before: PublicationDefinition | null;
  after: PublicationDefinition | null;
}

export interface GraphObjectSummary {
  apiName: string;
  label: string;
  physical: string;
  properties: string[];
  links: string[];
}

export interface GraphSummary {
  objects: GraphObjectSummary[];
  objectCount: number;
  linkCount: number;
  propertyCount: number;
  /** 指標・業務ルール・共有プロパティなど、オブジェクト・関係・プロパティ以外（値型は数えない）。 */
  otherCount: number;
}

/** 利用者に見せない内部の概念（型名だけの値型など）。差分の一覧・件数に出さない（#1720）。 */
const INTERNAL_KINDS = new Set(["value_type"]);

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string" && item.trim() !== "") : [];
}

function mappings(definition: PublicationDefinition): { owner: string; object_name: string }[] {
  const value = definition.mappings;
  return Array.isArray(value)
    ? value.filter((item): item is { owner: string; object_name: string } =>
      typeof item === "object" && item !== null && typeof (item as { object_name?: unknown }).object_name === "string")
    : [];
}

export function isInternalDefinition(definition: PublicationDefinition | null | undefined): boolean {
  return INTERNAL_KINDS.has(text(definition?.kind));
}

/**
 * 概念の表示名。同じ名前が複数あるプロパティには、属するオブジェクトの名前を添える
 * （例「部門名（従業員部門ビュー）」）。どのオブジェクトの項目か分かるようにする（#1720）。
 */
export function conceptDisplayNames(definitions: PublicationDefinition[]): Map<string, string> {
  const byApiName = new Map<string, PublicationDefinition>();
  for (const definition of definitions) {
    const apiName = text(definition.api_name);
    if (apiName) byApiName.set(apiName, definition);
  }
  const counts = new Map<string, number>();
  for (const definition of definitions) {
    const name = text(definition.name_ja) || text(definition.api_name);
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const names = new Map<string, string>();
  for (const definition of definitions) {
    const apiName = text(definition.api_name);
    const name = text(definition.name_ja) || apiName;
    const owner = byApiName.get(text(definition.object_type));
    const ownerName = owner ? text(owner.name_ja) || text(owner.api_name) : "";
    const label = (counts.get(name) ?? 0) > 1 && ownerName ? `${name}（${ownerName}）` : name;
    if (apiName) names.set(apiName, label);
    const id = text(definition.id);
    if (id) names.set(id, label);
  }
  return names;
}

/** graph として何ができたか（オブジェクトと、その関係・プロパティ）の要約。 */
export function summarizeOntologyGraph(definitions: PublicationDefinition[]): GraphSummary {
  const names = conceptDisplayNames(definitions);
  const label = (apiName: string) => names.get(apiName) ?? apiName;
  const objects = definitions.filter(definition => definition.kind === "object_type");
  const properties = definitions.filter(definition => definition.kind === "property");
  const links = definitions.filter(definition => definition.kind === "link_type");
  const summaries = objects.map(object => {
    const apiName = text(object.api_name);
    const owned = properties.filter(property => text(property.object_type) === apiName).map(property => text(property.api_name));
    const propertyNames = [...new Set([...list(object.properties), ...owned])];
    const physical = [...new Set(mappings(object).map(mapping => [mapping.owner, mapping.object_name].filter(Boolean).join(".")))].join(" / ");
    const objectLinks = links.flatMap(link => {
      const source = text(link.source);
      const target = text(link.target);
      if (source !== apiName && target !== apiName) return [];
      const other = source === apiName ? target : source;
      const name = text(link.name_ja);
      return [`${name ? `${name}: ` : ""}${source === apiName ? "→" : "←"} ${label(other)}`];
    });
    // オブジェクトの中の一覧なので、プロパティは素の名前で出す（どのオブジェクトかは見出しで分かる）。
    const plain = (name: string) => {
      const definition = definitions.find(item => text(item.api_name) === name);
      return (definition && text(definition.name_ja)) || name;
    };
    return { apiName, label: label(apiName), physical, properties: propertyNames.map(plain), links: objectLinks };
  });
  const counted = new Set(["object_type", "property", "link_type", ...INTERNAL_KINDS]);
  return {
    objects: summaries,
    objectCount: objects.length,
    linkCount: links.length,
    propertyCount: properties.length,
    otherCount: definitions.filter(definition => !counted.has(text(definition.kind))).length,
  };
}

/** 差分の一覧に出す概念。内部の概念（値型）は除く。 */
export function visibleDifferences(differences: PublicationDifference[]): PublicationDifference[] {
  return differences.filter(difference => !isInternalDefinition(difference.after ?? difference.before));
}

export interface FindingPartition {
  blocking: OntologyFinding[];
  warnings: OntologyFinding[];
  /** graph は作れるが、補えば精度が上がる項目（公開を止めない。控えめに 1 か所にまとめる）。 */
  improvements: OntologyFinding[];
}

export function partitionFindings(findings: OntologyFinding[]): FindingPartition {
  return {
    blocking: findings.filter(finding => finding.severity === "error"),
    warnings: findings.filter(finding => finding.severity === "warning"),
    improvements: findings.filter(finding => finding.severity === "info"),
  };
}

/** 診断の対象の表示（概念の名前・項目・診断コード）。名前が分からなければ ID を使う。 */
export function findingTarget(finding: OntologyFinding, names?: Map<string, string>): string {
  const id = finding.definition_id ?? "";
  const name = id ? names?.get(id) : undefined;
  const subject = name && name !== id ? `${name}（${id}）` : id;
  return [subject, finding.field, finding.code].filter(Boolean).join(" / ");
}
