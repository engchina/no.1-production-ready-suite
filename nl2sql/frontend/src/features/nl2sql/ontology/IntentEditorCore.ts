import type {
  GraphPatch,
  GraphPatchOperation,
  IntentConcept,
  IntentFilter,
  IntentRelationshipPath,
  IntentSort,
  IntentTimeRange,
  OntologyGraph,
  OntologyJsonValue,
  OntologyNode,
  QuestionIntentGraph,
} from "./types";

export type IntentEditorOptionKind = "entity" | "metric" | "property" | "sort";

export interface IntentEditorNodeOption {
  id: string;
  label: string;
  kind: OntologyNode["kind"];
  physicalObjectIds: string[];
}

export interface IntentEditorConceptDraft {
  key: string;
  id: string;
  ontologyNodeId: string;
  nameJa: string;
  role: string;
  aggregation: string;
  granularity: string;
  physicalObjectIds: string[];
  formulaDescriptionJa: string;
}

export interface IntentEditorFilterDraft {
  key: string;
  id: string;
  propertyNodeId: string;
  labelJa: string;
  operator: string;
  valueText: string;
  valueType: string;
  required: boolean;
}

export interface IntentEditorTimeRangeDraft {
  propertyNodeId: string;
  labelJa: string;
  start: string;
  end: string;
  startInclusive: boolean;
  endInclusive: boolean;
  relativeExpression: string;
  timezone: string;
}

export interface IntentEditorSortDraft {
  key: string;
  targetId: string;
  direction: "asc" | "desc";
}

export interface IntentEditorDraft {
  questionEffective: string;
  entities: IntentEditorConceptDraft[];
  metrics: IntentEditorConceptDraft[];
  dimensions: IntentEditorConceptDraft[];
  filters: IntentEditorFilterDraft[];
  timeRange: IntentEditorTimeRangeDraft;
  granularity: string;
  sorts: IntentEditorSortDraft[];
  limit: string;
  selectedPathId: string;
}

export type IntentEditorValidationErrorCode =
  | "question_required"
  | "entity_required"
  | "filter_property_required"
  | "filter_value_required"
  | "time_property_required"
  | "limit_invalid"
  | "sort_target_required";

export interface IntentEditorPatchResult {
  patch: GraphPatch;
  errors: IntentEditorValidationErrorCode[];
}

function conceptValue(value: IntentConcept | string): IntentConcept {
  return typeof value === "string" ? { name_ja: value } : value;
}

function conceptName(value: IntentConcept): string {
  return value.name_ja || value.name || value.ontology_node_id || value.node_id || "";
}

function stablePart(value: string): string {
  const normalized = value
    .normalize("NFKC")
    .toLocaleLowerCase("ja")
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
  return normalized || "item";
}

function conceptDraft(
  value: IntentConcept | string,
  prefix: "entity" | "metric" | "dimension",
  index: number
): IntentEditorConceptDraft {
  const concept = conceptValue(value);
  const nameJa = conceptName(concept);
  const id = concept.id || `${prefix}-${stablePart(concept.ontology_node_id || nameJa)}-${index + 1}`;
  return {
    key: `${prefix}:${id}:${index}`,
    id,
    ontologyNodeId: concept.ontology_node_id || concept.node_id || "",
    nameJa,
    role: concept.role || (prefix === "entity" ? "subject" : ""),
    aggregation: concept.aggregation || "",
    granularity: concept.granularity || "",
    physicalObjectIds: concept.physical_object_ids?.slice() ?? [],
    formulaDescriptionJa: concept.formula_description_ja || "",
  };
}

function valueText(value: OntologyJsonValue): string {
  if (value === null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function filterDraft(value: IntentFilter, index: number): IntentEditorFilterDraft {
  const id = value.id || `filter-${index + 1}`;
  return {
    key: `filter:${id}:${index}`,
    id,
    propertyNodeId: value.property_node_id || value.node_id || "",
    labelJa: value.label_ja || value.field || "",
    operator: value.operator || "=",
    valueText: valueText(value.value),
    valueType: value.value_type || typeof value.value || "string",
    required: value.required ?? false,
  };
}

function timeRangeDraft(value: IntentTimeRange | null | undefined): IntentEditorTimeRangeDraft {
  return {
    propertyNodeId: value?.property_node_id || value?.field || "",
    labelJa: value?.label_ja || "",
    start: value?.start || "",
    end: value?.end || "",
    startInclusive: value?.start_inclusive ?? true,
    endInclusive: value?.end_inclusive ?? true,
    relativeExpression: value?.relative_expression || value?.relative || "",
    timezone: value?.timezone || "Asia/Tokyo",
  };
}

function sortDraft(value: IntentSort, index: number): IntentEditorSortDraft {
  const targetId = value.target_id || value.field || "";
  return {
    key: `sort:${targetId || index}:${index}`,
    targetId,
    direction: value.direction,
  };
}

export function createIntentEditorDraft(intent: QuestionIntentGraph): IntentEditorDraft {
  return {
    questionEffective:
      intent.question_effective || intent.rewritten_question || intent.question || intent.question_original || "",
    entities: intent.entities.map((value, index) => conceptDraft(value, "entity", index)),
    metrics: intent.metrics.map((value, index) => conceptDraft(value, "metric", index)),
    dimensions: intent.dimensions.map((value, index) => conceptDraft(value, "dimension", index)),
    filters: intent.filters.map(filterDraft),
    timeRange: timeRangeDraft(intent.time_range),
    granularity: intent.granularity || intent.grain || "",
    sorts: (intent.sorts ?? intent.sort ?? []).map(sortDraft),
    limit: intent.limit == null ? "" : String(intent.limit),
    selectedPathId: intent.selected_path_id || "",
  };
}

function physicalObjectIds(node: OntologyNode): string[] {
  if (node.kind === "table" || node.kind === "view") return [node.id];
  return Array.from(
    new Set(
      (node.physical_mappings ?? [])
        .map((mapping) => mapping.object_ref.node_id)
        .filter((nodeId): nodeId is string => Boolean(nodeId))
    )
  );
}

function allowedKinds(kind: IntentEditorOptionKind): Set<OntologyNode["kind"]> {
  if (kind === "entity") {
    return new Set(["business_entity", "business_event", "table", "view"]);
  }
  if (kind === "metric") return new Set(["metric"]);
  if (kind === "property") return new Set(["property", "column"]);
  return new Set(["business_entity", "business_event", "table", "view", "metric", "property", "column"]);
}

export function intentEditorNodeOptions(
  graph: OntologyGraph,
  kind: IntentEditorOptionKind
): IntentEditorNodeOption[] {
  const kinds = allowedKinds(kind);
  return graph.nodes
    .filter(
      (node) =>
        kinds.has(node.kind)
        && node.review_status === "approved"
    )
    .map((node) => ({
      id: node.id,
      label: node.business_name_ja,
      kind: node.kind,
      physicalObjectIds: physicalObjectIds(node),
    }))
    .filter((option) => kind !== "entity" || option.physicalObjectIds.length > 0)
    .sort((left, right) => left.label.localeCompare(right.label, "ja"));
}

export function intentEditorConceptFromOption(
  option: IntentEditorNodeOption,
  prefix: "entity" | "metric" | "dimension",
  sequence: number
): IntentEditorConceptDraft {
  const id = `${prefix}-${stablePart(option.id)}-${sequence}`;
  return {
    key: `${prefix}:${id}`,
    id,
    ontologyNodeId: option.id,
    nameJa: option.label,
    role: prefix === "entity" ? (option.kind === "business_event" ? "event" : "subject") : "",
    aggregation: prefix === "metric" ? "SUM" : "",
    granularity: "",
    physicalObjectIds: prefix === "entity" ? option.physicalObjectIds : [],
    formulaDescriptionJa: "",
  };
}

export function intentEditorFilterFromOption(
  option: IntentEditorNodeOption,
  sequence: number
): IntentEditorFilterDraft {
  const id = `filter-${stablePart(option.id)}-${sequence}`;
  return {
    key: `filter:${id}`,
    id,
    propertyNodeId: option.id,
    labelJa: option.label,
    operator: "=",
    valueText: "",
    valueType: "string",
    required: false,
  };
}

export function intentEditorSort(
  targetId: string,
  sequence: number
): IntentEditorSortDraft {
  return {
    key: `sort:${stablePart(targetId)}:${sequence}`,
    targetId,
    direction: "asc",
  };
}

function conceptPayload(value: IntentEditorConceptDraft, type: "entity" | "metric" | "dimension") {
  const result: Record<string, OntologyJsonValue> = {
    id: value.id,
    ontology_node_id: value.ontologyNodeId,
    name_ja: value.nameJa,
  };
  if (type === "entity") {
    result.role = value.role || "subject";
    result.physical_object_ids = value.physicalObjectIds;
  } else if (type === "metric") {
    result.aggregation = value.aggregation;
    result.formula_description_ja = value.formulaDescriptionJa;
  } else {
    result.granularity = value.granularity;
  }
  return result;
}

function parseFilterValue(value: IntentEditorFilterDraft): OntologyJsonValue {
  if (value.operator === "is_null" || value.operator === "is_not_null") return null;
  if (value.valueType === "number") {
    const parsed = Number(value.valueText);
    return Number.isFinite(parsed) ? parsed : value.valueText;
  }
  if (value.valueType === "boolean") return value.valueText.toLocaleLowerCase() === "true";
  return value.valueText;
}

function filterPayload(value: IntentEditorFilterDraft): Record<string, OntologyJsonValue> {
  return {
    id: value.id,
    property_node_id: value.propertyNodeId,
    label_ja: value.labelJa,
    operator: value.operator,
    value: parseFilterValue(value),
    value_type: value.valueType || "string",
    required: value.required,
  };
}

function timeRangePayload(value: IntentEditorTimeRangeDraft): Record<string, OntologyJsonValue> | null {
  const hasValue = Boolean(
    value.propertyNodeId || value.start || value.end || value.relativeExpression
  );
  if (!hasValue) return null;
  return {
    property_node_id: value.propertyNodeId,
    label_ja: value.labelJa || "期間",
    start: value.start || null,
    end: value.end || null,
    start_inclusive: value.startInclusive,
    end_inclusive: value.endInclusive,
    relative_expression: value.relativeExpression,
    timezone: value.timezone || "Asia/Tokyo",
  };
}

function sortPayload(value: IntentEditorSortDraft): Record<string, OntologyJsonValue> {
  return { target_id: value.targetId, direction: value.direction };
}

function originalConceptPayload(value: IntentConcept | string, type: "entity" | "metric" | "dimension", index: number) {
  return conceptPayload(
    conceptDraft(value, type === "dimension" ? "dimension" : type, index),
    type
  );
}

function originalFilterPayload(value: IntentFilter, index: number) {
  return filterPayload(filterDraft(value, index));
}

function originalSortPayload(value: IntentSort, index: number) {
  return sortPayload(sortDraft(value, index));
}

function originalTimeRangePayload(value: IntentTimeRange | null | undefined) {
  return timeRangePayload(timeRangeDraft(value));
}

function valuesEqual(left: OntologyJsonValue, right: OntologyJsonValue): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

function pushReplace(
  operations: GraphPatchOperation[],
  path: string,
  before: OntologyJsonValue,
  after: OntologyJsonValue,
  reasonJa: string
) {
  if (valuesEqual(before, after)) return;
  operations.push({ op: "replace", path, value: after, reason_ja: reasonJa });
}

export function validateIntentEditorDraft(draft: IntentEditorDraft): IntentEditorValidationErrorCode[] {
  const errors: IntentEditorValidationErrorCode[] = [];
  if (!draft.questionEffective.trim()) errors.push("question_required");
  if (draft.entities.length === 0) errors.push("entity_required");
  if (draft.filters.some((filter) => !filter.propertyNodeId)) {
    errors.push("filter_property_required");
  }
  if (
    draft.filters.some(
      (filter) =>
        filter.operator !== "is_null"
        && filter.operator !== "is_not_null"
        && !filter.valueText.trim()
    )
  ) {
    errors.push("filter_value_required");
  }
  const hasTimeValue = Boolean(
    draft.timeRange.start
    || draft.timeRange.end
    || draft.timeRange.relativeExpression
  );
  if (hasTimeValue && !draft.timeRange.propertyNodeId) {
    errors.push("time_property_required");
  }
  if (draft.limit.trim()) {
    const limit = Number(draft.limit);
    if (!Number.isInteger(limit) || limit < 1 || limit > 5000) errors.push("limit_invalid");
  }
  if (draft.sorts.some((sort) => !sort.targetId)) errors.push("sort_target_required");
  return Array.from(new Set(errors));
}

export function buildIntentEditorPatch(
  intent: QuestionIntentGraph,
  draft: IntentEditorDraft,
  summaryJa: string
): IntentEditorPatchResult {
  const operations: GraphPatchOperation[] = [];
  const currentQuestion =
    intent.question_effective || intent.rewritten_question || intent.question || intent.question_original || "";
  pushReplace(
    operations,
    "/question_effective",
    currentQuestion,
    draft.questionEffective.trim(),
    summaryJa
  );
  pushReplace(
    operations,
    "/entities",
    intent.entities.map((value, index) => originalConceptPayload(value, "entity", index)),
    draft.entities.map((value) => conceptPayload(value, "entity")),
    summaryJa
  );
  pushReplace(
    operations,
    "/metrics",
    intent.metrics.map((value, index) => originalConceptPayload(value, "metric", index)),
    draft.metrics.map((value) => conceptPayload(value, "metric")),
    summaryJa
  );
  pushReplace(
    operations,
    "/dimensions",
    intent.dimensions.map((value, index) => originalConceptPayload(value, "dimension", index)),
    draft.dimensions.map((value) => conceptPayload(value, "dimension")),
    summaryJa
  );
  pushReplace(
    operations,
    "/filters",
    intent.filters.map(originalFilterPayload),
    draft.filters.map(filterPayload),
    summaryJa
  );
  pushReplace(
    operations,
    "/time_range",
    originalTimeRangePayload(intent.time_range),
    timeRangePayload(draft.timeRange),
    summaryJa
  );
  pushReplace(
    operations,
    "/granularity",
    intent.granularity || intent.grain || "",
    draft.granularity,
    summaryJa
  );
  pushReplace(
    operations,
    "/sorts",
    (intent.sorts ?? intent.sort ?? []).map(originalSortPayload),
    draft.sorts.map(sortPayload),
    summaryJa
  );
  pushReplace(
    operations,
    "/limit",
    intent.limit ?? null,
    draft.limit.trim() ? Number(draft.limit) : null,
    summaryJa
  );
  pushReplace(
    operations,
    "/selected_path_id",
    intent.selected_path_id ?? null,
    draft.selectedPathId || null,
    summaryJa
  );
  return {
    patch: {
      base_version: intent.version,
      operations,
      summary_ja: summaryJa,
    },
    errors: validateIntentEditorDraft(draft),
  };
}

export function intentPathOptions(intent: QuestionIntentGraph): Array<{
  id: string;
  label: string;
  approved: boolean;
}> {
  return (intent.candidate_paths ?? [])
    .filter((path): path is IntentRelationshipPath & { id: string } => Boolean(path.id))
    .map((path) => ({
      id: path.id,
      label: path.name_ja || path.label || path.node_ids.join(" → "),
      approved: path.approved ?? path.reviewed ?? false,
    }));
}
