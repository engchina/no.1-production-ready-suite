/**
 * 既定のモデル 2 つ（既定のテキストモデル / 既定の Vision モデル）の選択肢と検証（#499）。
 * 検証の規則は backend の `pr_system_settings.model.validate_default_models` と同じ。
 */
import type { SelectFieldOption } from "@engchina/production-ready-ui";

import { t } from "./messages";
import type {
  EnterpriseAiConfiguredModel,
  EnterpriseAiModelSettings,
} from "./types";

export type DefaultModelField =
  "default_text_model_id" | "default_vision_model_id";
export type DefaultModelErrors = Partial<Record<DefaultModelField, string>>;

/** 画面の入力欄の id（保存前の検証で最初の不正な欄へフォーカスする）。 */
export const DEFAULT_MODEL_FIELD_IDS: Record<DefaultModelField, string> = {
  default_text_model_id: "enterprise-default-text-model",
  default_vision_model_id: "enterprise-default-vision-model",
};
/** フォーカスする順（画面の並び順。テキスト → Vision。#566）。 */
export const DEFAULT_MODEL_FIELD_ORDER: readonly DefaultModelField[] = [
  "default_text_model_id",
  "default_vision_model_id",
];

/** モデル ID を入力した行（登録モデル）。前後の空白は backend と同じく除く。 */
export function registeredModels(
  models: readonly EnterpriseAiConfiguredModel[],
): EnterpriseAiConfiguredModel[] {
  return models
    .map((model) => ({ ...model, model_id: model.model_id.trim() }))
    .filter((model) => model.model_id);
}

export function validateDefaultModels(
  enterprise: Pick<
    EnterpriseAiModelSettings,
    "models" | "default_text_model_id" | "default_vision_model_id"
  >,
): DefaultModelErrors {
  const models = registeredModels(enterprise.models);
  const registered = new Set(models.map((model) => model.model_id));
  const visionCapable = new Set(
    models.filter((model) => model.vision_enabled).map((model) => model.model_id),
  );
  const errors: DefaultModelErrors = {};
  const text = enterprise.default_text_model_id.trim();
  if (models.length > 0 && !text) {
    errors.default_text_model_id = t("settings.model.defaults.error.textRequired");
  } else if (text && !registered.has(text)) {
    errors.default_text_model_id = t("settings.model.defaults.error.textRemoved", {
      model: text,
    });
  }
  const vision = enterprise.default_vision_model_id.trim();
  if (models.length > 0 && visionCapable.size === 0) {
    errors.default_vision_model_id = t("settings.model.defaults.error.noVisionModel");
  } else if (models.length > 0 && !vision) {
    errors.default_vision_model_id = t("settings.model.defaults.error.visionRequired");
  } else if (vision && !registered.has(vision)) {
    errors.default_vision_model_id = t("settings.model.defaults.error.visionRemoved", {
      model: vision,
    });
  } else if (vision && !visionCapable.has(vision)) {
    errors.default_vision_model_id = t("settings.model.defaults.error.visionNotCapable", {
      model: vision,
    });
  }
  return errors;
}

function modelOption(model: EnterpriseAiConfiguredModel): SelectFieldOption {
  const displayName = model.display_name.trim();
  return displayName && displayName !== model.model_id
    ? { value: model.model_id, label: displayName, description: model.model_id }
    : { value: model.model_id, label: model.model_id };
}

/** 既定の Vision モデルの選択肢（画像入力に対応した登録モデルだけ）。 */
export function visionModelOptions(
  models: readonly EnterpriseAiConfiguredModel[],
): SelectFieldOption[] {
  return uniqueById(registeredModels(models).filter((model) => model.vision_enabled)).map(
    modelOption,
  );
}

/** 既定のテキストモデルの選択肢（登録モデルすべて。Vision 対応のモデルも選べる）。 */
export function textModelOptions(
  models: readonly EnterpriseAiConfiguredModel[],
): SelectFieldOption[] {
  return uniqueById(registeredModels(models)).map(modelOption);
}

function uniqueById(models: EnterpriseAiConfiguredModel[]) {
  const seen = new Set<string>();
  return models.filter((model) => {
    if (seen.has(model.model_id)) return false;
    seen.add(model.model_id);
    return true;
  });
}

/**
 * 登録モデルの行を変えたときの既定のモデルの追従。
 * - 既定に選んでいたモデルの ID を書き換えたら、既定も新しい ID にする（消し切ったときは除く）
 * - Vision 対応をオンにしたとき、既定の Vision モデルが未選択ならそのモデルを選ぶ
 * - 削除・Vision 対応のオフは既定を変えない（フィールドのエラーで選び直しを案内する）
 */
export function followModelChange(
  enterprise: Pick<
    EnterpriseAiModelSettings,
    "default_text_model_id" | "default_vision_model_id"
  >,
  previous: EnterpriseAiConfiguredModel | undefined,
  next: EnterpriseAiConfiguredModel,
): Pick<EnterpriseAiModelSettings, "default_text_model_id" | "default_vision_model_id"> {
  const previousId = previous?.model_id.trim() ?? "";
  const nextId = next.model_id.trim();
  let text = enterprise.default_text_model_id;
  let vision = enterprise.default_vision_model_id;
  // ID を消し切ったときは追従しない（同じ ID を打ち直せば選択が戻る）。
  if (previousId && nextId && previousId !== nextId) {
    if (text.trim() === previousId) text = nextId;
    if (vision.trim() === previousId) vision = nextId;
  }
  if (next.vision_enabled && !previous?.vision_enabled && !vision.trim() && nextId) {
    vision = nextId;
  }
  return { default_text_model_id: text, default_vision_model_id: vision };
}
