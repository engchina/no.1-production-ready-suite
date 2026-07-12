import {
  CheckCircle2,
  Pencil,
  Plus,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";

import {
  Banner,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  StatusBadge,
} from "@engchina/production-ready-ui";

import { t } from "@/lib/i18n";
import {
  buildIntentEditorPatch,
  createIntentEditorDraft,
  intentEditorConceptFromOption,
  intentEditorFilterFromOption,
  intentEditorNodeOptions,
  intentEditorSort,
  intentPathOptions,
  type IntentEditorConceptDraft,
  type IntentEditorDraft,
  type IntentEditorNodeOption,
  type IntentEditorSortDraft,
  type IntentEditorValidationErrorCode,
} from "./IntentEditorCore";
import type {
  GraphPatch,
  OntologyGraph,
  QuestionIntentGraph,
} from "./types";

export interface IntentEditorProps {
  intent: QuestionIntentGraph;
  businessGraph: OntologyGraph;
  onApply?: (patch: GraphPatch) => void | Promise<void>;
  busy?: boolean;
  disabled?: boolean;
}

const inputClass =
  "min-h-11 w-full min-w-0 rounded-md border border-border bg-card px-3 py-2 text-base text-foreground outline-none focus:border-primary focus:ring-2 focus:ring-ring/40 disabled:cursor-not-allowed disabled:bg-muted/30 disabled:text-muted sm:text-sm";
const selectClass = `${inputClass} cursor-pointer`;

const ERROR_KEYS: Record<IntentEditorValidationErrorCode, string> = {
  question_required: "nl2sql.intentEditor.error.questionRequired",
  entity_required: "nl2sql.intentEditor.error.entityRequired",
  filter_property_required: "nl2sql.intentEditor.error.filterPropertyRequired",
  filter_value_required: "nl2sql.intentEditor.error.filterValueRequired",
  time_property_required: "nl2sql.intentEditor.error.timePropertyRequired",
  limit_invalid: "nl2sql.intentEditor.error.limitInvalid",
  sort_target_required: "nl2sql.intentEditor.error.sortTargetRequired",
};

const AGGREGATION_OPTIONS = [
  { value: "", labelKey: "nl2sql.intentEditor.aggregation.none" },
  { value: "COUNT", labelKey: "nl2sql.intentEditor.aggregation.count" },
  { value: "SUM", labelKey: "nl2sql.intentEditor.aggregation.sum" },
  { value: "AVG", labelKey: "nl2sql.intentEditor.aggregation.average" },
  { value: "MIN", labelKey: "nl2sql.intentEditor.aggregation.minimum" },
  { value: "MAX", labelKey: "nl2sql.intentEditor.aggregation.maximum" },
];

const GRANULARITY_OPTIONS = [
  { value: "", labelKey: "nl2sql.intentEditor.granularity.none" },
  { value: "day", labelKey: "nl2sql.intentEditor.granularity.day" },
  { value: "week", labelKey: "nl2sql.intentEditor.granularity.week" },
  { value: "month", labelKey: "nl2sql.intentEditor.granularity.month" },
  { value: "quarter", labelKey: "nl2sql.intentEditor.granularity.quarter" },
  { value: "year", labelKey: "nl2sql.intentEditor.granularity.year" },
];

const FILTER_OPERATOR_OPTIONS = [
  { value: "=", labelKey: "nl2sql.intentEditor.operator.equals" },
  { value: "!=", labelKey: "nl2sql.intentEditor.operator.notEquals" },
  { value: "contains", labelKey: "nl2sql.intentEditor.operator.contains" },
  { value: ">", labelKey: "nl2sql.intentEditor.operator.greaterThan" },
  { value: ">=", labelKey: "nl2sql.intentEditor.operator.greaterOrEqual" },
  { value: "<", labelKey: "nl2sql.intentEditor.operator.lessThan" },
  { value: "<=", labelKey: "nl2sql.intentEditor.operator.lessOrEqual" },
  { value: "is_null", labelKey: "nl2sql.intentEditor.operator.empty" },
  { value: "is_not_null", labelKey: "nl2sql.intentEditor.operator.notEmpty" },
];

function updateAt<T>(values: T[], index: number, update: (value: T) => T): T[] {
  return values.map((value, currentIndex) =>
    currentIndex === index ? update(value) : value
  );
}

function removeAt<T>(values: T[], index: number): T[] {
  return values.filter((_, currentIndex) => currentIndex !== index);
}

function optionForCurrentConcept(
  concept: IntentEditorConceptDraft,
  options: IntentEditorNodeOption[]
): IntentEditorNodeOption | null {
  const id = concept.ontologyNodeId;
  if (!id || options.some((option) => option.id === id)) return null;
  return {
    id,
    label: concept.nameJa,
    kind: "unknown",
    physicalObjectIds: concept.physicalObjectIds,
  };
}

function ConceptNodeSelect({
  id,
  label,
  concept,
  options,
  disabled,
  onChange,
}: {
  id: string;
  label: string;
  concept: IntentEditorConceptDraft;
  options: IntentEditorNodeOption[];
  disabled: boolean;
  onChange: (option: IntentEditorNodeOption) => void;
}) {
  const fallbackOption = optionForCurrentConcept(concept, options);
  const value = concept.ontologyNodeId || `legacy:${concept.key}`;
  return (
    <div className="min-w-0 space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium text-foreground">
        {label}
      </label>
      <select
        id={id}
        className={selectClass}
        value={value}
        disabled={disabled}
        onChange={(event) => {
          const option = options.find((item) => item.id === event.target.value);
          if (option) onChange(option);
        }}
      >
        {!concept.ontologyNodeId ? (
          <option value={`legacy:${concept.key}`}>{concept.nameJa}</option>
        ) : null}
        {fallbackOption ? (
          <option value={fallbackOption.id}>{fallbackOption.label}</option>
        ) : null}
        {options.map((option) => (
          <option key={option.id} value={option.id}>{option.label}</option>
        ))}
      </select>
    </div>
  );
}

function EmptyRows({ children }: { children: string }) {
  return (
    <p className="rounded-md border border-dashed border-border bg-background px-3 py-4 text-sm leading-6 text-muted">
      {children}
    </p>
  );
}

function AddButton({
  children,
  disabled,
  onClick,
}: {
  children: string;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <Button
      type="button"
      variant="secondary"
      size="sm"
      className="min-h-11"
      disabled={disabled}
      onClick={onClick}
    >
      <Plus size={15} aria-hidden="true" />
      {children}
    </Button>
  );
}

function RemoveButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="min-h-11 text-danger hover:bg-danger-bg"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
    >
      <Trash2 size={15} aria-hidden="true" />
      <span>{t("nl2sql.intentEditor.remove")}</span>
    </Button>
  );
}

function availableOption(
  options: IntentEditorNodeOption[],
  selectedNodeIds: string[]
): IntentEditorNodeOption | undefined {
  const selected = new Set(selectedNodeIds);
  return options.find((option) => !selected.has(option.id));
}

export function IntentEditor({
  intent,
  businessGraph,
  onApply,
  busy = false,
  disabled = false,
}: IntentEditorProps) {
  const idPrefix = useId().replace(/:/g, "-");
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<IntentEditorDraft>(() => createIntentEditorDraft(intent));
  const [showErrors, setShowErrors] = useState(false);
  const entityOptions = useMemo(
    () => intentEditorNodeOptions(businessGraph, "entity"),
    [businessGraph]
  );
  const metricOptions = useMemo(
    () => intentEditorNodeOptions(businessGraph, "metric"),
    [businessGraph]
  );
  const propertyOptions = useMemo(
    () => intentEditorNodeOptions(businessGraph, "property"),
    [businessGraph]
  );
  const graphSortOptions = useMemo(
    () => intentEditorNodeOptions(businessGraph, "sort"),
    [businessGraph]
  );
  const pathOptions = useMemo(() => intentPathOptions(intent), [intent]);
  const patchResult = useMemo(
    () => buildIntentEditorPatch(intent, draft, t("nl2sql.intentEditor.patchSummary")),
    [draft, intent]
  );
  const dirty = patchResult.patch.operations.length > 0;
  const formDisabled = disabled || busy;

  useEffect(() => {
    setDraft(createIntentEditorDraft(intent));
    setShowErrors(false);
  }, [intent]);

  const reset = () => {
    setDraft(createIntentEditorDraft(intent));
    setShowErrors(false);
  };

  const addConcept = (
    field: "entities" | "metrics" | "dimensions",
    options: IntentEditorNodeOption[]
  ) => {
    const current = draft[field];
    const option = availableOption(options, current.map((item) => item.ontologyNodeId));
    if (!option) return;
    const prefix = field === "entities" ? "entity" : field === "metrics" ? "metric" : "dimension";
    setDraft((value) => ({
      ...value,
      [field]: [
        ...value[field],
        intentEditorConceptFromOption(option, prefix, value[field].length + 1),
      ],
    }));
  };

  const replaceConcept = (
    field: "entities" | "metrics" | "dimensions",
    index: number,
    option: IntentEditorNodeOption
  ) => {
    setDraft((value) => ({
      ...value,
      [field]: updateAt(value[field], index, (item) => ({
        ...item,
        ontologyNodeId: option.id,
        nameJa: option.label,
        physicalObjectIds: field === "entities" ? option.physicalObjectIds : [],
      })),
    }));
  };

  const sortOptions = useMemo(() => {
    const selected = [
      ...draft.entities.map((item) => ({ id: item.id, label: item.nameJa })),
      ...draft.metrics.map((item) => ({ id: item.id, label: item.nameJa })),
      ...draft.dimensions.map((item) => ({ id: item.id, label: item.nameJa })),
      ...graphSortOptions.map((item) => ({ id: item.id, label: item.label })),
    ];
    return Array.from(new Map(selected.map((item) => [item.id, item])).values());
  }, [draft.dimensions, draft.entities, draft.metrics, graphSortOptions]);

  const submit = async () => {
    setShowErrors(true);
    if (patchResult.errors.length > 0 || !dirty || !onApply) return;
    await onApply(patchResult.patch);
  };

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <CardTitle>{t("nl2sql.intentEditor.title")}</CardTitle>
          <CardDescription className="mt-1 leading-6">
            {t("nl2sql.intentEditor.description")}
          </CardDescription>
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="min-h-11"
          aria-expanded={open}
          aria-controls={`${idPrefix}-form`}
          onClick={() => setOpen((value) => !value)}
          disabled={disabled}
        >
          <Pencil size={15} aria-hidden="true" />
          {open ? t("nl2sql.intentEditor.close") : t("nl2sql.intentEditor.open")}
        </Button>
      </CardHeader>
      {open ? (
        <CardContent id={`${idPrefix}-form`} className="space-y-5">
          <Banner severity="info" title={t("nl2sql.intentEditor.sessionOnlyTitle")}>
            {t("nl2sql.intentEditor.sessionOnlyDescription")}
          </Banner>

          <form
            className="space-y-6"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <div className="space-y-1.5">
              <label htmlFor={`${idPrefix}-question`} className="text-sm font-medium text-foreground">
                {t("nl2sql.intentEditor.effectiveQuestion")}
              </label>
              <textarea
                id={`${idPrefix}-question`}
                className={`${inputClass} min-h-24 resize-y leading-6`}
                value={draft.questionEffective}
                disabled={formDisabled}
                aria-describedby={`${idPrefix}-question-help`}
                onChange={(event) => setDraft((value) => ({ ...value, questionEffective: event.target.value }))}
              />
              <p id={`${idPrefix}-question-help`} className="text-xs leading-5 text-muted">
                {t("nl2sql.intentEditor.effectiveQuestionHelp")}
              </p>
            </div>

            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-sm font-semibold text-foreground">
                {t("nl2sql.intentEditor.entities")}
              </legend>
              {draft.entities.length === 0 ? (
                <EmptyRows>{t("nl2sql.intentEditor.entitiesEmpty")}</EmptyRows>
              ) : (
                <div className="grid gap-3">
                  {draft.entities.map((entity, index) => (
                    <div key={entity.key} className="grid min-w-0 gap-3 rounded-lg bg-background p-3">
                      <ConceptNodeSelect
                        id={`${idPrefix}-entity-${index}`}
                        label={t("nl2sql.intentEditor.entity")}
                        concept={entity}
                        options={entityOptions}
                        disabled={formDisabled}
                        onChange={(option) => replaceConcept("entities", index, option)}
                      />
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-entity-role-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.entityRole")}
                        </label>
                        <select
                          id={`${idPrefix}-entity-role-${index}`}
                          className={selectClass}
                          value={entity.role}
                          disabled={formDisabled}
                          onChange={(event) => setDraft((value) => ({
                            ...value,
                            entities: updateAt(value.entities, index, (item) => ({ ...item, role: event.target.value })),
                          }))}
                        >
                          <option value="subject">{t("nl2sql.intentEditor.entityRole.subject")}</option>
                          <option value="related">{t("nl2sql.intentEditor.entityRole.related")}</option>
                          <option value="event">{t("nl2sql.intentEditor.entityRole.event")}</option>
                        </select>
                      </div>
                      <RemoveButton
                        label={t("nl2sql.intentEditor.removeNamed", { name: entity.nameJa })}
                        disabled={formDisabled}
                        onClick={() => setDraft((value) => ({ ...value, entities: removeAt(value.entities, index) }))}
                      />
                    </div>
                  ))}
                </div>
              )}
              <AddButton
                disabled={formDisabled || !availableOption(entityOptions, draft.entities.map((item) => item.ontologyNodeId))}
                onClick={() => addConcept("entities", entityOptions)}
              >
                {t("nl2sql.intentEditor.addEntity")}
              </AddButton>
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-sm font-semibold text-foreground">
                {t("nl2sql.intentEditor.metrics")}
              </legend>
              {draft.metrics.length === 0 ? (
                <EmptyRows>{t("nl2sql.intentEditor.metricsEmpty")}</EmptyRows>
              ) : (
                <div className="grid gap-3">
                  {draft.metrics.map((metric, index) => (
                    <div key={metric.key} className="grid min-w-0 gap-3 rounded-lg bg-background p-3">
                      <ConceptNodeSelect
                        id={`${idPrefix}-metric-${index}`}
                        label={t("nl2sql.intentEditor.metric")}
                        concept={metric}
                        options={metricOptions}
                        disabled={formDisabled}
                        onChange={(option) => replaceConcept("metrics", index, option)}
                      />
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-aggregation-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.aggregation")}
                        </label>
                        <select
                          id={`${idPrefix}-aggregation-${index}`}
                          className={selectClass}
                          value={metric.aggregation}
                          disabled={formDisabled}
                          onChange={(event) => setDraft((value) => ({
                            ...value,
                            metrics: updateAt(value.metrics, index, (item) => ({ ...item, aggregation: event.target.value })),
                          }))}
                        >
                          {AGGREGATION_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>{t(option.labelKey)}</option>
                          ))}
                        </select>
                      </div>
                      <RemoveButton
                        label={t("nl2sql.intentEditor.removeNamed", { name: metric.nameJa })}
                        disabled={formDisabled}
                        onClick={() => setDraft((value) => ({ ...value, metrics: removeAt(value.metrics, index) }))}
                      />
                    </div>
                  ))}
                </div>
              )}
              <AddButton
                disabled={formDisabled || !availableOption(metricOptions, draft.metrics.map((item) => item.ontologyNodeId))}
                onClick={() => addConcept("metrics", metricOptions)}
              >
                {t("nl2sql.intentEditor.addMetric")}
              </AddButton>
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-sm font-semibold text-foreground">
                {t("nl2sql.intentEditor.dimensions")}
              </legend>
              {draft.dimensions.length === 0 ? (
                <EmptyRows>{t("nl2sql.intentEditor.dimensionsEmpty")}</EmptyRows>
              ) : (
                <div className="grid gap-3">
                  {draft.dimensions.map((dimension, index) => (
                    <div key={dimension.key} className="grid min-w-0 gap-3 rounded-lg bg-background p-3">
                      <ConceptNodeSelect
                        id={`${idPrefix}-dimension-${index}`}
                        label={t("nl2sql.intentEditor.dimension")}
                        concept={dimension}
                        options={propertyOptions}
                        disabled={formDisabled}
                        onChange={(option) => replaceConcept("dimensions", index, option)}
                      />
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-dimension-grain-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.dimensionGranularity")}
                        </label>
                        <select
                          id={`${idPrefix}-dimension-grain-${index}`}
                          className={selectClass}
                          value={dimension.granularity}
                          disabled={formDisabled}
                          onChange={(event) => setDraft((value) => ({
                            ...value,
                            dimensions: updateAt(value.dimensions, index, (item) => ({ ...item, granularity: event.target.value })),
                          }))}
                        >
                          {GRANULARITY_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>{t(option.labelKey)}</option>
                          ))}
                        </select>
                      </div>
                      <RemoveButton
                        label={t("nl2sql.intentEditor.removeNamed", { name: dimension.nameJa })}
                        disabled={formDisabled}
                        onClick={() => setDraft((value) => ({ ...value, dimensions: removeAt(value.dimensions, index) }))}
                      />
                    </div>
                  ))}
                </div>
              )}
              <AddButton
                disabled={formDisabled || !availableOption(propertyOptions, draft.dimensions.map((item) => item.ontologyNodeId))}
                onClick={() => addConcept("dimensions", propertyOptions)}
              >
                {t("nl2sql.intentEditor.addDimension")}
              </AddButton>
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-sm font-semibold text-foreground">
                {t("nl2sql.intentEditor.filters")}
              </legend>
              {draft.filters.length === 0 ? (
                <EmptyRows>{t("nl2sql.intentEditor.filtersEmpty")}</EmptyRows>
              ) : (
                <div className="grid gap-3">
                  {draft.filters.map((filter, index) => (
                    <div key={filter.key} className="grid min-w-0 gap-3 rounded-lg bg-background p-3">
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-filter-property-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.filterProperty")}
                        </label>
                        <select
                          id={`${idPrefix}-filter-property-${index}`}
                          className={selectClass}
                          value={filter.propertyNodeId}
                          disabled={formDisabled}
                          onChange={(event) => {
                            const option = propertyOptions.find((item) => item.id === event.target.value);
                            if (!option) return;
                            setDraft((value) => ({
                              ...value,
                              filters: updateAt(value.filters, index, (item) => ({
                                ...item,
                                propertyNodeId: option.id,
                                labelJa: option.label,
                              })),
                            }));
                          }}
                        >
                          {propertyOptions.map((option) => (
                            <option key={option.id} value={option.id}>{option.label}</option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-filter-operator-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.filterOperator")}
                        </label>
                        <select
                          id={`${idPrefix}-filter-operator-${index}`}
                          className={selectClass}
                          value={filter.operator}
                          disabled={formDisabled}
                          onChange={(event) => setDraft((value) => ({
                            ...value,
                            filters: updateAt(value.filters, index, (item) => ({ ...item, operator: event.target.value })),
                          }))}
                        >
                          {FILTER_OPERATOR_OPTIONS.map((option) => (
                            <option key={option.value} value={option.value}>{t(option.labelKey)}</option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-filter-value-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.filterValue")}
                        </label>
                        <input
                          id={`${idPrefix}-filter-value-${index}`}
                          className={inputClass}
                          value={filter.valueText}
                          disabled={formDisabled || filter.operator === "is_null" || filter.operator === "is_not_null"}
                          onChange={(event) => setDraft((value) => ({
                            ...value,
                            filters: updateAt(value.filters, index, (item) => ({ ...item, valueText: event.target.value })),
                          }))}
                        />
                      </div>
                      <RemoveButton
                        label={t("nl2sql.intentEditor.removeFilter", { number: index + 1 })}
                        disabled={formDisabled}
                        onClick={() => setDraft((value) => ({ ...value, filters: removeAt(value.filters, index) }))}
                      />
                    </div>
                  ))}
                </div>
              )}
              <AddButton
                disabled={formDisabled || propertyOptions.length === 0}
                onClick={() => {
                  const option = propertyOptions[0];
                  if (!option) return;
                  setDraft((value) => ({
                    ...value,
                    filters: [...value.filters, intentEditorFilterFromOption(option, value.filters.length + 1)],
                  }));
                }}
              >
                {t("nl2sql.intentEditor.addFilter")}
              </AddButton>
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-sm font-semibold text-foreground">
                {t("nl2sql.intentEditor.timeAndGranularity")}
              </legend>
              <div className="grid min-w-0 gap-3">
                <div className="space-y-1.5">
                  <label htmlFor={`${idPrefix}-time-property`} className="text-sm font-medium text-foreground">
                    {t("nl2sql.intentEditor.timeProperty")}
                  </label>
                  <select
                    id={`${idPrefix}-time-property`}
                    className={selectClass}
                    value={draft.timeRange.propertyNodeId}
                    disabled={formDisabled || propertyOptions.length === 0}
                    onChange={(event) => {
                      const option = propertyOptions.find((item) => item.id === event.target.value);
                      setDraft((value) => ({
                        ...value,
                        timeRange: {
                          ...value.timeRange,
                          propertyNodeId: option?.id || "",
                          labelJa: option?.label || "",
                        },
                      }));
                    }}
                  >
                    <option value="">{t("nl2sql.intentEditor.selectNone")}</option>
                    {propertyOptions.map((option) => (
                      <option key={option.id} value={option.id}>{option.label}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1.5">
                  <label htmlFor={`${idPrefix}-time-start`} className="text-sm font-medium text-foreground">
                    {t("nl2sql.intentEditor.timeStart")}
                  </label>
                  <input
                    id={`${idPrefix}-time-start`}
                    type="date"
                    className={inputClass}
                    value={draft.timeRange.start}
                    disabled={formDisabled}
                    onChange={(event) => setDraft((value) => ({
                      ...value,
                      timeRange: { ...value.timeRange, start: event.target.value },
                    }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor={`${idPrefix}-time-end`} className="text-sm font-medium text-foreground">
                    {t("nl2sql.intentEditor.timeEnd")}
                  </label>
                  <input
                    id={`${idPrefix}-time-end`}
                    type="date"
                    className={inputClass}
                    value={draft.timeRange.end}
                    disabled={formDisabled}
                    onChange={(event) => setDraft((value) => ({
                      ...value,
                      timeRange: { ...value.timeRange, end: event.target.value },
                    }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor={`${idPrefix}-time-relative`} className="text-sm font-medium text-foreground">
                    {t("nl2sql.intentEditor.relativeTime")}
                  </label>
                  <input
                    id={`${idPrefix}-time-relative`}
                    className={inputClass}
                    value={draft.timeRange.relativeExpression}
                    placeholder={t("nl2sql.intentEditor.relativeTimePlaceholder")}
                    disabled={formDisabled}
                    onChange={(event) => setDraft((value) => ({
                      ...value,
                      timeRange: { ...value.timeRange, relativeExpression: event.target.value },
                    }))}
                  />
                </div>
                <div className="space-y-1.5">
                  <label htmlFor={`${idPrefix}-granularity`} className="text-sm font-medium text-foreground">
                    {t("nl2sql.intentEditor.overallGranularity")}
                  </label>
                  <select
                    id={`${idPrefix}-granularity`}
                    className={selectClass}
                    value={draft.granularity}
                    disabled={formDisabled}
                    onChange={(event) => setDraft((value) => ({ ...value, granularity: event.target.value }))}
                  >
                    {GRANULARITY_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{t(option.labelKey)}</option>
                    ))}
                  </select>
                </div>
              </div>
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-sm font-semibold text-foreground">
                {t("nl2sql.intentEditor.sortAndLimit")}
              </legend>
              {draft.sorts.length === 0 ? (
                <EmptyRows>{t("nl2sql.intentEditor.sortsEmpty")}</EmptyRows>
              ) : (
                <div className="grid gap-3">
                  {draft.sorts.map((sort, index) => (
                    <div key={sort.key} className="grid min-w-0 gap-3 rounded-lg bg-background p-3">
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-sort-target-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.sortTarget")}
                        </label>
                        <select
                          id={`${idPrefix}-sort-target-${index}`}
                          className={selectClass}
                          value={sort.targetId}
                          disabled={formDisabled}
                          onChange={(event) => setDraft((value) => ({
                            ...value,
                            sorts: updateAt(value.sorts, index, (item) => ({ ...item, targetId: event.target.value })),
                          }))}
                        >
                          <option value="">{t("nl2sql.intentEditor.selectNone")}</option>
                          {sortOptions.map((option) => (
                            <option key={option.id} value={option.id}>{option.label}</option>
                          ))}
                        </select>
                      </div>
                      <div className="space-y-1.5">
                        <label htmlFor={`${idPrefix}-sort-direction-${index}`} className="text-sm font-medium text-foreground">
                          {t("nl2sql.intentEditor.sortDirection")}
                        </label>
                        <select
                          id={`${idPrefix}-sort-direction-${index}`}
                          className={selectClass}
                          value={sort.direction}
                          disabled={formDisabled}
                          onChange={(event) => setDraft((value) => ({
                            ...value,
                            sorts: updateAt(value.sorts, index, (item) => ({
                              ...item,
                              direction: event.target.value as IntentEditorSortDraft["direction"],
                            })),
                          }))}
                        >
                          <option value="asc">{t("nl2sql.intentEditor.sortDirection.asc")}</option>
                          <option value="desc">{t("nl2sql.intentEditor.sortDirection.desc")}</option>
                        </select>
                      </div>
                      <RemoveButton
                        label={t("nl2sql.intentEditor.removeSort", { number: index + 1 })}
                        disabled={formDisabled}
                        onClick={() => setDraft((value) => ({ ...value, sorts: removeAt(value.sorts, index) }))}
                      />
                    </div>
                  ))}
                </div>
              )}
              <div className="grid gap-3">
                <AddButton
                  disabled={formDisabled || sortOptions.length === 0}
                  onClick={() => {
                    const option = sortOptions[0];
                    if (!option) return;
                    setDraft((value) => ({
                      ...value,
                      sorts: [...value.sorts, intentEditorSort(option.id, value.sorts.length + 1)],
                    }));
                  }}
                >
                  {t("nl2sql.intentEditor.addSort")}
                </AddButton>
                <div className="w-full space-y-1.5">
                  <label htmlFor={`${idPrefix}-limit`} className="text-sm font-medium text-foreground">
                    {t("nl2sql.intentEditor.limit")}
                  </label>
                  <input
                    id={`${idPrefix}-limit`}
                    type="number"
                    inputMode="numeric"
                    min="1"
                    max="5000"
                    className={inputClass}
                    value={draft.limit}
                    disabled={formDisabled}
                    onChange={(event) => setDraft((value) => ({ ...value, limit: event.target.value }))}
                  />
                </div>
              </div>
            </fieldset>

            <fieldset className="space-y-3 rounded-lg border border-border p-4">
              <legend className="px-1 text-sm font-semibold text-foreground">
                {t("nl2sql.intentEditor.relationshipPath")}
              </legend>
              <div className="space-y-1.5">
                <label htmlFor={`${idPrefix}-path`} className="text-sm font-medium text-foreground">
                  {t("nl2sql.intentEditor.relationshipPathSelect")}
                </label>
                <select
                  id={`${idPrefix}-path`}
                  className={selectClass}
                  value={draft.selectedPathId}
                  disabled={formDisabled || pathOptions.length === 0}
                  onChange={(event) => setDraft((value) => ({ ...value, selectedPathId: event.target.value }))}
                >
                  <option value="">{t("nl2sql.intentEditor.selectNone")}</option>
                  {pathOptions.map((path) => (
                    <option key={path.id} value={path.id} disabled={!path.approved}>
                      {path.label} {path.approved ? `(${t("nl2sql.intentEditor.pathApproved")})` : `(${t("nl2sql.intentEditor.pathUnapproved")})`}
                    </option>
                  ))}
                </select>
                <p className="text-xs leading-5 text-muted">
                  {pathOptions.length > 0
                    ? t("nl2sql.intentEditor.relationshipPathHelp")
                    : t("nl2sql.intentEditor.relationshipPathEmpty")}
                </p>
              </div>
            </fieldset>

            {showErrors && patchResult.errors.length > 0 ? (
              <Banner severity="danger" title={t("nl2sql.intentEditor.validationTitle")}>
                <ul className="list-disc space-y-1 pl-5">
                  {patchResult.errors.map((error) => (
                    <li key={error}>{t(ERROR_KEYS[error])}</li>
                  ))}
                </ul>
              </Banner>
            ) : null}

            <div className="grid gap-3 rounded-lg border border-border bg-background p-4">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    variant={dirty ? "info" : "neutral"}
                    label={
                      dirty
                        ? t("nl2sql.intentEditor.changeCount", { count: patchResult.patch.operations.length })
                        : t("nl2sql.intentEditor.noChanges")
                    }
                  />
                  <span className="text-xs text-muted">
                    {t("nl2sql.intentEditor.currentVersion", { version: intent.version })}
                  </span>
                </div>
                <p className="mt-2 text-sm leading-6 text-foreground">
                  {t("nl2sql.intentEditor.applyHelp")}
                </p>
              </div>
              <div className="grid gap-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="md"
                  className="min-h-11 w-full"
                  disabled={formDisabled || !dirty}
                  onClick={reset}
                >
                  <RotateCcw size={15} aria-hidden="true" />
                  {t("nl2sql.intentEditor.reset")}
                </Button>
                <Button
                  type="submit"
                  size="md"
                  className="min-h-11 w-full"
                  loading={busy}
                  disabled={disabled || !onApply || !dirty}
                >
                  <CheckCircle2 size={16} aria-hidden="true" />
                  {t("nl2sql.session.applyPatch")}
                </Button>
              </div>
            </div>
          </form>
        </CardContent>
      ) : null}
    </Card>
  );
}
