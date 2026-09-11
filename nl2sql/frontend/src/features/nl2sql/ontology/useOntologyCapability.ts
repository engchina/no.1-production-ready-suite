import { useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  useResetExecutionConsent,
  useWorkspaceState,
} from "@/components/WorkspaceState";
import { ApiError, apiGet, apiPatch, apiPost } from "@/lib/api";
import { t } from "@/lib/i18n";
import { useUnsavedChangesGuard } from "@/lib/useUnsavedChangesGuard";
import { versionLabel } from "./ontologyResultPresentation";
export interface Parameter {
  api_name: string;
  name_ja: string;
  data_type: string;
  required: boolean;
}
export interface Capability {
  definition: {
    id: string;
    kind: string;
    name_ja: string;
    api_name: string;
    description_ja: string;
    expression_sql?: string;
    implementation_key?: string;
    parameters: Parameter[];
  };
  target_parameters: Parameter[];
  status: string;
  reason_ja: string;
  binding: {
    etag: string;
    kind: string;
    expression_sql: string;
    implementation_key: string;
    state_requirements: unknown[];
    reviewed_rules_ja: string;
    enabled: boolean;
  } | null;
}
export interface Catalog {
  release_id: string;
  display_version?: number | null;
  capabilities: Capability[];
  implementations: { functions: string[]; actions: string[] };
}
export interface Preview {
  id: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
  expires_at: string;
}
export interface Outcome {
  status: "succeeded" | "failed" | "unresolved";
  execution?: Execution;
}
export interface PendingAction {
  preview: Preview;
  key: string;
  input: string;
  releaseId: string;
  displayVersion?: number | null;
}
export interface Execution {
  display_version?: number | null;
  message_ja?: string;
  id: string;
  release_id: string;
  at: string;
  status: string;
  result?: unknown;
  before?: Record<string, unknown>;
  after?: Record<string, unknown>;
}

function stateConditions(value: string): string {
  try {
    const normalize = (item: unknown): unknown =>
      Array.isArray(item)
        ? item
            .map(normalize)
            .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
        : item && typeof item === "object"
          ? Object.fromEntries(
              Object.entries(item)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([key, entry]) => [key, normalize(entry)]),
            )
          : item;
    return JSON.stringify(normalize(JSON.parse(value)));
  } catch {
    return value;
  }
}

export function useOntologyCapability({
  profileId,
  profileLabel,
  releaseId,
  displayVersion,
  capability,
  refresh,
  stale,
}: {
  profileId: string;
  profileLabel?: string;
  releaseId: string;
  displayVersion?: number | null;
  capability: Capability;
  implementations: string[];
  refresh: () => Promise<unknown>;
  stale: boolean;
}) {
  const definition = capability.definition;
  const isFunction = definition.kind === "function";
  const prefix = `ontology-capability:${profileId}:${releaseId}:${definition.id}`;
  const endpoint = `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-capabilities/${encodeURIComponent(definition.id)}`;
  const [values, setValues] = useWorkspaceState<Record<string, string>>(
    `${prefix}:parameters`,
    {},
  );
  const [targets, setTargets] = useWorkspaceState<Record<string, string>>(
    `${prefix}:target`,
    {},
  );
  const [kind, setKind] = useWorkspaceState(
    `${prefix}:binding-kind`,
    capability.binding?.kind ?? (isFunction ? "expression" : "property_update"),
  );
  const [sql, setSql] = useWorkspaceState(
    `${prefix}:sql`,
    capability.binding?.expression_sql ?? definition.expression_sql ?? "",
  );
  const [implementation, setImplementation] = useWorkspaceState(
    `${prefix}:implementation`,
    capability.binding?.implementation_key ??
      definition.implementation_key ??
      "",
  );
  const [rules, setRules] = useWorkspaceState(
    `${prefix}:rules`,
    capability.binding?.reviewed_rules_ja ?? "",
  );
  const [states, setStates] = useWorkspaceState(
    `${prefix}:states`,
    JSON.stringify(capability.binding?.state_requirements ?? [], null, 2),
  );
  const [enabled, setEnabled] = useWorkspaceState(
    `${prefix}:enabled`,
    capability.binding?.enabled ?? true,
  );
  const [lastId, setLastId] = useWorkspaceState(`${prefix}:last-id`, "");
  const [lastInput, setLastInput] = useWorkspaceState(
    `${prefix}:last-input`,
    "",
  );
  const [pendingJson, setPendingJson] = useWorkspaceState(
    `ontology-capability:${profileId}:${definition.id}:pending`,
    "",
  );
  const pending: PendingAction | null = pendingJson
    ? (JSON.parse(pendingJson) as PendingAction)
    : null;
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [success, setSuccess] = useState("");
  const [error, setError] = useState("");
  const confirm = useConfirm();
  const configuration = JSON.stringify([
    kind,
    sql,
    implementation,
    rules,
    stateConditions(states),
    enabled,
  ]);
  const savedConfiguration = JSON.stringify([
    capability.binding?.kind ?? (isFunction ? "expression" : "property_update"),
    capability.binding?.expression_sql ?? definition.expression_sql ?? "",
    capability.binding?.implementation_key ??
      definition.implementation_key ??
      "",
    capability.binding?.reviewed_rules_ja ?? "",
    stateConditions(
      JSON.stringify(capability.binding?.state_requirements ?? []),
    ),
    capability.binding?.enabled ?? true,
  ]);
  const configurationBaseline = useRef(savedConfiguration);
  useEffect(() => {
    if (
      configuration === configurationBaseline.current &&
      configuration !== savedConfiguration
    ) {
      const [
        nextKind,
        nextSql,
        nextImplementation,
        nextRules,
        nextStates,
        nextEnabled,
      ] = JSON.parse(savedConfiguration) as [
        string,
        string,
        string,
        string,
        string,
        boolean,
      ];
      setKind(nextKind);
      setSql(nextSql);
      setImplementation(nextImplementation);
      setRules(nextRules);
      setStates(nextStates);
      setEnabled(nextEnabled);
    }
    configurationBaseline.current = savedConfiguration;
  }, [savedConfiguration]);
  useUnsavedChangesGuard(configuration !== savedConfiguration, () =>
    confirm({
      title: t("ontologyUi.leaveTitle"),
      description: t("ontologyUi.leaveHint"),
      confirmLabel: t("ontologyUi.leave"),
      tone: "warning",
    }),
  );

  const consent = useRef(0);
  useEffect(
    () => () => {
      consent.current += 1;
    },
    [],
  );
  const input = JSON.stringify({ values, targets });
  useResetExecutionConsent(
    () => {
      setPreview(null);
      consent.current += 1;
    },
    `${prefix}:${input}:${kind}:${sql}:${rules}:${states}:${implementation}:${enabled}:${capability.binding?.etag ?? ""}`,
  );
  const execution = useQuery({
    queryKey: [
      "nl2sql",
      "profiles",
      "ontology-capability-execution",
      profileId,
      lastId,
    ],
    queryFn: ({ signal }) =>
      apiGet<Execution>(
        `/api/nl2sql/profiles/${encodeURIComponent(profileId)}/ontology-capabilities/executions/${encodeURIComponent(lastId)}`,
        { signal },
      ),
    enabled: !!lastId,
    retry: false,
  });
  const outcome = useQuery({
    queryKey: [
      "nl2sql",
      "profiles",
      "ontology-capability-outcome",
      profileId,
      definition.id,
      pending?.preview.id,
    ],
    queryFn: ({ signal }) =>
      apiGet<Outcome>(
        `${endpoint}/previews/${encodeURIComponent(pending!.preview.id)}/outcome`,
        { signal },
      ),
    enabled: !!pending && !busy,
    retry: false,
  });
  useEffect(() => {
    if (
      pending &&
      !busy &&
      (outcome.data?.status === "succeeded" ||
        outcome.data?.status === "failed") &&
      outcome.data.execution
    ) {
      setLastId(outcome.data.execution.id);
      setLastInput(pending.input);
      setPendingJson("");
      setPreview(null);
      setError(
        outcome.data.status === "failed"
          ? (outcome.data.execution.message_ja ??
              t("ontologyCapability.failed"))
          : "",
      );
    }
  }, [
    busy,
    outcome.data,
    pendingJson,
    setLastId,
    setLastInput,
    setPendingJson,
  ]);
  const convert = (parameters: Parameter[], entries: Record<string, string>) =>
    Object.fromEntries(
      parameters.map((parameter) => {
        const value = entries[parameter.api_name];
        return [
          parameter.api_name,
          value === undefined || value === ""
            ? null
            : ["integer", "number"].includes(parameter.data_type)
              ? Number(value)
              : parameter.data_type === "boolean"
                ? value === "true"
                : parameter.data_type === "object"
                  ? JSON.parse(value)
                  : value,
        ];
      }),
    );
  const run = async (action: "bind" | "invoke" | "preview" | "execute") => {
    if (busy || stale) return;
    if (action === "preview" && pending) return;
    const errors: Record<string, string> = {};
    if (["preview", "invoke"].includes(action)) {
      for (const [group, parameters, entries] of [
        ["values", definition.parameters ?? [], values],
        ["targets", capability.target_parameters ?? [], targets],
      ] as const) {
        for (const parameter of parameters) {
          const value = entries[parameter.api_name] ?? "";
          const key = `${group}:${parameter.api_name}`;
          if (!value.trim()) {
            if (parameter.required) errors[key] = t("ontologyUi.requiredError");
            continue;
          }
          if (
            ["number", "integer"].includes(parameter.data_type) &&
            !Number.isFinite(Number(value))
          )
            errors[key] = t("ontologyUi.numberError");
          else if (
            parameter.data_type === "integer" &&
            !Number.isInteger(Number(value))
          )
            errors[key] = t("ontologyUi.integerError");
          if (parameter.data_type === "object") {
            try {
              const parsed = JSON.parse(value);
              if (
                !parsed ||
                Array.isArray(parsed) ||
                typeof parsed !== "object"
              )
                throw new Error();
            } catch {
              errors[key] = t("ontologyUi.objectError");
            }
          }
        }
      }
    }
    if (action === "bind") {
      try {
        if (!Array.isArray(JSON.parse(states))) throw new Error();
      } catch {
        errors.states = t("ontologyUi.jsonError");
      }
      if (kind === "backend" && !implementation)
        errors.implementation = t("ontologyUi.requiredError");
      if (isFunction && kind !== "backend" && !sql.trim())
        errors.sql = t("ontologyUi.requiredError");
    }
    setFieldErrors(errors);
    if (Object.keys(errors).length) return;
    const operation =
      action === "execute"
        ? (pending ??
          (preview
            ? {
                preview,
                key: crypto.randomUUID(),
                input,
                releaseId,
                displayVersion,
              }
            : null))
        : null;
    if (action === "execute" && !operation) return;
    const current = consent.current;
    if (action === "execute" && pending) {
      const recovered = await outcome.refetch();
      if (
        current !== consent.current ||
        recovered.isError ||
        recovered.data?.status !== "unresolved"
      )
        return;
    }
    if (
      ["bind", "execute", "invoke"].includes(action) &&
      !(await confirm({
        title: t(`ontologyCapability.${action}`),
        description: t("ontologyWorkspace.confirmScope", {
          profile: profileLabel || profileId,
          version: versionLabel(
            operation ? operation.displayVersion : displayVersion,
          ),
        }),
        confirmLabel: t("ontologyWorkspace.confirm"),
        tone: "info",
      }))
    )
      return;
    if (current !== consent.current) return;
    setBusy(action);
    setError("");
    setSuccess("");
    try {
      if (action === "bind") {
        await apiPatch(
          `${endpoint}/binding`,
          {
            release_id: releaseId,
            kind,
            expression_sql: sql,
            implementation_key: implementation,
            max_rows: 100,
            state_requirements: JSON.parse(states),
            reviewed_rules_ja: rules,
            enabled,
          },
          { "If-Match": capability.binding?.etag ?? "*" },
        );
        setPreview(null);
        await refresh();
        setSuccess(t("ontologyUi.saved"));
      } else {
        const body =
          action === "execute"
            ? { preview_id: operation?.preview.id, confirmed: true }
            : {
                release_id: releaseId,
                parameters: convert(definition.parameters ?? [], values),
                target: convert(capability.target_parameters ?? [], targets),
              };
        // サーバーへ送信する前に復旧 ID を同一タブの草稿へ保存する。
        if (operation)
          flushSync(() => setPendingJson(JSON.stringify(operation)));
        const result = await apiPost<Preview | Execution>(
          `${endpoint}/${action}`,
          body,
          {
            headers: {
              "Idempotency-Key": operation?.key ?? crypto.randomUUID(),
            },
          },
        );
        if (action === "preview") {
          if (current === consent.current) setPreview(result as Preview);
        } else {
          const execution = result as Execution;
          setLastId(execution.id);
          setLastInput(operation?.input ?? input);
          setPendingJson("");
          setPreview(null);
          if (execution.status === "failed")
            setError(execution.message_ja ?? t("ontologyCapability.failed"));
        }
      }
    } catch (cause) {
      if (
        action === "execute" &&
        !pending &&
        cause instanceof ApiError &&
        [
          "ACTION_PREVIEW_STALE",
          "OBJECT_VERSION_CHANGED",
          "ACTION_EFFECT_CHANGED",
        ].includes(cause.errorCode ?? "")
      )
        setPendingJson("");
      setPreview(null);
      setError(
        cause instanceof Error ? cause.message : t("ontologyWorkspace.failed"),
      );
    } finally {
      setBusy("");
    }
  };
  return {
    definition,
    isFunction,
    values,
    setValues,
    targets,
    setTargets,
    kind,
    setKind,
    sql,
    setSql,
    implementation,
    setImplementation,
    rules,
    setRules,
    states,
    setStates,
    enabled,
    setEnabled,
    lastId,
    lastInput,
    pending,
    preview,
    setPreview,
    busy,
    error,
    success,
    fieldErrors,
    setFieldErrors,
    prefix,
    input,
    execution,
    outcome,
    run,
  };
}
