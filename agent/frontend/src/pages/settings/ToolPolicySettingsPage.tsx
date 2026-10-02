import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save } from "lucide-react";
import {
  Banner,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  FormActionBar,
  FormStatus,
  FormSkeleton,
  ListSkeleton,
  TimedLoadingState,
  PageHeader,
  StatusBadge,
  toast,
  PageBody,
  SelectField,
  type SelectFieldOption,
} from "@engchina/production-ready-ui";
import { agentApi } from "@/lib/api";
import { QueryState } from "@/components/ListViews";
import { t } from "@/lib/i18n";
import { useValuesChanged } from "@/lib/render-sync";
import { permissionView } from "@/lib/status-labels";
import { sameDraft, useSettingsLeaveGuard } from "@/lib/leave-guard";

type ToolPolicyChoice = "default" | "allow" | "ask" | "deny";

function SettingsSaveBar({
  section,
  onSave,
  saving,
  error,
}: {
  section: string;
  onSave: () => void;
  saving: boolean;
  error: Error | null;
}) {
  return (
    <FormActionBar
      ariaLabel={t("settings.saveActions", { section })}
      primaryActions={[{ id: "save", label: t("common.save"), icon: Save, loading: saving, onClick: onSave }]}
      status={error ? <FormStatus tone="danger" message={t("settings.saveFailed", { reason: error.message })} /> : null}
    />
  );
}

interface ToolPolicyDraft {
  defaultMode: "approval" | "deny";
  policies: Array<[string, ToolPolicyChoice]>;
}

/** 「既定」は未指定と同じ意味なので外し、ツール名で並べて比べる。 */
function toolPolicyDraftOf(
  defaultMode: "approval" | "deny",
  policies: Record<string, ToolPolicyChoice>
): ToolPolicyDraft {
  return {
    defaultMode,
    policies: Object.entries(policies)
      .filter(([, policy]) => policy !== "default")
      .sort(([left], [right]) => left.localeCompare(right)),
  };
}

export function ToolPolicySettingsPage() {
  const queryClient = useQueryClient();
  const tools = useQuery({ queryKey: ["tools"], queryFn: agentApi.listTools });
  const settings = useQuery({
    queryKey: ["settings", "tool-policy"],
    queryFn: agentApi.getToolPolicySettings,
  });
  const mutation = useMutation({
    mutationFn: agentApi.patchToolPolicySettings,
    onSuccess: () => {
      toast.success(t("common.saved"));
      void queryClient.invalidateQueries({ queryKey: ["settings", "tool-policy"] });
    },
  });
  const [defaultMode, setDefaultMode] = useState<"approval" | "deny">("approval");
  const [toolPolicies, setToolPolicies] = useState<Record<string, ToolPolicyChoice>>({});
  const [baseline, setBaseline] = useState<ToolPolicyDraft | null>(null);

  // server 値が変わったレンダーで、フォームと比較の基準を server 値に戻す（effect で setState しない）。
  const serverChanged = useValuesChanged([settings.data]);
  if (serverChanged && settings.data) {
    const current = settings.data;
    const nextPolicies: Record<string, ToolPolicyChoice> = {};
    current.allow.forEach((name) => {
      nextPolicies[name] = "allow";
    });
    current.ask.forEach((name) => {
      nextPolicies[name] = "ask";
    });
    current.deny.forEach((name) => {
      nextPolicies[name] = "deny";
    });
    setDefaultMode(current.default_mode);
    setToolPolicies(nextPolicies);
    setBaseline(toolPolicyDraftOf(current.default_mode, nextPolicies));
  }

  const draft = toolPolicyDraftOf(defaultMode, toolPolicies);
  useSettingsLeaveGuard(baseline !== null && !sameDraft(draft, baseline), mutation.isPending);

  function setPolicy(toolName: string, policy: ToolPolicyChoice) {
    setToolPolicies((current) => ({ ...current, [toolName]: policy }));
  }

  const toolPolicyOptions: SelectFieldOption<ToolPolicyChoice>[] = [
    { value: "default", label: t("settings.toolPolicy.default") },
    { value: "allow", label: t("settings.toolPolicy.allow") },
    { value: "ask", label: t("settings.toolPolicy.ask") },
    { value: "deny", label: t("settings.toolPolicy.deny") },
  ];

  function save() {
    const allow: string[] = [];
    const ask: string[] = [];
    const deny: string[] = [];
    for (const [toolName, policy] of Object.entries(toolPolicies)) {
      if (policy === "allow") {
        allow.push(toolName);
      } else if (policy === "ask") {
        ask.push(toolName);
      } else if (policy === "deny") {
        deny.push(toolName);
      }
    }
    const submitted = draft;
    mutation.mutate(
      {
        default_mode: defaultMode,
        allow: allow.sort(),
        ask: ask.sort(),
        deny: deny.sort(),
      },
      { onSuccess: () => setBaseline(submitted) }
    );
  }

  return (
    <>
      <PageHeader wide title={t("nav.settingsToolPolicy")} subtitle={t("page.settings.toolPolicy.subtitle")} />
      <PageBody wide>
<div className="space-y-5">
        <QueryState query={settings} loadingLabel={t("loading.settings")} skeleton={<FormSkeleton fields={4} />}>
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>{t("nav.settingsToolPolicy")}</CardTitle>
              <CardDescription>{t("page.settings.toolPolicy.subtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <SelectField<"approval" | "deny">
                id="tool-policy-default-mode"
                label={t("settings.toolPolicy.defaultMode")}
                width="md"
                value={defaultMode}
                options={[
                  { value: "approval", label: t("settings.toolPolicy.defaultModeApproval") },
                  { value: "deny", label: t("settings.toolPolicy.defaultModeDeny") },
                ]}
                onValueChange={setDefaultMode}
              />

              {tools.error ? <Banner severity="danger">{tools.error.message}</Banner> : null}
              {tools.isLoading ? (
                <TimedLoadingState label={t("loading.tools")} testId="tool-policy-tools-loading">
                  <ListSkeleton rows={4} rowClassName="h-20" />
                </TimedLoadingState>
              ) : (tools.data?.tools ?? []).length ? (
                <div className="grid gap-3" role="list" aria-label={t("nav.settingsToolPolicy")}>
                  {(tools.data?.tools ?? []).map((tool) => {
                    const policy = toolPolicies[tool.name] ?? "default";
                    return (
                      <div
                        key={tool.name}
                        className="grid min-w-0 gap-3 rounded-md border border-border p-3 md:grid-cols-[minmax(0,1fr)_190px] md:items-center"
                      >
                        <div className="min-w-0 space-y-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <p className="break-all text-sm font-medium text-fg">{tool.name}</p>
                            <StatusBadge {...permissionView(tool.permission_level)} icon={false} />
                            {tool.side_effects ? (
                              <StatusBadge variant="warning" label={t("status.sideEffects")} icon={false} />
                            ) : null}
                          </div>
                          <p className="break-words text-xs leading-5 text-fg-muted [overflow-wrap:anywhere]">
                            {tool.description}
                          </p>
                        </div>
                        <SelectField<ToolPolicyChoice>
                          id={`tool-policy-${tool.name}`}
                          label={t("settings.toolPolicy.policy")}
                          className="min-w-0"
                          value={policy}
                          options={toolPolicyOptions}
                          onValueChange={(value) => setPolicy(tool.name, value)}
                        />
                      </div>
                    );
                  })}
                </div>
              ) : (
                <EmptyState title={t("common.empty.title")} />
              )}

              <SettingsSaveBar
                section={t("nav.settingsToolPolicy")}
                onSave={save}
                saving={mutation.isPending}
                error={mutation.error}
              />
            </CardContent>
          </Card>
        </QueryState>
      </div>
</PageBody>
    </>
  );
}
