/**
 * モデル設定画面の既定の文言（NL2SQL の文言。#103）。
 * key は NL2SQL の i18n key と同じにして、移設したコードをそのまま使えるようにしている。
 */
export const MODEL_MESSAGES = {
  "common.delete": "削除",
  "settings.model.enterprise.addModel": "追加",
  "settings.model.enterprise.apiKey": "API key",
  "settings.model.enterprise.apiKeyHelp":
    "OpenAI-compatible gateway の Bearer 認証で使います。保存先はサーバーの .env のみで、JSON や API 応答には含めません。",
  "settings.model.enterprise.apiKeyHide": "API key を隠す",
  "settings.model.enterprise.apiKeyNotSet": "未設定",
  "settings.model.enterprise.apiKeySaved": "保存済み",
  "settings.model.enterprise.apiKeyShow": "API key を表示",
  "settings.model.enterprise.clearApiKey": "保存済み API key を削除する",
  "settings.model.enterprise.default": "既定",
  "settings.model.enterprise.description":
    "回答生成と Vision/OCR 解析に使う Enterprise AI の接続情報を設定します。",
  "settings.model.enterprise.displayName": "表示名",
  "settings.model.enterprise.endpoint": "Endpoint URL",
  "settings.model.enterprise.endpointDocs":
    "公式ドキュメント（新しいタブで開く）",
  "settings.model.enterprise.endpointHelp":
    "公式 docs の OpenAI-compatible base URL を指定します。Responses API path は /responses です。",
  "settings.model.enterprise.modelId": "モデル ID",
  "settings.model.enterprise.models": "登録モデル",
  "settings.model.enterprise.modelsDescription":
    "回答生成と Vision/OCR 解析に使用するモデルを登録し、既定モデルを選択します。",
  "settings.model.enterprise.modelsSaved": "登録モデルを保存しました。",
  "settings.model.enterprise.project": "Project OCID",
  "settings.model.enterprise.projectHelp":
    "OCI OpenAI-compatible API 呼び出しに必要な Generative AI project OCID。",
  "settings.model.enterprise.removeConfirm.description":
    "モデル「{model}」を一覧から削除します。保存するまで確定しません。",
  "settings.model.enterprise.removeConfirm.descriptionUnnamed":
    "このモデルを一覧から削除します。保存するまで確定しません。",
  "settings.model.enterprise.removeConfirm.title": "このモデルを削除しますか？",
  "settings.model.enterprise.removeModel": "モデルを削除",
  "settings.model.enterprise.saved":
    "OCI Enterprise AI 接続設定を保存しました。",
  "settings.model.enterprise.title": "OCI Enterprise AI",
  "settings.model.enterprise.vision": "Vision",
  "settings.model.fixed": "固定",
  "settings.model.genai.description":
    "埋め込みとリランクのみ Generative AI の Cohere モデルを使います。",
  "settings.model.genai.embeddingDim": "Embedding 次元",
  "settings.model.genai.embeddingDimHelp":
    "Cohere Embed v4 と Oracle 26ai のベクトル列に合わせます。",
  "settings.model.genai.embeddingModel": "埋め込みモデル ID",
  "settings.model.genai.rerankModel": "リランクモデル ID",
  "settings.model.genai.saved": "OCI Generative AI 設定を保存しました。",
  "settings.model.genai.title": "OCI Generative AI",
  "settings.model.legacySecret.description":
    "原因: 旧形式の model-settings.json に API Key が保存されています。復旧方法: この画面で保存すると API Key を .env へ移し、JSON から secret を削除します。移行後は接続テストを実行してください。",
  "settings.model.legacySecret.title": "旧 JSON に API Key が残っています",
  "settings.model.loadError": "モデル設定の取得に失敗しました。",
  "settings.model.loading": "モデル設定を読み込んでいます。",
  "settings.model.placeholder.apiKey":
    "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "settings.model.placeholder.displayName": "業務 NL2SQL 標準",
  "settings.model.placeholder.embeddingModel": "cohere.embed-v4.0",
  "settings.model.placeholder.endpoint":
    "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/openai/v1",
  "settings.model.placeholder.modelId": "enterprise-llm",
  "settings.model.placeholder.project":
    "ocid1.generativeaiproject.oc1.us-chicago-1.xxxxxxxx",
  "settings.model.placeholder.rerankModel": "cohere.rerank-v4.0-fast",
  "settings.model.requiredInOci": "OCI 運用時必須",
  "settings.model.save": "保存",
  "settings.model.saveError":
    "モデル設定を保存できませんでした。入力内容とバックエンド接続を確認して再試行してください。",
  "settings.model.subtitle":
    "OCI Enterprise AI の LLM カタログと OCI Generative AI（埋め込み/リランク）のモデルを設定します。",
  "settings.model.test.action": "テスト",
  "settings.model.test.apiFailed":
    "モデルテスト API の呼び出しに失敗しました。バックエンドのログとネットワークを確認してください。",
  "settings.model.test.aria": "{model} をテスト",
  "settings.model.test.failed":
    "モデルテストに失敗しました。入力値とバックエンド接続を確認してください。",
} as const;

export type ModelMessageKey = keyof typeof MODEL_MESSAGES;

/** `{name}` を params の値で置き換える。 */
export function t(
  key: ModelMessageKey,
  params?: Record<string, string | number>,
): string {
  let value: string = MODEL_MESSAGES[key] ?? key;
  if (params) {
    for (const [name, replacement] of Object.entries(params)) {
      value = value.replace(
        new RegExp(`\\{${name}\\}`, "g"),
        String(replacement),
      );
    }
  }
  return value;
}
