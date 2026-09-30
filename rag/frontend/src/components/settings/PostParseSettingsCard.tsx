import {
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Disclosure,
  FormStatus,
  StatusBadge,
  Switch,
} from "@engchina/production-ready-ui";
import { ListChecks, RotateCcw, Save, Sparkles } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { canOpenNavRoute } from "@/components/layout/nav-config";
import { useAuth } from "@/components/security/AuthProvider";
import { ApiError, type ParserAdapterSettingsData } from "@/lib/api";
import { t, type I18nKey } from "@/lib/i18n";
import { useLeaveGuard } from "@/lib/leave-guard";
import { useExtractionFieldsSettings, useUpdateParserAdapterSettings } from "@/lib/queries";
import { APP_ROUTES } from "@/lib/routes";
import { SETTINGS_ANCHORS } from "@/lib/settings-anchors";

import { AnswerPromptPanel } from "./AnswerPromptEditor";
import { ExtractionFieldsEditor } from "./ExtractionFieldsEditor";

type PostParseField = "vision_enabled" | "field_extraction_enabled" | "navigation_summary_enabled";
type PostParseForm = Record<PostParseField, boolean>;

/** 解析の後の処理の順（取込の `_attach_vision` → `_attach_extraction_fields` → `_attach_navigation_tree`）。 */
const POST_PARSE_STEPS: readonly {
  field: PostParseField;
  label: I18nKey;
  hint: I18nKey;
  anchor: string;
}[] = [
  {
    field: "vision_enabled",
    label: "knowledgeBases.adapter.field.vision",
    hint: "settings.parserAdapters.postParse.vision.hint",
    anchor: SETTINGS_ANCHORS.vision,
  },
  {
    field: "field_extraction_enabled",
    label: "knowledgeBases.adapter.field.fieldExtraction",
    hint: "settings.parserAdapters.postParse.fieldExtraction.hint",
    anchor: SETTINGS_ANCHORS.fieldExtraction,
  },
  {
    field: "navigation_summary_enabled",
    label: "knowledgeBases.adapter.field.navigationSummary",
    hint: "settings.parserAdapters.postParse.navigationSummary.hint",
    anchor: SETTINGS_ANCHORS.navigationSummary,
  },
];

function formFromSettings(settings: ParserAdapterSettingsData): PostParseForm {
  return {
    vision_enabled: settings.vision_enabled,
    field_extraction_enabled: settings.field_extraction_enabled,
    navigation_summary_enabled: settings.navigation_summary_enabled,
  };
}

function sameForm(left: PostParseForm, right: PostParseForm) {
  return POST_PARSE_STEPS.every((step) => left[step.field] === right[step.field]);
}

/**
 * 文書解析の「解析後の処理」（#528）。Vision・メタデータ/項目抽出・ナビゲーション要約の全体の既定を、
 * 取込の処理順に並べて保存する（保存先は backend/.env。文書のレシピで上書きできる）。
 * Vision の読み取りプロンプトと、項目抽出の項目の定義も、それぞれの項目の中で編集する。
 */
export function PostParseSettingsCard({ settings }: { settings: ParserAdapterSettingsData }) {
  const save = useUpdateParserAdapterSettings();
  const saved = formFromSettings(settings);
  const [form, setForm] = useState<PostParseForm>(saved);
  // 保存値が変わったレンダーで、未編集なら編集中の値を保存値へそろえる（ほかの節の保存でも data は変わる）。
  const [base, setBase] = useState<PostParseForm>(saved);
  if (!sameForm(base, saved)) {
    setBase(saved);
    if (sameForm(base, form)) setForm(saved);
  }
  const dirty = !sameForm(form, saved);
  useLeaveGuard(dirty);
  const fieldsQuery = useExtractionFieldsSettings();
  const fieldCount = fieldsQuery.data?.fields.length;
  const usesStandardFields = fieldsQuery.data?.uses_standard === true;

  function toggle(field: PostParseField, checked: boolean) {
    save.reset();
    setForm((current) => ({ ...current, [field]: checked }));
  }

  function submit() {
    save.mutate(form, {
      onSuccess: (data) => {
        const next = formFromSettings(data);
        setBase(next);
        setForm(next);
      },
    });
  }

  function reset() {
    save.reset();
    setForm(saved);
  }

  const extras: Record<PostParseField, ReactNode> = {
    vision_enabled: <VisionExtras />,
    field_extraction_enabled: (
      <>
        {form.field_extraction_enabled && fieldCount === 0 ? (
          <FormStatus
            tone="warning"
            className="text-xs"
            message={t("settings.parserAdapters.postParse.fieldExtraction.empty")}
          />
        ) : null}
        <Disclosure
          summary={t("settings.parserAdapters.postParse.fieldExtraction.definitions")}
          icon={ListChecks}
          surface="sunken"
          meta={
            fieldCount !== undefined ? (
              <span className="flex flex-wrap items-center gap-2">
                {/* 一度も保存していない環境は標準の項目を使う（#556）。 */}
                {usesStandardFields ? (
                  <StatusBadge
                    variant="info"
                    label={t("settings.parserAdapters.postParse.fieldExtraction.standard")}
                  />
                ) : null}
                <span className="tnum text-xs text-fg-muted">
                  {t("settings.parserAdapters.postParse.fieldExtraction.count", {
                    count: fieldCount,
                  })}
                </span>
              </span>
            ) : null
          }
        >
          <ExtractionFieldsEditor />
        </Disclosure>
      </>
    ),
    navigation_summary_enabled: null,
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-info-subtle text-info-fg">
            <Sparkles size={20} aria-hidden />
          </div>
          <div>
            <CardTitle>{t("settings.parserAdapters.postParse.title")}</CardTitle>
            <CardDescription>{t("settings.parserAdapters.postParse.description")}</CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <ol className="space-y-3">
          {POST_PARSE_STEPS.map((step, index) => {
            const labelId = `${step.anchor}-label`;
            const hintId = `${step.anchor}-hint`;
            const name = t(step.label);
            return (
              <li
                key={step.field}
                id={step.anchor}
                // hash で移動したときに、上端の固定ヘッダーに隠れないようにする。
                className="scroll-mt-24 space-y-3 rounded-md border border-border bg-surface p-3"
              >
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <h3 id={labelId} className="text-sm font-semibold text-fg">
                      {t("settings.parserAdapters.postParse.step", { index: index + 1, name })}
                    </h3>
                    <p id={hintId} className="mt-1 text-xs leading-relaxed text-fg-muted">
                      {t(step.hint)}
                    </p>
                  </div>
                  <Switch
                    checked={form[step.field]}
                    disabled={save.isPending}
                    aria-label={name}
                    aria-describedby={hintId}
                    onCheckedChange={(checked) => toggle(step.field, checked)}
                  />
                </div>
                {extras[step.field]}
              </li>
            );
          })}
        </ol>
        <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:flex-wrap sm:items-center">
          <Button
            type="button"
            icon={Save}
            loading={save.isPending}
            disabled={!dirty}
            onClick={submit}
            className="w-full sm:w-auto"
          >
            {t("settings.parserAdapters.postParse.save")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            icon={RotateCcw}
            disabled={!dirty || save.isPending}
            onClick={reset}
            className="w-full sm:w-auto"
          >
            {t("settings.parserAdapters.actions.reset")}
          </Button>
          <div className="min-h-6">
            {dirty ? (
              <FormStatus tone="warning" message={t("settings.parserAdapters.actions.unsaved")} />
            ) : null}
            {save.isSuccess && !dirty ? (
              <FormStatus tone="success" message={t("settings.parserAdapters.postParse.saved")} />
            ) : null}
            {save.isError ? (
              <FormStatus
                tone="danger"
                message={
                  save.error instanceof ApiError
                    ? save.error.message
                    : t("settings.parserAdapters.postParse.saveError")
                }
              />
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

/** Vision の項目の補足: 既定の Vision モデルの場所と、図・画像の読み取りプロンプト（全体で 1 つ）。 */
function VisionExtras() {
  const { hasPermission } = useAuth();
  const canOpenModel = canOpenNavRoute(APP_ROUTES.settingsModel, hasPermission);
  return (
    <>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-muted">
        {t("settings.parserAdapters.postParse.vision.model")}
        {canOpenModel ? (
          <Link
            to={APP_ROUTES.settingsModel}
            className="font-medium text-accent-fg underline-offset-2 hover:underline"
          >
            {t("settings.parserAdapters.postParse.vision.openModel")}
          </Link>
        ) : null}
      </p>
      {/* 読み取りの指示は Vision を使うときだけ効く。全体で 1 つなので、既定を無効にしていても編集できる（#497）。 */}
      <Disclosure
        summary={t("settings.parserAdapters.postParse.vision.prompt")}
        description={t("settings.answerPrompts.image_retrieval.description")}
        surface="sunken"
      >
        <AnswerPromptPanel promptKey="image_retrieval" />
      </Disclosure>
    </>
  );
}
