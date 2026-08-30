import type {
  OntologyEdge,
  OntologyGraph,
  OntologyJsonValue,
  OntologyNode,
  SqlSemanticGraph,
  SqlSemanticItem,
  SqlSemanticJoin,
} from "./types";

export type SqlOntologyGroundingStatus = "matched" | "partial" | "unmatched" | "unavailable";

export interface SqlOntologyGroundingNodeMatch {
  sql: string;
  ontologyNodeIds: string[];
  ontologyLabels: string[];
}

export interface SqlOntologyGroundingEdgeMatch {
  sql: string;
  ontologyEdgeIds: string[];
  ontologyLabels: string[];
}

export interface SqlOntologyGroundingResult {
  status: SqlOntologyGroundingStatus;
  highlightNodeIds: string[];
  highlightEdgeIds: string[];
  matchedTables: SqlOntologyGroundingNodeMatch[];
  matchedColumns: SqlOntologyGroundingNodeMatch[];
  matchedJoins: SqlOntologyGroundingEdgeMatch[];
  unmatchedTables: string[];
  unmatchedColumns: string[];
  unmatchedJoins: string[];
}

interface ObjectIdentity {
  owner: string;
  objectName: string;
  nodeId?: string;
}

interface ColumnIdentity extends ObjectIdentity {
  columnName: string;
}

interface ObjectEntry extends ObjectIdentity {
  node: OntologyNode;
}

interface ColumnEntry extends ColumnIdentity {
  node: OntologyNode;
}

interface GroundingIndex {
  nodesById: Map<string, OntologyNode>;
  edgesById: Map<string, OntologyEdge>;
  objectEntries: ObjectEntry[];
  columnEntries: ColumnEntry[];
}

const TABLE_NODE_KINDS = new Set(["table", "view", "business_entity", "business_event"]);
const QUALIFIED_COLUMN_PATTERN = /(?:(?:"[^"]+"|[A-Za-z_][\w$#]*)\.){1,2}(?:"[^"]+"|[A-Za-z_][\w$#]*)/gu;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function jsonString(value: OntologyJsonValue | undefined): string {
  return typeof value === "string" ? value : "";
}

function normalizeIdentifier(value: string | undefined | null): string {
  return String(value ?? "")
    .normalize("NFKC")
    .trim()
    .replace(/^["'`\[]+|["'`\]]+$/g, "")
    .toLocaleUpperCase("en-US");
}

function normalizeAlias(value: string | undefined | null): string {
  return normalizeIdentifier(value).replace(/\s+/g, "");
}

function splitQualifiedName(value: string | undefined | null): {
  owner: string;
  objectName: string;
  columnName: string;
} {
  const cleaned = String(value ?? "")
    .replace(/"/g, "")
    .replace(/`/g, "")
    .replace(/\[/g, "")
    .replace(/\]/g, "")
    .trim();
  const firstToken = cleaned.split(/\s+/u)[0] ?? "";
  const parts = firstToken.split(".").map(normalizeIdentifier).filter(Boolean);
  if (parts.length >= 3) {
    return {
      owner: parts[parts.length - 3] ?? "",
      objectName: parts[parts.length - 2] ?? "",
      columnName: parts[parts.length - 1] ?? "",
    };
  }
  if (parts.length === 2) {
    return { owner: "", objectName: parts[0] ?? "", columnName: parts[1] ?? "" };
  }
  return { owner: "", objectName: "", columnName: parts[0] ?? "" };
}

function splitObjectName(value: string | undefined | null): ObjectIdentity | null {
  const cleaned = String(value ?? "")
    .replace(/"/g, "")
    .replace(/`/g, "")
    .replace(/\[/g, "")
    .replace(/\]/g, "")
    .trim();
  const firstToken = cleaned.split(/\s+/u)[0] ?? "";
  const parts = firstToken.split(".").map(normalizeIdentifier).filter(Boolean);
  if (parts.length >= 2) {
    return {
      owner: parts[parts.length - 2] ?? "",
      objectName: parts[parts.length - 1] ?? "",
    };
  }
  if (parts[0]) return { owner: "", objectName: parts[0] };
  return null;
}

function firstMappingObject(node: OntologyNode) {
  return node.physical_mappings?.[0]?.object_ref;
}

function objectIdentityFromNode(node: OntologyNode): ObjectIdentity | null {
  const mapping = firstMappingObject(node);
  const fallback = node.physical_mapping;
  const metadata = node.metadata ?? {};
  const technical = splitQualifiedName(node.technical_name);
  const owner = normalizeIdentifier(
    mapping?.owner ||
      jsonString(metadata.owner) ||
      fallback?.owner ||
      (TABLE_NODE_KINDS.has(node.kind) || node.kind === "column" ? technical.owner : "")
  );
  const objectName = normalizeIdentifier(
    mapping?.object_name ||
      jsonString(metadata.object_name) ||
      fallback?.object_name ||
      (TABLE_NODE_KINDS.has(node.kind) || node.kind === "column" ? technical.objectName : "")
  );
  if (!objectName) return null;
  return {
    owner,
    objectName,
    nodeId: mapping?.node_id || (node.kind === "table" || node.kind === "view" ? node.id : undefined),
  };
}

function columnIdentityFromNode(node: OntologyNode): ColumnIdentity | null {
  const object = objectIdentityFromNode(node);
  if (!object) return null;
  const columnRef = node.physical_mappings?.[0]?.column_refs?.[0];
  const metadata = node.metadata ?? {};
  const technical = splitQualifiedName(node.technical_name);
  const columnName = normalizeIdentifier(
    columnRef?.column_name ||
      jsonString(metadata.column_name) ||
      node.physical_mapping?.column_name ||
      technical.columnName
  );
  if (!columnName) return null;
  return { ...object, columnName };
}

function buildIndex(graph: OntologyGraph): GroundingIndex {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const edgesById = new Map(graph.edges.map((edge) => [edge.id, edge]));
  const objectEntries: ObjectEntry[] = [];
  const columnEntries: ColumnEntry[] = [];
  for (const node of graph.nodes) {
    const object = objectIdentityFromNode(node);
    if (object && TABLE_NODE_KINDS.has(node.kind)) objectEntries.push({ ...object, node });
    const column = columnIdentityFromNode(node);
    if (column) columnEntries.push({ ...column, node });
  }
  return { nodesById, edgesById, objectEntries, columnEntries };
}

function normalizeSqlItem(value: SqlSemanticItem | string): SqlSemanticItem {
  return typeof value === "string" ? { expression: value } : value;
}

function normalizeSqlJoin(value: SqlSemanticJoin | string): SqlSemanticJoin {
  return typeof value === "string" ? { expression: value, condition: value } : value;
}

function itemLabel(item: SqlSemanticItem | SqlSemanticJoin | string): string {
  if (typeof item === "string") return item;
  return (
    item.expression ||
    item.expression_sql ||
    ("condition_sql" in item ? item.condition_sql || item.condition : "") ||
    item.query_sql ||
    item.source_sql ||
    item.qualified_name ||
    item.output_name ||
    item.name ||
    "SQL element"
  );
}

function tableIdentityFromItem(item: SqlSemanticItem): ObjectIdentity | null {
  if (item.is_cte) return null;
  const owner = normalizeIdentifier(item.owner);
  const name = normalizeIdentifier(item.name || item.qualified_name || item.expression || item.source_sql);
  if (owner && item.name) return { owner, objectName: normalizeIdentifier(item.name) };
  const qualified = splitObjectName(item.qualified_name || item.source_sql || item.expression || item.name);
  if (qualified?.objectName) {
    return {
      owner: owner || qualified.owner,
      objectName: normalizeIdentifier(item.name) || qualified.objectName,
    };
  }
  if (name) return { owner, objectName: name };
  return null;
}

function objectMatches(left: ObjectIdentity, right: ObjectIdentity): boolean {
  if (!left.objectName || !right.objectName) return false;
  if (left.nodeId && right.nodeId && left.nodeId === right.nodeId) return true;
  if (left.objectName !== right.objectName) return false;
  return !left.owner || !right.owner || left.owner === right.owner;
}

function columnMatches(left: ColumnIdentity, right: ColumnIdentity): boolean {
  return left.columnName === right.columnName && objectMatches(left, right);
}

function matchingObjectEntries(index: GroundingIndex, identity: ObjectIdentity | null): ObjectEntry[] {
  if (!identity?.objectName) return [];
  return index.objectEntries.filter((entry) => objectMatches(entry, identity));
}

/**
 * 未修飾列(`SELECT "SALARY" FROM ...` のようにテーブル修飾が無い列)は、
 * その SQL scope の FROM 句に現れた表だけに束縛する。列名だけで ontology 全体を
 * 横断一致させると、同名列を持つ無関係な表(例: DEPARTMENT_ID を持つ DEPARTMENT /
 * PROJECT)まで接地扱いになるため。
 */
function matchingColumnEntries(
  index: GroundingIndex,
  identity: ColumnIdentity,
  scopeObjects: ObjectIdentity[]
): ColumnEntry[] {
  if (identity.objectName) {
    return index.columnEntries.filter((entry) => columnMatches(entry, identity));
  }
  const byName = index.columnEntries.filter((entry) => entry.columnName === identity.columnName);
  if (scopeObjects.length === 0) return byName;
  const scoped = byName.filter((entry) =>
    scopeObjects.some((object) => objectMatches(entry, object))
  );
  if (scoped.length > 0) return scoped;
  // FROM の表が ontology に 1 つも接地しないときだけ全体フォールバックする。
  // 接地しているのに列が無い場合は素直に「未接地」として報告する。
  return scopeObjects.some((object) => matchingObjectEntries(index, object).length > 0)
    ? []
    : byName;
}

function dedupe<T>(values: T[]): T[] {
  return [...new Set(values)];
}

function nodeLabels(index: GroundingIndex, nodeIds: string[]): string[] {
  return dedupe(nodeIds.map((nodeId) => index.nodesById.get(nodeId)?.business_name_ja || nodeId));
}

function edgeLabels(index: GroundingIndex, edgeIds: string[]): string[] {
  return dedupe(edgeIds.map((edgeId) => index.edgesById.get(edgeId)?.relationship_name_ja || edgeId));
}

function tableAliasMap(sqlGraph: SqlSemanticGraph): Map<string, ObjectIdentity> {
  const aliases = new Map<string, ObjectIdentity>();
  for (const rawTable of sqlGraph.tables) {
    const table = normalizeSqlItem(rawTable);
    const identity = tableIdentityFromItem(table);
    if (!identity) continue;
    const alias = normalizeAlias(table.alias);
    if (alias) aliases.set(alias, identity);
    if (table.name) aliases.set(normalizeAlias(table.name), identity);
    if (table.qualified_name) aliases.set(normalizeAlias(table.qualified_name), identity);
  }
  return aliases;
}

interface TableScope {
  byScope: Map<string, ObjectIdentity[]>;
  all: ObjectIdentity[];
}

/** SQL の FROM 句に現れる実体表を scope 単位で集める(CTE は tableIdentityFromItem が除外)。 */
function tableScopeIdentities(sqlGraph: SqlSemanticGraph): TableScope {
  const byScope = new Map<string, ObjectIdentity[]>();
  const all: ObjectIdentity[] = [];
  for (const rawTable of sqlGraph.tables) {
    const table = normalizeSqlItem(rawTable);
    const identity = tableIdentityFromItem(table);
    if (!identity?.objectName) continue;
    all.push(identity);
    const scopeId = String(table.scope_id ?? "").trim();
    if (!scopeId) continue;
    const bucket = byScope.get(scopeId);
    if (bucket) bucket.push(identity);
    else byScope.set(scopeId, [identity]);
  }
  return { byScope, all };
}

/** 列が属する scope の表集合。scope が判らない旧 artifact は SQL 全体の表へ縮退する。 */
function scopeObjectsForItem(scope: TableScope, item: SqlSemanticItem): ObjectIdentity[] {
  const scopeId = String(item.scope_id ?? "").trim();
  const scoped = scopeId ? scope.byScope.get(scopeId) : undefined;
  return scoped?.length ? scoped : scope.all;
}

function identityFromSourceName(source: string | undefined | null, aliases: Map<string, ObjectIdentity>): ObjectIdentity | null {
  const value = String(source ?? "").trim();
  if (!value) return null;
  const directAlias = aliases.get(normalizeAlias(value));
  if (directAlias) return directAlias;
  const parts = value.split(/\s+/u).filter(Boolean);
  if (parts.length > 1) {
    const alias = aliases.get(normalizeAlias(parts[parts.length - 1]));
    if (alias) return alias;
    const sourceIdentity = splitObjectName(parts[0]);
    if (sourceIdentity?.objectName) return sourceIdentity;
  }
  return splitObjectName(value);
}

function columnIdentityFromPath(path: string, aliases: Map<string, ObjectIdentity>): ColumnIdentity | null {
  const normalizedPath = String(path ?? "").trim();
  if (!normalizedPath) return null;
  const cleaned = normalizedPath.replace(/"/g, "").replace(/`/g, "");
  const parts = cleaned.split(".").map(normalizeIdentifier).filter(Boolean);
  if (parts.length >= 3) {
    return {
      owner: parts[parts.length - 3] ?? "",
      objectName: parts[parts.length - 2] ?? "",
      columnName: parts[parts.length - 1] ?? "",
    };
  }
  if (parts.length === 2) {
    const aliasIdentity = aliases.get(normalizeAlias(parts[0]));
    if (aliasIdentity) return { ...aliasIdentity, columnName: parts[1] ?? "" };
    return { owner: "", objectName: parts[0] ?? "", columnName: parts[1] ?? "" };
  }
  if (parts[0]) return { owner: "", objectName: "", columnName: parts[0] };
  return null;
}

function columnIdentitiesFromItem(item: SqlSemanticItem, aliases: Map<string, ObjectIdentity>): ColumnIdentity[] {
  const values = new Set<string>();
  if (item.table && (item.column || item.name)) {
    values.add(`${item.table}.${item.column || item.name}`);
  } else if (item.column || (item.name && !item.function_name)) {
    values.add(item.column || item.name || "");
  }
  for (const value of item.referenced_columns ?? []) values.add(value);
  for (const value of item.lineage ?? []) values.add(value);
  for (const value of [item.expression, item.expression_sql, item.source_sql, item.qualified_name]) {
    if (!value) continue;
    for (const match of value.matchAll(QUALIFIED_COLUMN_PATTERN)) {
      values.add(match[0]);
    }
  }
  return dedupe(
    [...values]
      .map((value) => columnIdentityFromPath(value, aliases))
      .filter((value): value is ColumnIdentity => Boolean(value?.columnName))
  );
}

function enrichColumnNodeIds(index: GroundingIndex, columnEntries: ColumnEntry[]): string[] {
  const nodeIds = new Set(columnEntries.map((entry) => entry.node.id));
  for (const column of columnEntries) {
    for (const object of matchingObjectEntries(index, column)) {
      nodeIds.add(object.node.id);
    }
  }
  return [...nodeIds];
}

function endpointIdentity(endpoint: { owner?: string; object_name?: string; column_name?: string } | undefined): ColumnIdentity | null {
  const objectName = normalizeIdentifier(endpoint?.object_name);
  const columnName = normalizeIdentifier(endpoint?.column_name);
  if (!objectName || !columnName) return null;
  return { owner: normalizeIdentifier(endpoint?.owner), objectName, columnName };
}

function columnIdentityFromObject(
  object: ObjectIdentity | null,
  columnName: string | undefined
): ColumnIdentity | null {
  const normalized = normalizeIdentifier(columnName);
  if (!object || !normalized) return null;
  return { ...object, columnName: normalized };
}

function edgeJoinConditionPairs(
  index: GroundingIndex,
  edge: OntologyEdge
): Array<{ left: ColumnIdentity; right: ColumnIdentity }> {
  const sourceNode = index.nodesById.get(edge.source_node_id);
  const targetNode = index.nodesById.get(edge.target_node_id);
  const sourceObject = sourceNode ? objectIdentityFromNode(sourceNode) : null;
  const targetObject = targetNode ? objectIdentityFromNode(targetNode) : null;
  const pairs: Array<{ left: ColumnIdentity; right: ColumnIdentity }> = [];
  for (const condition of edge.join_conditions ?? []) {
    const left =
      endpointIdentity(condition.left) ?? columnIdentityFromObject(sourceObject, condition.source_column);
    const right =
      endpointIdentity(condition.right) ?? columnIdentityFromObject(targetObject, condition.target_column);
    if (left && right) pairs.push({ left, right });
  }
  return pairs;
}

/**
 * ON 句の列対で edge を照合する。left_source/right_source の表ペアは star join で
 * ずれることがあるため、接地は ON 句の列を正として先に判定する。
 */
function edgeMatchesJoinColumns(
  index: GroundingIndex,
  edge: OntologyEdge,
  columns: ColumnIdentity[]
): boolean {
  if (columns.length === 0) return false;
  const pairs = edgeJoinConditionPairs(index, edge);
  if (pairs.length === 0) return false;
  return pairs.every(
    (pair) =>
      columns.some((column) => columnMatches(column, pair.left)) &&
      columns.some((column) => columnMatches(column, pair.right))
  );
}

function edgeMatchesObjects(index: GroundingIndex, edge: OntologyEdge, left: ObjectIdentity, right: ObjectIdentity): boolean {
  const source = index.nodesById.get(edge.source_node_id);
  const target = index.nodesById.get(edge.target_node_id);
  const sourceObject = source ? objectIdentityFromNode(source) : null;
  const targetObject = target ? objectIdentityFromNode(target) : null;
  if (sourceObject && targetObject) {
    const forward = objectMatches(sourceObject, left) && objectMatches(targetObject, right);
    const reverse = objectMatches(sourceObject, right) && objectMatches(targetObject, left);
    if (forward || reverse) return true;
  }
  for (const condition of edge.join_conditions ?? []) {
    const conditionLeft = endpointIdentity(condition.left);
    const conditionRight = endpointIdentity(condition.right);
    if (!conditionLeft || !conditionRight) continue;
    const forward = objectMatches(conditionLeft, left) && objectMatches(conditionRight, right);
    const reverse = objectMatches(conditionLeft, right) && objectMatches(conditionRight, left);
    if (forward || reverse) return true;
  }
  return false;
}

function joinColumnIdentities(
  join: SqlSemanticJoin,
  aliases: Map<string, ObjectIdentity>
): ColumnIdentity[] {
  const values = new Set<string>();
  for (const value of join.referenced_columns ?? []) values.add(value);
  for (const value of [join.condition_sql, join.condition, join.expression, join.expression_sql]) {
    if (!value) continue;
    for (const match of value.matchAll(QUALIFIED_COLUMN_PATTERN)) values.add(match[0]);
  }
  return [...values]
    .map((value) => columnIdentityFromPath(value, aliases))
    .filter((value): value is ColumnIdentity => Boolean(value?.columnName && value.objectName));
}

/**
 * SELECT で新しく作られた出力別名(`COUNT(*) AS "CNT"` の CNT など)。
 * ORDER BY / GROUP BY / HAVING はこの別名を未修飾で参照できるが物理列ではないので、
 * 未接地として報告しない。式が参照列そのものの別名(`t.SALARY` → SALARY)は除く。
 */
function computedOutputNames(sqlGraph: SqlSemanticGraph): Set<string> {
  const names = new Set<string>();
  for (const rawProjection of sqlGraph.projections ?? []) {
    const projection = normalizeSqlItem(rawProjection);
    const outputName = normalizeIdentifier(projection.output_name);
    if (!outputName) continue;
    const referenced = (projection.referenced_columns ?? []).some((value) => {
      const parts = String(value).split(".");
      return normalizeIdentifier(parts[parts.length - 1]) === outputName;
    });
    if (!referenced) names.add(outputName);
  }
  return names;
}

function sqlItemsForColumnGrounding(sqlGraph: SqlSemanticGraph): Array<SqlSemanticItem | string> {
  // columns[] は backend が全 clause の列参照を (scope, clause, 式) で重複排除した正本。
  // projections/filters/aggregates/… は同じ列の再掲なので、併せて走査すると同一列を
  // 二重に数えてしまう(接地件数が実際の倍になる)。
  if (sqlGraph.columns.length > 0) return sqlGraph.columns;
  // columns[] を持たない旧 artifact 互換。
  return [
    ...(sqlGraph.projections ?? []),
    ...sqlGraph.filters,
    ...sqlGraph.aggregates,
    ...(sqlGraph.groups ?? sqlGraph.group_by ?? []),
    ...sqlGraph.having,
    ...(sqlGraph.orders ?? sqlGraph.order_by ?? []),
  ];
}

export function groundSqlSemanticGraphOnOntologyGraph(
  sqlGraph: SqlSemanticGraph | null | undefined,
  ontologyGraph: OntologyGraph | null | undefined
): SqlOntologyGroundingResult {
  const empty: SqlOntologyGroundingResult = {
    status: "unavailable",
    highlightNodeIds: [],
    highlightEdgeIds: [],
    matchedTables: [],
    matchedColumns: [],
    matchedJoins: [],
    unmatchedTables: [],
    unmatchedColumns: [],
    unmatchedJoins: [],
  };
  if (!sqlGraph || !ontologyGraph || ontologyGraph.nodes.length === 0) return empty;

  const index = buildIndex(ontologyGraph);
  const aliases = tableAliasMap(sqlGraph);
  const tableScope = tableScopeIdentities(sqlGraph);
  const outputNames = computedOutputNames(sqlGraph);
  const highlightedNodes = new Set<string>();
  const highlightedEdges = new Set<string>();
  const matchedTables: SqlOntologyGroundingNodeMatch[] = [];
  const matchedColumns: SqlOntologyGroundingNodeMatch[] = [];
  const matchedJoins: SqlOntologyGroundingEdgeMatch[] = [];
  const unmatchedTables: string[] = [];
  const unmatchedColumns: string[] = [];
  const unmatchedJoins: string[] = [];

  for (const rawTable of sqlGraph.tables) {
    const table = normalizeSqlItem(rawTable);
    const label = table.qualified_name || itemLabel(table);
    const entries = matchingObjectEntries(index, tableIdentityFromItem(table));
    const nodeIds = dedupe(entries.map((entry) => entry.node.id));
    if (nodeIds.length === 0) {
      if (!table.is_cte) unmatchedTables.push(label);
      continue;
    }
    nodeIds.forEach((nodeId) => highlightedNodes.add(nodeId));
    matchedTables.push({ sql: label, ontologyNodeIds: nodeIds, ontologyLabels: nodeLabels(index, nodeIds) });
  }

  for (const rawItem of sqlItemsForColumnGrounding(sqlGraph)) {
    const item = normalizeSqlItem(rawItem);
    const label = itemLabel(item);
    const scopeObjects = scopeObjectsForItem(tableScope, item);
    const columnEntries = dedupe(
      columnIdentitiesFromItem(item, aliases).flatMap((identity) =>
        matchingColumnEntries(index, identity, scopeObjects)
      )
    );
    if (columnEntries.length === 0) {
      const hasColumnHint =
        (item.referenced_columns?.length ?? 0) > 0 ||
        Boolean(item.table && (item.column || item.name)) ||
        Boolean(item.column) ||
        // columns[] 由来の未修飾列(clause 付き・table 空)も未接地として報告する。
        // ただし SELECT で作られた出力別名の参照は物理列ではないので除く。
        Boolean(item.clause && item.name && !outputNames.has(normalizeIdentifier(item.name)));
      if (hasColumnHint) unmatchedColumns.push(label);
      continue;
    }
    const nodeIds = enrichColumnNodeIds(index, columnEntries);
    nodeIds.forEach((nodeId) => highlightedNodes.add(nodeId));
    matchedColumns.push({ sql: label, ontologyNodeIds: nodeIds, ontologyLabels: nodeLabels(index, nodeIds) });
  }

  for (const rawJoin of sqlGraph.joins) {
    const join = normalizeSqlJoin(rawJoin);
    const label = itemLabel(join);
    const directEdge = join.ontology_edge_id ? index.edgesById.get(join.ontology_edge_id) : undefined;
    let edgeIds = directEdge ? [directEdge.id] : [];
    if (edgeIds.length === 0) {
      const joinColumns = joinColumnIdentities(join, aliases);
      edgeIds = ontologyGraph.edges
        .filter((edge) => edgeMatchesJoinColumns(index, edge, joinColumns))
        .map((edge) => edge.id);
    }
    if (edgeIds.length === 0) {
      const left = identityFromSourceName(join.left_source || join.source_table, aliases);
      const right = identityFromSourceName(join.right_source || join.target_table, aliases);
      if (left && right) {
        edgeIds = ontologyGraph.edges
          .filter((edge) => edgeMatchesObjects(index, edge, left, right))
          .map((edge) => edge.id);
      }
    }
    edgeIds = dedupe(edgeIds);
    if (edgeIds.length === 0) {
      unmatchedJoins.push(label);
      continue;
    }
    edgeIds.forEach((edgeId) => {
      highlightedEdges.add(edgeId);
      const edge = index.edgesById.get(edgeId);
      if (edge) {
        highlightedNodes.add(edge.source_node_id);
        highlightedNodes.add(edge.target_node_id);
      }
    });
    matchedJoins.push({ sql: label, ontologyEdgeIds: edgeIds, ontologyLabels: edgeLabels(index, edgeIds) });
  }

  const unmatchedCount = unmatchedTables.length + unmatchedColumns.length + unmatchedJoins.length;
  const matchedCount = matchedTables.length + matchedColumns.length + matchedJoins.length;
  const status: SqlOntologyGroundingStatus =
    matchedCount === 0 ? "unmatched" : unmatchedCount > 0 ? "partial" : "matched";

  return {
    status,
    highlightNodeIds: [...highlightedNodes],
    highlightEdgeIds: [...highlightedEdges],
    matchedTables,
    matchedColumns,
    matchedJoins,
    unmatchedTables: dedupe(unmatchedTables),
    unmatchedColumns: dedupe(unmatchedColumns),
    unmatchedJoins: dedupe(unmatchedJoins),
  };
}

export function isSqlSemanticGraph(value: unknown): value is SqlSemanticGraph {
  if (!isRecord(value)) return false;
  return (
    Array.isArray(value.ctes) &&
    Array.isArray(value.tables) &&
    Array.isArray(value.columns) &&
    Array.isArray(value.joins) &&
    Array.isArray(value.filters) &&
    Array.isArray(value.aggregates) &&
    Array.isArray(value.having) &&
    Array.isArray(value.windows)
  );
}
