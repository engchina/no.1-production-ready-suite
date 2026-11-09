import type {
  ExternalParserBackendName,
  ExternalParserConnectionData,
  ExternalParserConnectionUpdate,
} from "@/lib/api";
import { t } from "@/lib/i18n";

/** 外部 GPU 解析エンジンの接続の編集中の値。API key は入力した値だけを持ち、保存済みの値は持たない。 */
export type ExternalParserConnectionForm = {
  endpoint: string;
  model: string;
  api_key: string;
  clear_api_key: boolean;
};

export type ConnectionFieldErrors = Record<string, string>;

function defaultExternalModel(backend: ExternalParserBackendName) {
  if (backend === "dots_ocr") return "rednote-hilab/dots.mocr";
  return "";
}

export function connectionFormFromSettings(
  backend: ExternalParserBackendName,
  connection: ExternalParserConnectionData | undefined
): ExternalParserConnectionForm {
  return {
    endpoint: connection?.endpoint ?? "",
    model: connection?.model ?? defaultExternalModel(backend),
    api_key: "",
    clear_api_key: false,
  };
}

export function connectionFormIsDirty(
  backend: ExternalParserBackendName,
  form: ExternalParserConnectionForm,
  connection: ExternalParserConnectionData | undefined
): boolean {
  const saved = connectionFormFromSettings(backend, connection);
  return (
    form.endpoint.trim() !== saved.endpoint ||
    form.model.trim() !== saved.model ||
    Boolean(form.api_key) ||
    form.clear_api_key
  );
}

/** 接続だけの更新。API key は入力したときだけ送り、空なら backend が保存済みの値を残す。 */
export function connectionUpdatePayload(
  backend: ExternalParserBackendName,
  form: ExternalParserConnectionForm
): ExternalParserConnectionUpdate {
  return {
    backend,
    endpoint: form.endpoint.trim(),
    model: form.model.trim(),
    ...(form.api_key ? { api_key: form.api_key } : {}),
    ...(form.clear_api_key ? { clear_api_key: true } : {}),
  };
}

/** 欄のエラーを `<backend>.<欄>` の key で返す（欄の id は `external-parser-<backend>-<欄>`）。 */
export function validateConnectionForm(
  backend: ExternalParserBackendName,
  form: ExternalParserConnectionForm
): ConnectionFieldErrors {
  const errors: ConnectionFieldErrors = {};
  const endpoint = form.endpoint.trim();
  if (endpoint) {
    try {
      const parsed = new URL(endpoint);
      if (
        // backend（ExternalParserConnectionUpdate）と同じく `http(s)://host` の形だけを受け付ける。
        // `new URL("http:foo")` は `http://foo/` として通るため、書き方も確かめる（#976）。
        !/^https?:\/\/[^/?#]/i.test(endpoint) ||
        !["http:", "https:"].includes(parsed.protocol) ||
        parsed.username ||
        parsed.password ||
        parsed.search ||
        parsed.hash
      ) {
        errors[`${backend}.endpoint`] = t("settings.parserAdapters.connection.invalidEndpoint");
      }
    } catch {
      errors[`${backend}.endpoint`] = t("settings.parserAdapters.connection.invalidEndpoint");
    }
  }
  if (endpoint && !form.model.trim()) {
    errors[`${backend}.model`] = t("settings.parserAdapters.connection.modelRequired");
  }
  return errors;
}
