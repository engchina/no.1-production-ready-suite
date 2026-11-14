/** 3製品で共通の安全チェック（OCI Guardrails）の設定の文言（#5061）。 */
export const GUARDRAIL_SETTINGS_MESSAGES = {
  loading: "安全チェックの設定を読み込んでいます",
  loadError: "安全チェックの設定を取得できませんでした。",
  saveError: "安全チェックの設定を保存できませんでした。",
  retry: "再試行",
  ociLabel: "OCI Guardrails",
  ociDescription: "入力と出力を個別に選択できます。選択したテキストだけを OCI Guardrails に送信します。",
  selection: "検査するテキスト（既定は両方とも未選択）",
  input: "入力を検査する",
  output: "出力を検査する",
  inputDescription:
    "質問を OCI Guardrails に送信し、個人情報・プロンプト攻撃・不適切な内容を検査します。未選択のときは、この検査のために質問を OCI Guardrails へ送信しません。",
  outputDescription:
    "回答を OCI Guardrails に送信し、個人情報・プロンプト攻撃・不適切な内容を検査します。未選択のときは、この検査のために回答を OCI Guardrails へ送信しません。",
  resultHandling:
    "OCI の検出結果に基づき、個人情報として返された位置の文字をアプリでマスクします。プロンプト攻撃・不適切な内容を検出したとき、または検査を完了できないときは、処理・回答の表示を停止します。",
  officialDocs: "OCI Guardrails の公式ドキュメント（新しいタブで開きます）",
  overview: "全体の設定です。保存すると次の質問・回答から使います。",
  ociSettingsLink: "OCI 認証設定を開く",
  compartmentMissing: "OCI Guardrails の compartment が未設定です。保存する前に OCI 認証設定を完了してください。",
  credentialsInvalid: "OCI API キー認証を利用できません。config・profile・秘密鍵とファイル権限を確認してください。",
  actionsLabel: "安全チェックの設定の操作",
  save: "保存",
  saved: "安全チェックを保存しました。",
  reset: "変更を破棄",
  unsaved: "未保存の変更があります。",
};
export type GuardrailSettingsMessages = typeof GUARDRAIL_SETTINGS_MESSAGES;
