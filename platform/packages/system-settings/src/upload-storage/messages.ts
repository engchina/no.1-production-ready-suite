/** アップロード保存先画面の既定の文言（NL2SQL の文言。#97）。製品側は `messages` prop で上書きできる。 */
export const UPLOAD_STORAGE_MESSAGES = {
  loading: "アップロード保存先設定を読み込んでいます。",
  loadError: "アップロード保存先設定の取得に失敗しました。",
  saveError: "アップロード保存先設定の保存に失敗しました。",
  retry: "再試行",
  destinationTitle: "保存先",
  destinationDescription: "アップロードされた PDF、画像、テキスト原本をどこへ保管するかを設定します。",
  fieldBackend: "アップロード原本の保存先",
  backendLocal: "ローカルディレクトリ",
  backendLocalDescription: "バックエンドが稼働する環境の PLATFORM_LOCAL_STORAGE_DIR 配下へ保存します。",
  backendOci: "OCI Object Storage",
  backendOciDescription: "OCI 認証設定の namespace と指定 bucket へ保存します。",
  fieldLocalStorageDir: "ローカル保存ディレクトリ",
  fieldObjectStorageRegion: "Object Storage リージョン",
  fieldObjectStorageNamespace: "Object Storage ネームスペース",
  fieldObjectStorageBucket: "Object Storage バケット",
  helperLocalStorageDir: "バックエンドプロセスから作成・書き込みできるディレクトリを指定します。",
  helperObjectStorageRegion:
    "原本ファイルを保存する bucket のリージョン。OCI 認証設定の Object Storage リージョンと合わせます。",
  helperObjectStorageNamespace: "OCI 認証設定で保存済みの Object Storage namespace を使用します。",
  helperObjectStorageBucket:
    "Object Storage ネームスペースは OCI 認証設定の値を使用します。原本ファイルを保存する bucket 名を指定します。",
  regionPlaceholder: "選択してください",
  required: "必須",
  save: "保存",
  saved: "保存しました",
  openOciSettings: "OCI 認証設定を開く",
  ociSettingsIncomplete: "OCI Object Storage を使うには、リージョンとネームスペースの設定が必要です。",
  validationRequired: "値を入力してください。",
  validationLocalStorageDir: "ローカル保存ディレクトリを入力してください。",
  validationObjectStorageRegion: "OCI 認証設定で Object Storage リージョンを選択してください。",
  validationObjectStorageNamespace: "OCI 認証設定で Object Storage ネームスペースを設定してください。",
  validationObjectStorageName: "英数字、ハイフン、アンダースコア、ドットで入力してください。",
};

export type UploadStorageMessages = typeof UPLOAD_STORAGE_MESSAGES;
