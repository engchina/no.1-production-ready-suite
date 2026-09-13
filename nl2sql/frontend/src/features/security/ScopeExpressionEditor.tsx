import { useWorkspaceActive } from "@/components/WorkspaceState";
import { useDatabaseStatus } from "@/lib/queries";
import {
  FieldError,
  Button,
} from "@engchina/production-ready-ui";
import { ErrorState } from "@/components/StateViews";
import {
  cloneElement,
  useId,
  useEffect,
  useRef,
  type ReactElement,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { t } from "@/lib/i18n";
import { securityApi } from "./api";
import { useAuth } from "./AuthProvider";
import {
  blankCondition,
  columnValueType,
  expressionCounts,
  expressionError,
  expressionSummary,
  filterError,
  scopeOperators,
} from "./scope-expression";
import type {
  DataEntitlementScopeFilter,
  DeepSecTargetColumn,
  ScopeExpression,
  ScopeGroup,
  ScopeNode,
  ScopeRelatedExists,
} from "./types";

const text = (key: string) => t(`security.deepsec.entitlements.${key}`);
const inputClass =
  "min-h-[44px] w-full min-w-0 rounded-md border border-border bg-surface-sunken px-3 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-focus-ring disabled:opacity-60";
function Labeled({ label, children }: { label: string; children: ReactNode }) {
  const generatedId = useId();
  const child = children as ReactElement<{ id?: string }>;
  const id = child.props.id ?? generatedId;
  return (
    <div className="grid min-w-0 gap-1 text-xs font-medium">
      <label htmlFor={id}>{label}</label>
      {cloneElement(child, { id })}
    </div>
  );
}
function InlineError({ message, id }: { message: string; id?: string }) {
  const fallbackId = useId();
  return message ? (
    <FieldError id={id ?? fallbackId} message={text(message)} />
  ) : null;
}

function ConditionEditor({
  filter,
  columns,
  onChange,
  id,
}: {
  filter: DataEntitlementScopeFilter;
  columns: DeepSecTargetColumn[];
  onChange: (filter: DataEntitlementScopeFilter) => void;
  id: string;
}) {
  const suffix = id.replace("security-deepsec-scope-filter-", "");
  const patch = (change: Partial<DataEntitlementScopeFilter>) =>
    onChange({ ...filter, ...change });
  const operators = scopeOperators[filter.value_type] ?? [];
  const valueSource = filter.value_source ?? "LITERAL";
  const hasValueSource =
    filter.operator === "EQ" && ["TEXT", "NUMBER"].includes(filter.value_type);
  const error = filterError(filter);
  const inputProps = {
    className: inputClass,
    "aria-invalid": Boolean(error),
    "aria-describedby": error ? id + "-error" : undefined,
  };
  return (
    <div className="grid gap-2">
      <div className="grid min-w-0 gap-2 md:grid-cols-2">
        <Labeled label={text("scopeFilterColumn")}>
          <select
            id={`deepsec-scope-filter-column-${suffix}`}
            className={inputClass}
            value={filter.column_name}
            onChange={(e) => {
              const column = columns.find(
                (c) => c.column_name === e.target.value,
              );
              onChange({
                column_name: e.target.value,
                operator: "EQ",
                value_type: columnValueType(column?.data_type ?? "") ?? "TEXT",
                value_source: "LITERAL",
                value: "",
                value_to: "",
                values: [],
              });
            }}
          >
            <option value="">{text("scopeFilterColumnPlaceholder")}</option>
            {columns
              .filter((c) => columnValueType(c.data_type))
              .map((c) => (
                <option key={c.column_name} value={c.column_name}>
                  {c.column_name} · {c.data_type}
                </option>
              ))}
          </select>
        </Labeled>
        <Labeled label={text("scopeFilterOperator")}>
          <select
            id={`deepsec-scope-filter-operator-${suffix}`}
            className={inputClass}
            value={filter.operator}
            onChange={(e) =>
              patch({
                operator: e.target.value,
                value_source: "LITERAL",
                value: "",
                value_to: "",
                values: [],
              })
            }
          >
            {operators.map((op) => (
              <option key={op} value={op}>
                {text("operator." + op)}
              </option>
            ))}
          </select>
        </Labeled>
        {hasValueSource && (
          <Labeled label={text("scopeFilterValueSource")}>
            <select
              id={`deepsec-scope-filter-value-source-${suffix}`}
              className={inputClass}
              value={valueSource}
              onChange={(e) =>
                patch({
                  value_source: e.target.value,
                  value: "",
                  value_to: "",
                  values: [],
                })
              }
            >
              <option value="LITERAL">{text("scopeFilterValueLiteral")}</option>
              <option value="LOGIN_USER_ID">
                {text("scopeFilterValueLoginUserId")}
              </option>
            </select>
          </Labeled>
        )}
        {!["IS_NULL", "IS_NOT_NULL"].includes(filter.operator) &&
          valueSource !== "LOGIN_USER_ID" && (
            <>
              {filter.operator === "IN" ? (
                <Labeled label={text("scopeFilterValues")}>
                  <input
                    id={`deepsec-scope-filter-values-${suffix}`}
                    {...inputProps}
                    value={(filter.values ?? []).join(",")}
                    onChange={(e) =>
                      patch({ values: e.target.value.split(",") })
                    }
                  />
                </Labeled>
              ) : (
                <Labeled label={text("scopeFilterValue")}>
                  <input
                    id={`deepsec-scope-filter-value-${suffix}`}
                    {...inputProps}
                    inputMode={
                      filter.value_type === "NUMBER"
                        ? filter.operator === "EQ"
                          ? "numeric"
                          : "decimal"
                        : "text"
                    }
                    value={filter.value ?? ""}
                    onChange={(e) => patch({ value: e.target.value })}
                  />
                </Labeled>
              )}
              {filter.operator === "BETWEEN" && (
                <Labeled label={text("scopeFilterValueTo")}>
                  <input
                    {...inputProps}
                    value={filter.value_to ?? ""}
                    onChange={(e) => patch({ value_to: e.target.value })}
                  />
                </Labeled>
              )}
            </>
          )}
      </div>
      {valueSource === "LOGIN_USER_ID" && (
        <p
          className="text-sm text-fg-muted"
          data-testid={`security-deepsec-scope-filter-login-user-id-${suffix}`}
        >
          {text("scopeFilterValueLoginUserId")}
        </p>
      )}
      <InlineError id={id + "-error"} message={error} />
    </div>
  );
}

type GroupProps = {
  group: ScopeGroup;
  columns: DeepSecTargetColumn[];
  onChange: (group: ScopeGroup) => void;
  owner: string;
  objectName: string;
  depth: number;
  inRelation: boolean;
  budget: { conditions: number; related: number };
  path: string;
};
function GroupEditor({
  group,
  columns,
  onChange,
  owner,
  objectName,
  depth,
  inRelation,
  budget,
  path,
}: GroupProps) {
  const container = useRef<HTMLFieldSetElement>(null);
  const confirm = useConfirm();
  const changeChildren = (children: ScopeNode[], focusLast = false) => {
    onChange({ ...group, children });
    requestAnimationFrame(() => {
      if (focusLast) {
        const children = container.current?.querySelectorAll<HTMLElement>(":scope > [data-scope-child]");
        children?.item(children.length - 1)?.querySelector<HTMLSelectElement>("select")?.focus();
      } else
        container.current?.querySelector<HTMLSelectElement>("select")?.focus();
    });
  };
  return (
    <fieldset
      ref={container}
      className="grid min-w-0 gap-3 rounded-md border border-border bg-surface p-1 sm:p-3"
      data-testid={`scope-group-${path}`}
    >
      <legend className="max-w-full break-words px-1 text-sm font-medium">
        {text("expression.group")}
      </legend>
      <Labeled label={text("expression.match")}>
        <select
          className={inputClass}
          value={group.operator}
          onChange={(e) =>
            onChange({ ...group, operator: e.target.value as "AND" | "OR" })
          }
        >
          <option value="AND">{text("expression.and")}</option>
          <option value="OR">{text("expression.or")}</option>
        </select>
      </Labeled>
      {!group.children.length && <InlineError message="expression.empty" />}
      {group.children.map((node, index) => (
        <div
          key={index}
          data-testid={
            node.kind === "condition"
              ? `security-deepsec-scope-filter-${path}-${index}`
              : undefined
          }
          data-scope-child
          className="grid min-w-0 gap-2 border-l-2 border-border pl-0 sm:pl-2"
        >
          {node.kind === "condition" ? (
            <ConditionEditor
              id={`security-deepsec-scope-filter-${path}-${index}`}
              filter={node.filter}
              columns={columns}
              onChange={(filter) =>
                onChange({
                  ...group,
                  children: group.children.map((n, i) =>
                    i === index ? { kind: "condition", filter } : n,
                  ),
                })
              }
            />
          ) : node.kind === "group" ? (
            <GroupEditor
              group={node}
              columns={columns}
              owner={owner}
              objectName={objectName}
              depth={depth + 1}
              inRelation={inRelation}
              budget={budget}
              path={`${path}-${index}`}
              onChange={(next) =>
                onChange({
                  ...group,
                  children: group.children.map((n, i) =>
                    i === index ? next : n,
                  ),
                })
              }
            />
          ) : (
            <RelatedEditor
              depth={depth}
              node={node}
              columns={columns}
              owner={owner}
              objectName={objectName}
              budget={budget}
              path={`${path}-${index}`}
              onChange={(next) =>
                onChange({
                  ...group,
                  children: group.children.map((n, i) =>
                    i === index ? next : n,
                  ),
                })
              }
            />
          )}
          <Button
            iconOnly
            size="sm"
            variant="ghost"
            type="button"
            className="justify-self-end"
            aria-label={text(
              node.kind === "condition"
                ? "scopeFilterRemove"
                : "expression.removeGroup",
            )}
            onClick={async () => {
              if (
                node.kind !== "condition" &&
                !(await confirm({
                  title: text("expression.removeGroup"),
                  description: text("expression.removeConfirm"),
                  confirmLabel: text("expression.removeGroup"),
                  tone: "danger",
                }))
              )
                return;
              changeChildren(group.children.filter((_, i) => i !== index));
            }} icon={Trash2}>
            </Button>
        </div>
      ))}
      <div className="flex min-w-0 flex-wrap gap-[8px]">
        <Button
          size="sm"
          variant="secondary"
          type="button"
          disabled={budget.conditions >= 20 || !columns.length}
          onClick={() =>
            changeChildren([...group.children, blankCondition(columns)], true)
          } icon={Plus}>
          {text("scopeFilterAdd")}
        </Button>
        <Button
          size="sm"
          variant="secondary"
          type="button"
          disabled={depth >= 3 || budget.conditions >= 20 || !columns.length}
          onClick={() =>
            changeChildren(
              [
                ...group.children,
                {
                  kind: "group",
                  operator: "AND",
                  children: [blankCondition(columns)],
                },
              ],
              true,
            )
          }
        >
          {text("expression.addGroup")}
        </Button>
        {!inRelation && (
          <Button
            size="sm"
            variant="secondary"
            type="button"
            disabled={
              depth >= 3 || budget.related >= 3 || budget.conditions >= 20
            }
            onClick={() =>
              changeChildren(
                [
                  ...group.children,
                  {
                    kind: "related_exists",
                    profile_id: "",
                    object_scope_version: 1,
                    target_owner: "",
                    target_object: "",
                    target_type: "TABLE",
                    relation_source: "MANUAL",
                    relation_id: "",
                    relation_version: "",
                    join_keys: [{ source_column: "", target_column: "" }],
                    condition: {
                      kind: "group",
                      operator: "AND",
                      children: [blankCondition([])],
                    },
                  },
                ],
                true,
              )
            }
          >
            {text("expression.addRelated")}
          </Button>
        )}
      </div>
    </fieldset>
  );
}

function QueryStatus({
  loading,
  error,
  retry,
}: {
  loading: boolean;
  error: Error | null;
  retry: () => void;
}) {
  return loading ? (
    <p role="status" className="text-sm text-fg-muted">
      {text("expression.loading")}
    </p>
  ) : error ? (
    <ErrorState message={error.message} onRetry={retry} />
  ) : null;
}
function RelatedEditor({
  depth,
  node,
  columns,
  onChange,
  owner,
  objectName,
  budget,
  path,
}: {
  depth: number;
  node: ScopeRelatedExists;
  columns: DeepSecTargetColumn[];
  onChange: (node: ScopeRelatedExists) => void;
  owner: string;
  objectName: string;
  budget: GroupProps["budget"];
  path: string;
}) {
  const { user } = useAuth();
  const active = useWorkspaceActive();
  const { data: database } = useDatabaseStatus({ enabled: active });
  const cacheScope = ["nl2sql", "deepsec", user?.user_uuid, database?.context_id];
  const profiles = useQuery({
    retry: false,
    queryKey: [...cacheScope, "scope-profiles"],
    enabled: active,
    queryFn: securityApi.deepSecScopeProfiles,
  });
  const catalog = useQuery({
    retry: false,
    queryKey: [
      ...cacheScope,
      "relations",
      node.profile_id,
      owner,
      objectName,
    ],
    queryFn: () =>
      securityApi.deepSecRelations(node.profile_id, owner, objectName),
    enabled: active && Boolean(node.profile_id),
  });
  const detail = useQuery({
    retry: false,
    queryKey: [
      ...cacheScope,
      "relation-columns",
      node.target_owner,
      node.target_object,
    ],
    queryFn: () =>
      securityApi.deepSecTargetObjectDetail({
        owner: node.target_owner,
        name: node.target_object,
        object_type: "",
        comment: "",
      }),
    enabled: active && Boolean(node.target_object),
  });
  const patch = (change: Partial<ScopeRelatedExists>) =>
    onChange({ ...node, ...change });
  useEffect(() => {
    const type = detail.data?.object_type;
    if (active && (type === "TABLE" || type === "VIEW" || type === "MATERIALIZED VIEW") && type !== node.target_type) {
      onChange({ ...node, target_type: type });
    }
  }, [active, detail.data, node, onChange]);
  const eligible =
    profiles.data?.filter((p) =>
      p.objects.includes(`${owner}.${objectName}`),
    ) ?? [];
  const relatedColumns = detail.data?.columns ?? [];
  const selectedTarget = node.target_object
    ? `${node.target_owner}.${node.target_object}`
    : "";
  const stale = Boolean(
    catalog.data &&
      (catalog.data.object_scope_version !== node.object_scope_version ||
        (selectedTarget && !catalog.data.objects.includes(selectedTarget))),
  );
  return (
    <fieldset
      className="grid min-w-0 gap-3 rounded-md border border-border p-1 sm:p-3"
      data-testid={`scope-related-${path}`}
    >
      <legend className="max-w-full break-words px-1 text-sm font-medium">
        {text("expression.exists")}
      </legend>
      <p className="text-xs text-fg-muted">{text("expression.sameRecord")}</p>
      <p className="text-xs text-fg-muted">
        {text("expression.protectedRelated")}
      </p>
      <QueryStatus
        loading={profiles.isLoading || catalog.isLoading || detail.isLoading}
        error={profiles.error ?? catalog.error ?? detail.error}
        retry={() => {
          void profiles.refetch();
          if (node.profile_id) void catalog.refetch();
          if (node.target_object) void detail.refetch();
        }}
      />
      <Labeled label={text("expression.profile")}>
        <select
          className={inputClass}
          value={node.profile_id}
          onChange={(e) =>
            patch({
              profile_id: e.target.value,
              object_scope_version:
                eligible.find((p) => p.id === e.target.value)
                  ?.object_scope_version ?? 1,
              target_owner: "",
              target_object: "",
              relation_source: "MANUAL",
              relation_id: "",
              relation_version: "",
              join_keys: [{ source_column: "", target_column: "" }],
              condition: {
                kind: "group",
                operator: "AND",
                children: [blankCondition([])],
              },
            })
          }
        >
          <option value="">{text("expression.select")}</option>
          {eligible.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
          {node.profile_id &&
            !eligible.some((p) => p.id === node.profile_id) && (
              <option value={node.profile_id}>
                {text("expression.unavailable")}
              </option>
            )}
        </select>
      </Labeled>
      {!profiles.isLoading && !profiles.error && !eligible.length && (
        <p className="text-sm text-fg-muted">{text("expression.noProfiles")}</p>
      )}
      <Labeled label={text("expression.relatedTable")}>
        <select
          className={inputClass}
          disabled={!catalog.data || catalog.isError}
          value={selectedTarget}
          onChange={(e) => {
            const [target_owner, target_object] = e.target.value.split(".");
            patch({
              target_owner: target_owner ?? "",
              target_object: target_object ?? "",
              object_scope_version: catalog.data!.object_scope_version,
              relation_source: "MANUAL",
              relation_id: "",
              relation_version: "",
              join_keys: [{ source_column: "", target_column: "" }],
              condition: {
                kind: "group",
                operator: "AND",
                children: [blankCondition([])],
              },
            });
          }}
        >
          <option value="">{text("expression.select")}</option>
          {catalog.data?.objects.map((name) => (
            <option key={name}>{name}</option>
          ))}
          {selectedTarget &&
            !catalog.data?.objects.includes(selectedTarget) && (
              <option value={selectedTarget}>
                {selectedTarget} — {text("expression.unavailable")}
              </option>
            )}
        </select>
      </Labeled>
      {stale && <InlineError message="expression.stale" />}
      {node.target_object && (
        <>
          <Labeled label={text("expression.relation")}>
            <select
              className={inputClass}
              value={node.relation_id}
              onChange={(e) => {
                const relation = catalog.data?.relations.find(
                  (r) => r.id === e.target.value,
                );
                patch(
                  relation
                    ? {
                        relation_source: relation.source,
                        relation_id: relation.id,
                        relation_version: relation.version,
                        join_keys: relation.join_keys,
                      }
                    : {
                        relation_source: "MANUAL",
                        relation_id: "",
                        relation_version: "",
                      },
                );
              }}
            >
              <option value="">{text("expression.manual")}</option>
              {catalog.data?.relations
                .filter((r) => r.target === selectedTarget)
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.source} · {r.id}
                  </option>
                ))}
            </select>
          </Labeled>
          {node.join_keys.map((key, index) => (
            <div
              key={index}
              className="grid min-w-0 gap-2 md:grid-cols-[1fr_auto_1fr_auto]"
            >
              <Labeled label={text("expression.sourceKey")}>
                <select
                  className={inputClass}
                  disabled={node.relation_source !== "MANUAL"}
                  value={key.source_column}
                  onChange={(e) =>
                    patch({
                      join_keys: node.join_keys.map((k, i) =>
                        i === index
                          ? { ...k, source_column: e.target.value }
                          : k,
                      ),
                    })
                  }
                >
                  <option value="">{text("expression.select")}</option>
                  {columns
                    .filter((c) => columnValueType(c.data_type))
                    .map((c) => (
                      <option key={c.column_name}>{c.column_name}</option>
                    ))}
                </select>
              </Labeled>
              <span className="self-center" aria-hidden>
                =
              </span>
              <Labeled label={text("expression.targetKey")}>
                <select
                  className={inputClass}
                  disabled={node.relation_source !== "MANUAL"}
                  value={key.target_column}
                  onChange={(e) =>
                    patch({
                      join_keys: node.join_keys.map((k, i) =>
                        i === index
                          ? { ...k, target_column: e.target.value }
                          : k,
                      ),
                    })
                  }
                >
                  <option value="">{text("expression.select")}</option>
                  {relatedColumns
                    .filter((c) => columnValueType(c.data_type))
                    .map((c) => (
                      <option key={c.column_name}>{c.column_name}</option>
                    ))}
                </select>
              </Labeled>
              <Button
                iconOnly
                variant="ghost"
                size="sm"
                type="button"
                disabled={
                  node.relation_source !== "MANUAL" ||
                  node.join_keys.length === 1
                }
                aria-label={text("expression.removeKey")}
                onClick={() =>
                  patch({
                    join_keys: node.join_keys.filter((_, i) => i !== index),
                  })
                } icon={Trash2}>
                </Button>
            </div>
          ))}
          <Button
            variant="secondary"
            size="sm"
            type="button"
            disabled={
              node.relation_source !== "MANUAL" || node.join_keys.length >= 8
            }
            onClick={() =>
              patch({
                join_keys: [
                  ...node.join_keys,
                  { source_column: "", target_column: "" },
                ],
              })
            }
          >
            {text("expression.addKey")}
          </Button>
          <GroupEditor
            group={node.condition}
            columns={relatedColumns}
            onChange={(condition) =>
              patch({
                condition,
                target_type:
                  (detail.data
                    ?.object_type as ScopeRelatedExists["target_type"]) ??
                  node.target_type,
              })
            }
            owner={owner}
            objectName={objectName}
            depth={depth + 1}
            inRelation
            budget={budget}
            path={path}
          />
        </>
      )}
    </fieldset>
  );
}

export function ScopeExpressionEditor({
  expression,
  columns,
  onChange,
  owner,
  objectName,
  disabled,
  index,
}: {
  expression: ScopeExpression;
  columns: DeepSecTargetColumn[];
  onChange: (expression: ScopeExpression) => void;
  owner: string;
  objectName: string;
  disabled: boolean;
  index: number;
}) {
  const id = useId();
  return (
    <fieldset
      disabled={disabled}
      className="grid min-w-0 gap-3"
      aria-describedby={id}
      data-testid={`security-deepsec-scope-filters-${index}`}
    >
      <p id={id} className="text-sm text-fg-muted">
        {text("expression.global")}
      </p>
      <GroupEditor
        group={expression.root}
        columns={columns}
        onChange={(root) => onChange({ version: 1, root })}
        owner={owner}
        objectName={objectName}
        depth={1}
        inRelation={false}
        budget={expressionCounts(expression.root)}
        path={String(index)}
      />
      <InlineError message={expressionError(expression)} />
      <p className="break-words text-sm" data-testid="scope-expression-summary">
        {text("expression.summary")}: {expressionSummary(expression.root, text)}
      </p>
      <p className="text-xs text-fg-muted">{text("expression.union")}</p>
    </fieldset>
  );
}
