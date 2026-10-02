# デプロイメント

## ローカル開発

設定は2つの `.env` に分かれる（#211）。

| ファイル | 内容 | 変数名 |
|---|---|---|
| `platform/.env`（雛形 `platform/.env.example`） | 3製品共通の設定（OCI 認証・アップロード保存先・モデル・データベース）。システム設定画面の保存先。`model-settings.json` も同じディレクトリに置き、3製品で共有する | `PLATFORM_*` |
| `rag/backend/.env`（雛形 `rag/backend/.env.example`） | RAG 固有の設定（認証・文書解析・検索・回答・HuggingFace など） | `RAG_*` |

backend は 環境変数 → `platform/.env` → `backend/.env` の順に読む（環境変数が最優先）。共通 `.env` の場所は
`PLATFORM_ENV_FILE` で上書きできる（既定はリポジトリの `platform/.env`）。旧名（接頭辞のない名前や `HF_TOKEN` /
`HF_ENDPOINT`）は読まない。既存環境は「[既存環境の更新手順（#211）](#既存環境の更新手順211-共通-env-への移行)」で移行する。

```bash
cp ../platform/.env.example ../platform/.env   # リポジトリ root から見て platform/.env（初回だけ）
cd backend
cp .env.example .env
uv sync   # 外部 parser は services/parsers/<name> の独立サービスで動く(backend には載せない)。
          # 単一 adapter をローカルで試す場合のみ per-adapter extra(例: `uv sync --extra docling`)。
uv run uvicorn app.main:app --reload

cd ../frontend
npm ci
BACKEND_URL=http://localhost:8000 npm run dev   # BACKEND_URL 未指定なら /api は proxy せず 404（hermetic）
```

一括起動はリポジトリ root で `./rag/scripts/start-all.sh` を実行する。最初に backend の
`uv sync`（初回は Sudachi 辞書の構築を含む）が完了するまで待ち、その後に API を起動する。
`BACKEND_READY_TIMEOUT_SECONDS`（既定 90 秒）は API の `/api/health` の待機だけに適用され、
依存準備の時間は含まない。準備中も Ctrl+C で子プロセスをまとめて停止できる。

NL2SQL と同じく、起動の判定には DB の readiness ではなくアプリの `/api/health` を使う。
ADB が停止中でも API と画面は起動できる。DB を使う一覧・検索・取込は利用できず、
取込 worker の Oracle 接続エラーは DB 復旧まで出ることがある。起動スクリプトは ADB を起動しない。

企業プロキシを利用する場合は、起動するターミナルで HTTP / HTTPS のプロキシも有効にする
（SSH だけの設定は npm に適用されない）。frontend の起動スクリプトは Ubuntu の
`/etc/ssl/certs/ca-certificates.crt` を `NODE_EXTRA_CA_CERTS` に設定し、OS に登録済みの企業 CA を
npm の証明書検証にも使う。別の CA ファイルが必要なら `NODE_EXTRA_CA_CERTS` を明示する。
指定済みの値は上書きせず、`strict-ssl=false` や `NODE_TLS_REJECT_UNAUTHORIZED=0` は使わない。
ネットワークの規則で公開 npm registry が禁止されている場合は、組織の推奨 registry を
`npm_config_registry` で指定する。内部 registry を VPN で直接利用する構成では、そのホストを
`no_proxy` / `NO_PROXY` に加える。証明書の追加だけではアクセス制限は解消しない。

### 前処理 / parser（uv の venv + systemd。#286）

前処理と parser はサービスごとの uv の venv（`uv sync --locked --no-dev --python 3.12`）で動くネイティブのプロセスで、
`127.0.0.1:<port>` だけで listen する。本番と同じく systemd の unit（`production-ready-rag-<service>.service`）で動かし、
「運用設定 › サービス管理」画面から起動 / 停止する。Docker は使わない。unit の定義は本番の `init_script.sh` と共通
（[`scripts/rag-systemd.sh`](../scripts/rag-systemd.sh)）。

| サービス | ディレクトリ | port |
|---|---|---|
| preprocess-office-to-pdf / pdf-to-page-images / csv-to-json / excel-to-json / url-to-markdown / image-enhance / pii-redact | `services/preprocess/<name>` | 18010〜18016 |
| parser-docling（既定の解析エンジン） | `services/parsers/docling` | 18020 |
| parser-unstructured（既定では配備しない。Docling が扱えない形式を取り込む場合） | `services/parsers/unstructured` | 18022 |
| parser-asr（GPU。開発環境だけ） | `services/parsers/asr` | 18026 |
| parser-oci-genai-vision / parser-oci-document-understanding | `services/parsers/oci_*` | 18027 / 18028 |

backend の URL 設定（`RAG_PARSER_*_SERVICE_URL` / `RAG_PREPROCESS_*_SERVICE_URL`）の既定はこの `127.0.0.1:<port>`。
`backend/.env` に以前の Docker Compose の service 名（`http://parser-docling:8000` など）が残っている場合は `127.0.0.1:<port>` に直す（#356 から読み替えない）。

1. OS のコマンドを入れる（Ubuntu。使うサービスの分だけでよい）。

   ```bash
   sudo apt-get install -y --no-install-recommends \
     libreoffice-core libreoffice-writer libreoffice-impress libreoffice-calc fonts-noto-cjk \
     poppler-utils tesseract-ocr tesseract-ocr-jpn
   ```

2. systemd が PID 1 の環境（WSL2 は `/etc/wsl.conf` の `[boot] systemd=true`）では、unit と sudoers を登録する（`rag/` で実行）。
   venv を作り（docling のモデルと pii_redact の日本語 NER モデルも取得する）、unit を `/etc/systemd/system/` に、
   画面から操作する unit の `systemctl` / `journalctl` だけを現在のユーザーに許可する sudoers を
   `/etc/sudoers.d/production-ready-rag-services` に置き、初めて登録した unit を起動する。

   ```bash
   scripts/rag-services.sh install            # 前処理 7 つと既定の CPU parser（docling）
   scripts/rag-services.sh install --unstructured  # Unstructured（Docling が扱えない形式を取り込む場合）
   scripts/rag-services.sh install --oci      # OCI parser も足す
   scripts/rag-services.sh install --gpu      # GPU parser（ASR。CUDA が要る）
   scripts/rag-services.sh status             # unit の状態
   scripts/rag-services.sh render /tmp/units  # 登録せずに unit と sudoers の中身だけ確かめる
   scripts/rag-services.sh uninstall          # unit と sudoers を消す
   ```

   backend を `RAG_ENVIRONMENT=development`（既定）で起動すると、サービス管理画面の起動 / 停止が有効になる。
   起動は `systemctl enable --now`、停止は `systemctl disable --now` なので、PC を再起動しても最後に操作した状態に戻る。
   `install` を再実行しても、停止したサービスは停止のまま（コードを更新したときは起動中のものだけ再起動する）。

3. systemd が無い環境では、使うサービスだけ前面で起動する（Ctrl+C で停止。サービス管理画面の起動 / 停止は使えないが、
   状態は /health で表示される）。

   ```bash
   scripts/rag-services.sh run parser-docling
   ```

   GPU の無い環境（CI の runner など）で Docling / Unstructured の venv を作るときは、`RAG_SERVICES_TORCH=cpu` を付けると
   CUDA の wheel（torch・torchvision・triton・`nvidia-*` / `cuda-*`。docling で約 2.7GB）を入れず、PyTorch の CPU 版の
   index から lock と同じ版の torch / torchvision を入れる（#366）。未設定なら lock どおりで、本番の `init_script.sh` は変えていない。

   ```bash
   RAG_SERVICES_TORCH=cpu scripts/rag-services.sh sync parser-docling
   RAG_SERVICES_TORCH=cpu scripts/rag-services.sh run parser-docling
   ```

### 既定の解析エンジン（Docling）で扱える形式と、それ以外の形式の取り込み方（#286）

既定の文書解析エンジン **Docling**（`parser-docling`。親子階層の分割に使うレイアウト解析）は **PDF と画像だけ**を解析する。

| 形式 | 既定（Docling）のまま | 取り込み方 |
|---|---|---|
| PDF（`.pdf`）・画像（`.png` / `.jpg` / `.jpeg` / `.webp` / `.bmp` / `.gif`） | 解析できる | そのまま「処理を開始」 |
| Office（`.docx` / `.pptx` / `.xlsx`） | 解析できない | 処理レシピの「ファイル準備」で **Office→PDF** を選ぶ（PDF にしてから Docling で解析）か、「文書解析」で **Unstructured** を選ぶ |
| テキスト・Markdown・CSV / TSV・JSON / JSONL・XML・HTML（`.txt` / `.md` / `.csv` / `.tsv` / `.json` / `.jsonl` / `.xml` / `.html` など） | 解析できない | 処理レシピの「文書解析」で **Unstructured** を選ぶ |
| メール（`.eml`） | 解析できない | 処理レシピの「文書解析」で **Unstructured** を選ぶ |
| TIFF・旧 Office（`.doc` / `.ppt` / `.xls`）・Outlook（`.msg`）・音声・不明な形式 | どの解析エンジンでも取り込めない（以前から「非対応形式」として止まる） | PDF などに変換してからアップロードする |

- Docling で解析できない形式は、**取込を始める前に止まる**。アップロードの結果に「このままでは取込を開始できません」と
  理由・対処を出し、処理レシピの「処理を開始」（文書一覧の一括取込を含む）は HTTP 409 で理由・対処を返す。自動で
  Unstructured へ振り分けない。判定は backend の `app/rag/parser_source_guard.py` の 1 か所で、ファイル準備が
  passthrough 以外（Office→PDF など形式が変わる）のときは変換後の形式で取込時に判定する。
- 処理レシピで Unstructured を選んだ文書は止めない。Unstructured の解析サービス（`parser-unstructured`）は**既定では配備しない**。
  - 本番: Terraform の stack で入力 `rag_enable_parser_unstructured` を有効にして再配備し、「運用設定 › サービス管理」で起動する。
  - 開発: `scripts/rag-services.sh install parser-unstructured`（または `--unstructured`）で登録する。
  - サービスが未配備・停止中のときは、止めたときの案内にその旨を添える。
- 全体の既定を Unstructured にする場合は「検索・回答設定 › 文書解析」で選んで保存する（`RAG_PARSER_ADAPTER_BACKEND=unstructured`）。

自前のコードの `docker-compose.yml` / `docker-compose.dev.yml` と Dockerfile は #356 で削除した。以前の Docker の環境からの移行は
「[既存環境の更新手順（#286）](#既存環境の更新手順286-docker-compose-からネイティブ配備への移行)」を参照する。

### ローカル保存ディレクトリ(`PLATFORM_LOCAL_STORAGE_DIR`)

`PLATFORM_UPLOAD_STORAGE_BACKEND=local` の原本は `PLATFORM_LOCAL_STORAGE_DIR/objects/` に保存される。既定値は製品ごとに `/u01/data/<製品名>` で統一している(RAG: `/u01/data/production-ready-rag`、NL2SQL: `/u01/data/production-ready-nl2sql`、Agent: `/u01/data/production-ready-agent`)。

#### 旧既定値 `/u01/production-ready-rag` からの移行

以前の既定値は `/u01/production-ready-rag` だった。backend は**ファイルを自動で移動しない**。

- 共通 `.env`（`platform/.env`）などで `PLATFORM_LOCAL_STORAGE_DIR` を明示設定している環境は、そのまま従来のディレクトリを使い続ける。
- 以前の Docker Compose の配備の named volume `backend-local-storage` の中身は、#286 の移行で `/u01/data/production-ready-rag` へ写す(`model-settings.json` は #211 以降 `platform/` に置く)。
- `PLATFORM_LOCAL_STORAGE_DIR` 未設定(既定値)で起動し、旧ディレクトリにデータが残っている場合は、起動時に警告ログ `legacy_local_storage_dir_detected` を出す。旧ディレクトリが新ディレクトリへの symlink になっていれば警告は出ない。

旧ディレクトリの原本を引き続き参照するには、次のいずれかを行う(NL2SQL の `init_script.sh` と同じく、コピー後に旧ディレクトリを退避して symlink を張る)。

```bash
# 1) 旧ディレクトリを使い続ける
#    platform/.env に PLATFORM_LOCAL_STORAGE_DIR=/u01/production-ready-rag を設定して再起動する

# 2) 新ディレクトリへ移行する(backend を停止してから実行)
OLD=/u01/production-ready-rag
NEW=/u01/data/production-ready-rag
sudo install -d -m 0755 -o "$(id -un)" -g "$(id -gn)" "${NEW}"
cp -an "${OLD}/." "${NEW}/"   # -n: 新ディレクトリの既存ファイルは上書きしない
mv "${OLD}" "${OLD}.legacy-$(date +%Y%m%d%H%M%S)"
ln -s "${NEW}" "${OLD}"
```

移行後に backend を起動し、警告ログが出ないこと、既存文書のプレビュー / 再取込ができることを確認してから退避ディレクトリを削除する。
readiness の確認は `/api/ready` を使う。`oci_common`、`enterprise_ai`、`genai`、`oracle`、`object_storage` の設定グループを確認する。
`RAG_ENVIRONMENT=production` では `audit_context_salt` も確認し、`RAG_AUDIT_CONTEXT_HASH_SALT` を必須にする。すべて `ok` のときだけ 200 になり、`missing`、`invalid`、`missing_credentials`、`wallet_not_found` が含まれる場合は 503 になる。

## 既存環境の更新手順の共通の注意（データを削除する migration。#619）

システムテーブルの migration には、テーブルの DROP や行の DELETE で**データを削除するもの**がある（例: #596 の `20260930_005_retire_standard_engine_objects`）。削除したデータは元に戻せないため、次のようにしている。

- **更新は承認が無ければ止まる。** 未適用の migration にデータを削除するものがあると、`system_schema_cli initialize`・画面の「システム設定 > データベース > RAG システムテーブル」の「作成・更新」・`init_script.sh` の再実行は、DB を何も変えずに止まる（error code `SCHEMA_DESTRUCTIVE_MIGRATIONS_CONFIRMATION_REQUIRED`）。一覧と、削除されるデータ・前にすることの説明は `system_schema_cli status` の `pending_destructive_migrations` と、画面の警告に出る。削除しない migration だけが未適用のときは、今までどおり承認なしで更新する。未初期化の DB の初期作成（migration を実行せずに記録だけする）と、確認語で承認する全再作成も対象外。
- **テストは当てない。** 実 Oracle のテスト（`tests/conftest.py` の `oracle_db`）は、テストの開始時に未適用の migration を当てるが、データを削除する migration が未適用なら何も当てずに実 Oracle のテストを skip し、理由を出す。実 Oracle のテストは、共有の開発 DB ではなくテスト専用の schema で流すことを推奨する（`rag/AGENTS.md` の「テスト/検証方針」）。

データを削除する migration を当てる手順:

1. `system_schema_cli status` の `pending_destructive_migrations` で、削除されるデータを確かめる。
2. 残すデータを書き出す（各更新手順の書き出し。例: #596 は `app.rag.legacy_export`）。ADB の自動バックアップが有効で、保持期間の中にあることも確かめる（OCI コンソールの Autonomous AI Database の「バックアップ」）。手元の Oracle（コンテナなど）はバックアップが無いので、必要なら先に Data Pump などで書き出す。
3. 承認して更新する。CLI は `--allow-destructive` を付ける。画面は「作成・更新」の確認ダイアログで「削除して更新」を押す。

   ```bash
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.system_schema_cli initialize --allow-destructive
   ```

`init_script.sh` は承認を付けずに `initialize` を実行する。データを削除する migration が未適用なら、WARNING を出して配備を続ける（backend は起動する）ので、上の手順で書き出してから適用する。

### 書き出す前に消えたデータを、DB のバックアップから取り出す

削除した表は `PURGE` のため recyclebin にも無く、`FLASHBACK TABLE` では戻せない。ADB（Oracle Autonomous AI Database）なら、自動バックアップから過去の時点の**別の DB（クローン）**を作り、そこから必要な表だけを取り出す。元の DB をその時点へ戻す復元（in-place のリストア）は、3 製品が共有する schema 全体とその後の更新を巻き戻すので使わない。

1. 消えた時刻を確かめる。migration の適用時刻は `SELECT migration_name, applied_at FROM rag_schema_migrations WHERE migration_name = '<migration の名前>'` で分かる。
2. OCI コンソールで対象の Autonomous AI Database を開き、「クローンの作成」で「バックアップからのクローン」→「特定の時点」を選び、1. の時刻の少し前を指定する（自動バックアップの保持期間の中の時点だけ選べる）。クローンは別の料金がかかる。
3. クローンの Wallet を取得し、クローンに接続して表を書き出す。RAG の書き出しの CLI は接続先を環境変数で変えられる（例: クローン用の共通 `.env` を用意し、`PLATFORM_ENV_FILE=<そのファイル> uv run python -m app.rag.legacy_export --table rag_agent_memories --output <出力先>`）。必要な行を元の DB へ戻す場合は、戻す先の表（無くなった表は戻さない）と取り込み方を決めてから行う。
4. 取り出しが終わったらクローンを終了（削除）する。

## 既存環境の更新手順（#635 RAG 検索の業務ビューを 1 つにする）

- **画面**: RAG 検索の「対象の業務ビュー」を 1 つだけ選ぶ欄にし、チャットの業務ビューの欄と同じ部品・文言・幅にした。以前の画面で複数を選んでいた作業状態（sessionStorage の `search.businessViewIds`）は読まず、再読込後は未選択から始まる。
- **API**: `POST /api/search`・`POST /api/search/stream` は業務ビューを `business_view_id`（1 つ）だけで受ける。`business_view_ids` を送ると 422 を返す（読み捨てると業務ビューの外を検索するため）。検索の絞り込みの候補 `GET /api/search/extraction-fields` の query も `business_view_ids` から `business_view_id` に変えた。MCP の `rag_search`（当時あったチャットの送信のツールは #787 で削除）と検証 CLI（`app.rag.answer_verify_cli`）は元から 1 つで、入力は変わらない。
- **データベース**: DDL・データの移行は要らない。回答の記録・質問の履歴は元から業務ビューを 1 つだけ保存している。以前に複数の業務ビューで検索した監査（`rag_search_audit` の `business_view_applied`）は `bv-1,bv-2` の文字列のまま残り、表示・集計は変わらない。
- **手順**: コードを更新して backend と frontend を再起動する。`/api/search` を直接呼ぶ独自のスクリプトがあれば、`business_view_ids: [id]` を `business_view_id: id` に変える。

## 既存環境の更新手順（#599 rag_poc から移したときの名前の改名）

#599 で、rag_poc から移したときの名前（`docrag`）を、コードの識別子・設定（環境変数）・API・DB の名前から外し、RAG の標準の名前にした。回答・分割の挙動は変わらない。旧名との互換は持たない（旧名の環境変数は読まず、保存値と表は migration で書き換える）。#599 の migration はデータを削除しないので、承認（`--allow-destructive`）は要らない（#621 の `20260930_007_retire_graph_claims_community` などデータを削除する migration が未適用なら、そちらの承認が要る。下の「既存環境の更新手順（#621）」と上の「共通の注意」）。

### 名前の対照

**backend の環境変数**（`backend/.env`。検索・回答設定の画面から保存した値もここにある）。旧名の行は読まないので、改名しないと既定値に戻る。

| 旧名 | 新しい名前 |
|---|---|
| `RAG_DOCRAG_QUERY_STRATEGY` | `RAG_QUERY_STRATEGY` |
| `RAG_DOCRAG_ANSWER_FLOW` | `RAG_ANSWER_FLOW` |
| `RAG_DOCRAG_NEIGHBOR_CHILD_COUNT` | `RAG_NEIGHBOR_CHILD_COUNT` |
| `RAG_DOCRAG_RERANK_ENABLED` | `RAG_RERANK_ENABLED` |
| `RAG_DOCRAG_SCREEN_LINKING_ENABLED` | `RAG_SCREEN_LINKING_ENABLED` |
| `RAG_DOCRAG_ANSWER_VISION_ENABLED` | `RAG_ANSWER_VISION_ENABLED` |
| `RAG_DOCRAG_HISTORY_REWRITE_ENABLED` | `RAG_HISTORY_REWRITE_ENABLED` |
| `RAG_DOCRAG_PROFILE` | `RAG_ANSWER_PROFILE` |
| `RAG_DOCRAG_CHILD_TARGET_CHARS` | `RAG_CHUNK_CHILD_TARGET_CHARS` |
| `RAG_DOCRAG_TABLE_CHILD_TARGET_CHARS` | `RAG_CHUNK_TABLE_CHILD_TARGET_CHARS` |
| `RAG_DOCRAG_PARENT_TARGET_CHARS` | `RAG_CHUNK_PARENT_TARGET_CHARS` |
| `RAG_DOCRAG_PARENT_MAX_PAGES` | `RAG_CHUNK_PARENT_MAX_PAGES` |
| `RAG_DOCRAG_PARENT_MAX_CHILDREN` | `RAG_CHUNK_PARENT_MAX_CHILDREN` |

**process の環境変数**（`rag_engine` の package が読む。`backend/.env` ではなく、systemd の unit の `Environment=` か shell の `export` で渡すもの）: `DOCRAG_*` は `RAG_ENGINE_*` にした（例: `DOCRAG_DOMAIN_PROFILE_FILE` → `RAG_ENGINE_DOMAIN_PROFILE_FILE`、docling サービスの `DOCRAG_RENDER_DPI` → `RAG_ENGINE_RENDER_DPI`・`DOCRAG_OUTPUT_DIR` → `RAG_ENGINE_OUTPUT_DIR`）。

**コード・package**: `rag/packages/docrag_core`（distribution `docrag-core`、import `docrag`）は `rag/packages/rag_engine`（distribution `rag-engine`、import `rag_engine`）。backend の `app/rag/docrag_answer.py` → `answer_engine.py`、`docrag_chunking.py` → `chunking_small_to_big.py`、`docrag_prompts.py` → `answer_prompts.py`、`docrag_verify_cli.py` → `answer_verify_cli.py`（`python -m app.rag.answer_verify_cli`）。CI の job `RAG / DocRAG core` は `RAG / Engine`。

**保存値・API**:

| 対象 | 旧名 | 新しい名前 |
|---|---|---|
| 分割方式（`RAG_CHUNKING_STRATEGY`・文書 / レシピの処理設定・KB の構築設定） | `docrag_small_to_big` | `small_to_big` |
| 分割のパラメータ（処理設定・文書分割の設定の API） | `docrag_child_target_chars` など 5 項目 | `chunk_child_target_chars` など（`docrag_` を `chunk_` に） |
| 業務ビューの検索・回答設定（`view_config.query`） | `docrag_query_strategy`・`docrag_answer_flow`・`docrag_neighbor_child_count`・`docrag_rerank_enabled`・`docrag_screen_linking_enabled` | `query_strategy`・`answer_flow`・`neighbor_child_count`・`rerank_enabled`・`screen_linking_enabled` |
| 回答生成のプロンプトの表・API | `rag_docrag_prompts`・`/api/settings/docrag-prompts` | `rag_answer_prompts`・`/api/settings/answer-prompts` |
| 検索の応答の `diagnostics` | `retrieval_strategy: "docrag"`・`retrieval_strategy_adapter: "docrag_grounded"` / `"docrag_retrieval_only"`・`docrag`（回答フローの診断） | `"hybrid"`・`"grounded"` / `"retrieval_only"`・`answer` |
| 回答の記録の詳細の API・チャットの SSE の `metadata` | `docrag` | `answer_diagnostics` |
| 回答の記録の回答の方式（`rag_answer_records.answer_engine`） | `docrag` | `grounded` |
| 進捗の stage・metrics の `stage` label・監査の `error_stage` | `docrag_answer`・`docrag_history_rewrite` | `answer`・`history_rewrite` |
| 親子階層の chunk の metadata | `docrag_parent_text`・`docrag_metadata_json`・`docrag_search_text`・`docrag_chunk_id`・`docrag_chunk_seq`・`docrag_source_record_refs_json`・`docrag_source_seq_ranges_json`・`docrag_first_page_context_json`、`source_parser: docling_docrag`、`chunk_group_kind: docrag_parent` | `parent_text`・`engine_metadata_json`・`engine_search_text`・`engine_chunk_id`・`engine_chunk_seq`・`source_record_refs_json`・`source_seq_ranges_json`・`first_page_context_json`、`docling_layout`、`small_to_big_parent` |
| Docling の解析結果（`parser_artifacts`）・分割の縮退の理由 | `docrag_layout`・`docrag_layout_missing` | `layout_records`・`layout_missing` |
| 派生情報レイヤーの指紋の入力 | `docrag_chunk_contract` | `chunk_metadata_contract` |

`chunk_set_id` は改名の前の名前で hash するので変わらない（既存の Chunk・embedding をそのまま使う。作り直しは要らない）。検索の設定の fingerprint（`config_fingerprint`）は入力の名前が変わるので、更新の前と後で値が変わる。Prometheus のダッシュボード・アラートで `stage="docrag_answer"` などを使っている場合は、新しい名前に直す。

### システムテーブルの更新がすること

- migration `20260930_008_answer_prompts_table`: 回答生成のプロンプトの表を `rag_docrag_prompts` から `rag_answer_prompts` へ移す。新しい表が無ければ `ALTER TABLE ... RENAME TO` で改名し、ある（システムテーブルの更新は migration の前に新しい表を作る）ときは旧表の行を新しい表へ写す。写した後、旧表は退役したオブジェクトとして消える（行は新しい表に残る）。主キーの制約と index はシステムの名前（`SYS_C...`）なので改名しない。
- migration `20260930_009_stored_engine_names`: 上の表の保存値を書き換える。対象は、文書・レシピの処理設定と解析結果、KB の構築設定、業務ビューの設定、取込ジョブの上書き、chunk set・抽出・派生情報レイヤーの記録、chunk の metadata、回答の記録、会話・フィードバックの引用、品質評価の入力と結果の JSON の列と、回答の記録の `answer_engine`・検索の監査の `error_stage`。旧名を含む行だけを更新し（冪等）、行は消さない。chunk が多い環境（`rag_chunks` の親子階層の chunk はすべて更新する）では時間がかかり、undo を使う。
- migration `20261001_001_document_sections`: 人が修正した文書の章節（章節ナビゲーション）の表 `rag_document_sections` を足す（#713）。文書ごとに 1 行で、文書を消すと一緒に消える。表を足すだけで既存の行は変えない。修正の後に新しく抽出された章節は表示のときに自動で足し（「新しく抽出」の印。保存すると外れる）、人が消した抽出の章節は `sections_json` の `removed_source_ids` に覚えて足し直さない（#721。保存の形は `{"sections": [...], "removed_source_ids": [...]}`、以前の配列の形もそのまま読む）。チャットの確認で選んだ章節は、回答のときに章節 ID で今のページを読み直す。
- 章節の抽出規則（#715）: 全体の既定は `backend/section-rules.json`（`RAG_SECTION_RULES_FILE` で場所を変えられる。抽出項目の定義と同じ形）。ファイルが無ければ「解析エンジンの見出し」（これまでどおり）。規則は章節を表示するたびに保存済みの抽出結果へ当てるので、取込のやり直しも migration も要らない。処理レシピの「章節の抽出規則」で文書ごとに方式を上書きできる。

### 手順

1. #599 の版のコードを取得する（`git pull` など）。backend・取込 worker・前処理 / parser は止める（旧 package の venv のままでは起動できない。保存値の書き換えの最中に取込が書き込まないようにする）。

   ```bash
   sudo systemctl stop 'production-ready-rag-*'
   ```

2. `backend/.env` の旧名の行を改名し、値の旧名（`RAG_CHUNKING_STRATEGY=docrag_small_to_big`）も書き換える（バックアップを取ってから編集する。値が旧名のままだと backend が起動時の検証で止まる。#624）。Compute では、`init_script.sh` が Resource Manager の入力（`/u01/aipoc/props/backend.env`）から `backend/.env` を作り直すので、両方を直す。

   ```bash
   for env_file in /u01/aipoc/no.1-production-ready-suite/rag/backend/.env /u01/aipoc/props/backend.env; do
     [ -f "${env_file}" ] || continue
     sudo cp -p "${env_file}" "${env_file}.bak-599"
     sudo sed -i -E \
       -e 's/^RAG_DOCRAG_(CHILD_TARGET_CHARS|TABLE_CHILD_TARGET_CHARS|PARENT_TARGET_CHARS|PARENT_MAX_PAGES|PARENT_MAX_CHILDREN)=/RAG_CHUNK_\1=/' \
       -e 's/^RAG_DOCRAG_PROFILE=/RAG_ANSWER_PROFILE=/' \
       -e 's/^RAG_DOCRAG_(QUERY_STRATEGY|ANSWER_FLOW|NEIGHBOR_CHILD_COUNT|RERANK_ENABLED|SCREEN_LINKING_ENABLED|ANSWER_VISION_ENABLED|HISTORY_REWRITE_ENABLED)=/RAG_\1=/' \
       -e 's/^RAG_CHUNKING_STRATEGY=docrag_small_to_big$/RAG_CHUNKING_STRATEGY=small_to_big/' \
       "${env_file}"
     sudo grep -ni 'docrag' "${env_file}" || echo "OK: ${env_file}"
   done
   ```

   ローカルは `rag/backend/.env` だけを同じ `sed` で直す。
3. process の環境変数 `DOCRAG_*` を `RAG_ENGINE_*` に直す。systemd の unit の drop-in（`systemctl edit production-ready-rag-backend.service` などで足した `Environment=DOCRAG_DOMAIN_PROFILE_FILE=...`）や、起動する shell の `export` を探して改名する。docling サービスの unit の `Environment=RAG_ENGINE_OUTPUT_DIR=...` は `scripts/rag-systemd.sh` が書くので、手順 4 の `init_script.sh` の再実行（ローカルは `scripts/rag-services.sh install`）で作り直す。

   ```bash
   sudo grep -rn 'DOCRAG_' /etc/systemd/system/production-ready-rag-* 2>/dev/null
   ```

4. venv を作り直す（package の名前が変わったので、旧 `docrag-core` を外して `rag-engine` を入れる）。Compute では `init_script.sh` を再実行すれば、venv・unit・frontend の build・システムテーブルの更新（手順 5）までを行う。手で行う場合は、リポジトリの所有者で実行する。

   ```bash
   # OCI Compute（手で行う場合）
   cd /u01/aipoc/no.1-production-ready-suite/rag
   sudo -u ubuntu bash -c 'cd backend && uv sync --locked --no-dev --python 3.12'
   sudo -u ubuntu bash -c 'cd services/parsers/docling && uv sync --locked --no-dev --python 3.12'

   # ローカル
   cd rag/backend && uv sync --locked
   cd ../services/parsers/docling && uv sync --locked
   ```

5. システムテーブルを更新する（#599 の migration だけが未適用なら承認は要らない）。画面の「システム設定 > データベース > RAG システムテーブル」の「作成・更新」でもよい。

   ```bash
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.system_schema_cli initialize
   ```

6. backend・worker・前処理 / parser と frontend（`npm run build` の成果物）を起動し直す（`sudo systemctl start 'production-ready-rag-*'`。`init_script.sh` を再実行した場合は起動済み）。
7. 確かめる。
   - `system_schema_cli status` が `ready` で、`retired_objects` と `pending_versions` が空。
   - 保存値に旧名が残っていない（0 件）。

     ```sql
     SELECT COUNT(*) FROM rag_chunks WHERE JSON_SERIALIZE(metadata_json RETURNING CLOB) LIKE '%docrag%';
     SELECT COUNT(*) FROM rag_business_views WHERE JSON_SERIALIZE(view_config RETURNING CLOB) LIKE '%docrag%';
     SELECT COUNT(*) FROM rag_answer_prompts;  -- 編集したプロンプトの件数が更新の前と同じ
     ```

   - 検索・回答設定の「検索方法」「文書分割」と業務ビューの「検索・回答設定」に、更新の前の値が出る。RAG 検索で回答し、「回答の根拠と実行記録」が出る。

## 既存環境の更新手順（#621 関係情報の構築の選択肢を 2 つにする）

#621 で、設定の「関係情報の構築」の選択肢を「構築しない」（`off`。既定）と「構築する」（`entities`）の 2 つにした。構築するのは、文書全体と章・節の見出し（表・図を含む）の「含む」のつながりで、LLM は使わない。ナレッジベースの「関係情報グラフ」で見るためのもので、回答の検索には使わない（#595）。

- **`full` を削除した。** `full` が追加で作っていた claims（`rag_graph_claims`）と community summary（`rag_graph_community_summaries`）は、どの画面・API・検索も読んでいなかった。`full` と `entities` が作る関係情報グラフの中身（`rag_graph_entities`・`rag_graph_relationships`・`rag_graph_entity_chunks`）は同じ。
- **legacy の `RAG_GRAPH_ENABLED` を削除した。** 読まない（`true` でも `full` に読み替えない）。設定画面の保存も書かない。
- **backend/.env の `RAG_GRAPH_PROFILE=full` は起動を止める。** 読み替えず、書き換え先（`entities` か `off`）を示すエラーで止まる。
- **保存済みの上書きの `full` は、migration `20260930_006_graph_profile_entities` が `entities` に書き換える**（文書・文書レシピの `processing_config`、ナレッジベースの `retrieval_config.ingestion`、取込ジョブの `settings_overrides.processing_config`、chunk_set に刻んだレシピ `recipe_subset`）。データを削除しない migration。
- **migration `20260930_007_retire_graph_claims_community` が `rag_graph_claims`・`rag_graph_community_summaries` を削除する。** データを削除する migration なので、承認（CLI の `--allow-destructive`・画面の確認ダイアログ）が要る（上の「既存環境の更新手順の共通の注意」）。どちらも文書から作り直せる派生データで、読む経路が無かったため、書き出しは要らない。
- **旧値との互換は持たない（#629）。** 保存値に `full` が残っていると、ほかの不正な値と同じ扱いになる（文書・文書レシピの処理設定などは読み込みが検証エラーになり、ナレッジベースの構築設定は警告を出して空の設定（すべて既定を継承）に戻る）。旧 `full` で構築した文書に残る `rag_graph_claims` の行も消さないので、表が残ったままだと再取込・削除が FK で失敗する。**新しい版の backend と取込 worker を起動する前に、2 つの migration（006・007）を当てる。** 2 つは同時に未適用になり、007 の承認が無いとどちらも当たらない。

### 手順

1. #621 の版のコードを取得する（`git pull` など）。backend・取込 worker・前処理 / parser は止める（migration の前に新しい版が保存値 `full` を読むと検証エラーになり、取込が関係情報の表に書き込まないようにする）。

   ```bash
   sudo systemctl stop 'production-ready-rag-*'
   ```

2. `backend/.env` に `RAG_GRAPH_PROFILE=full` があれば `RAG_GRAPH_PROFILE=entities` に書き換える（構築しない場合は `off`。`full` のままだと起動時の検証で止まる）。`RAG_GRAPH_ENABLED` の行があれば消す（残っても読まないが、`true` だった環境は、以前は `full` で構築していた。同じものを作り続けるなら `RAG_GRAPH_PROFILE=entities` にする）。Compute では、`init_script.sh` が Resource Manager の入力（`/u01/aipoc/props/backend.env`）から `backend/.env` を作り直すので、両方を直す。
3. backend の venv を新しい版に合わせる（リポジトリの所有者で実行する。`init_script.sh` は、システムテーブルの更新の後の手順 5 で再実行する）。

   ```bash
   # OCI Compute
   cd /u01/aipoc/no.1-production-ready-suite/rag
   sudo -u ubuntu bash -c 'cd backend && uv sync --locked --no-dev --python 3.12'

   # ローカル
   cd rag/backend && uv sync --locked
   ```

4. サービスを止めたまま、システムテーブルを承認付きで更新する（006 と 007 を当てる）。

   ```bash
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.system_schema_cli initialize --allow-destructive
   ```

   画面の「システム設定 > データベース > RAG システムテーブル」の「作成・更新」の確認ダイアログ（「削除して更新」）で行う場合は、backend だけを起動し（取込 worker と前処理 / parser は止めたまま）、ほかの操作をせずに更新する。当てるまでは、保存値に `full` が残る設定が上のとおり読めない（この間にナレッジベースの構築設定を画面から保存すると、既定に戻った設定のまま保存される）。
5. backend・worker・前処理 / parser と frontend を起動し直す（`sudo systemctl start 'production-ready-rag-*'`。Compute では `init_script.sh` の再実行でもよい。migration は当たっているので、承認なしの更新で止まらない）。
6. 確かめる。
   - `system_schema_cli status` が `ready` で、`retired_objects`・`pending_versions`・`pending_destructive_migrations` が空。
   - 保存値に `full` が残っていない（0 件）。

     ```sql
     SELECT COUNT(*) FROM rag_documents WHERE JSON_VALUE(processing_config, '$.graph_profile') = 'full';
     SELECT COUNT(*) FROM rag_document_recipes WHERE JSON_VALUE(processing_config, '$.graph_profile') = 'full';
     SELECT COUNT(*) FROM rag_knowledge_bases WHERE JSON_VALUE(retrieval_config, '$.ingestion.graph_profile') = 'full';
     ```

### 旧 `full` で構築した文書

- ナレッジベースの「関係情報グラフ」は、再取込しなくても今までどおり表示される（残す表の中身は `entities` と同じ）。
- 文書詳細の関係情報の層は、再取込するまで「構築計画に含まれていますが、まだ実体化していません」と出る。層の ID は構築の設定値から作るため、`full` で記録した層と `entities` の層が別の ID になる。気になる文書は再取込する。`off` と `entities` の文書の層・chunk_set・索引は変わらない（層の ID の算法 `KEY_VERSION` は変えていない）。

## 既存環境の更新手順（#286: Docker Compose からネイティブ配備への移行）

#286 で、RAG の backend・ingestion-worker・前処理・parser を Docker Compose から、サービスごとの uv の venv と
systemd の unit に移した。サービス管理画面は docker compose ではなく systemd の unit を操作する。既定の解析エンジンは Docling になった。

### 何が変わるか

| 項目 | 以前（Docker Compose） | #286 以降 |
|---|---|---|
| backend / ingestion-worker | compose の `backend` / `ingestion-worker` | `production-ready-rag-backend.service` / `production-ready-rag-ingestion-worker.service`（`rag/backend/.venv`） |
| 前処理 / parser | compose の service（`rag-compose`） | `production-ready-rag-<service>.service`（`rag/services/*/*/.venv`、`127.0.0.1:<port>`） |
| 実行ユーザー | コンテナの `appuser` | 専用の system user `ragsvc`（home は `/var/lib/production-ready-rag`） |
| アップロード原本 | named volume `production-ready-rag_backend-local-storage` | `/u01/data/production-ready-rag` |
| OCI の設定 | named volume `production-ready-rag_oci-config` | `/var/lib/production-ready-rag/.oci/config` |
| モデル（docling など） | image に焼き込み | `ragsvc` の `~/.cache`（配備時に取得） |
| サービス管理の操作 | `docker compose up / stop / build / rm` | `systemctl enable --now / disable --now / restart`、`journalctl -u <unit>`（sudoers で許可した unit だけ） |
| 起動 / 停止の状態 | 再配備で compose_services.txt の service を全部起動 | 最後に操作した状態（enable / disable）を再起動・再配備でも保つ。初めての unit は起動 |
| Terraform | `rag_compose_services` / `rag_enable_parser_docling` / `compose_services.txt` | `rag_services`（Docling は常に配備）/ `rag_services.txt` |
| 既定の解析エンジン | Unstructured | Docling（`RAG_PARSER_ADAPTER_BACKEND=docling`） |

### OCI Compute（`init_script.sh` で配備した環境）

新しい stack で Compute を作り直すか、既存の Compute 上で `init_script.sh` を実行し直せば移行する。
`init_script.sh` は次を自動で行う。

1. 以前の `production-ready-rag.service`（compose）を `systemctl disable --now` で止めて消し、`rag-compose down --remove-orphans`
   でコンテナを消す（volume と image は残す）。`/usr/local/bin/rag-compose` は `rag-compose.legacy-docker` に退避する。
2. named volume `production-ready-rag_backend-local-storage` の中身を `/u01/data/production-ready-rag` へ、
   `production-ready-rag_oci-config` の中身を `/var/lib/production-ready-rag/.oci` へ写す（写し先が空のときだけ。上書きしない）。
3. `ragsvc` を作り、backend と前処理 / parser の venv・unit・sudoers を作って起動する。前処理 / parser は初めての unit なので
   全部起動する（Docker で止めていたサービスは、移行後に画面から停止し直す。以後はその状態が保たれる）。

既存の Compute 上で実行し直す場合は、cloud-init と同じく tag を固定して取得し、以前の stack が書いた入力
（`/u01/aipoc/props/backend.env`）を新しい既定に合わせてから実行する。`init_script.sh` は以前の `compose_services.txt`
（`backend` / `ingestion-worker` は読み飛ばす）も読むが、`parser-docling` が無い構成（以前の `rag_enable_parser_docling=false`）は
拒否するので、そのときは `compose_services.txt` に `parser-docling` を足す。

```bash
cd /u01/aipoc/no.1-production-ready-suite
sudo -u ubuntu git fetch --depth 1 origin <suite-v の tag> && sudo -u ubuntu git checkout -f FETCH_HEAD
# 既定の解析エンジン（Docling）と、画面からの起動 / 停止を有効にする（stack の新しい既定と同じ）
sudo sed -i -e 's/^RAG_PARSER_ADAPTER_BACKEND=.*/RAG_PARSER_ADAPTER_BACKEND=docling/' \
  -e '/^RAG_PARSER_UNSTRUCTURED_ENABLED=/d' \
  -e 's/^RAG_SERVICE_CONTROL_ENABLED=.*/RAG_SERVICE_CONTROL_ENABLED=true/' /u01/aipoc/props/backend.env
grep -q '^RAG_PARSER_DOCLING_ENABLED=' /u01/aipoc/props/backend.env \
  || echo 'RAG_PARSER_DOCLING_ENABLED=true' | sudo tee -a /u01/aipoc/props/backend.env
sudo APP_ROOT=/u01/aipoc bash rag/init_script.sh      # ログは /var/log/rag-init.log
```

移行後の確認と片付け:

```bash
systemctl list-units --all 'production-ready-rag-*'
sudo journalctl -u production-ready-rag-backend.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/api/health
sudo ls /u01/data/production-ready-rag /var/lib/production-ready-rag/.oci
```

- サービス管理画面で各サービスの状態（稼働中 / 起動中 / 停止 / 起動失敗 / 未登録）とログ（journalctl）が見え、起動 / 停止できる。
- 文書をアップロードして取込が成功する（既定の解析エンジンは Docling）。
- 問題が無ければ Docker の資源を消してよい（任意）: `docker volume ls -q | grep '^production-ready-rag_'`、
  `docker images --format '{{.Repository}}' | grep '^production-ready-rag-'` を確認して削除し、Docker 自体が不要なら
  `sudo apt-get purge docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` で外す。
- 画面から保存した値（`platform/.env`・`model-settings.json`・`backend/.env`）は host のファイルなので、そのまま引き継がれる。

### ローカル（Docker Compose で前処理 / parser を動かしていた環境）

1. 以前のコンテナを止めて消す（port 18010〜18038 を空ける）。#310 より前の project 名（旧 repo の
   `no1-production-ready-rag`、monorepo の `rag/` で compose を使った場合の `rag`）のコンテナも同じく消す。
   label で絞るので、別 project（例: 外部の MinerU API）のコンテナは消えない。

   ```bash
   for project in production-ready-rag no1-production-ready-rag rag; do
     docker ps -aq --filter "label=com.docker.compose.project=${project}" | xargs -r docker rm -f
   done
   ```

2. 「[ローカル開発 › 前処理 / parser](#前処理--parseruv-の-venv--systemd286)」の手順で unit と sudoers を登録する
   （`scripts/rag-services.sh install`）。`scripts/build-services.sh`、compose ファイル、Dockerfile は削除した（#356）。
3. `backend/.env` の `RAG_SERVICE_CONTROL_COMMAND` はもう読まない（削除してよい）。サービスの URL
   （`RAG_PARSER_*_SERVICE_URL` / `RAG_PREPROCESS_*_SERVICE_URL`）を docker 名（`http://parser-docling:8000` など）で
   書いている場合は、`backend/.env.example` と同じ `127.0.0.1:<port>` に直すか行を消す（#356 から読み替えない）。
4. backend をコンテナで動かしていた場合は、named volume（`production-ready-rag_backend-local-storage` など）の中身を
   `PLATFORM_LOCAL_STORAGE_DIR`（既定 `/u01/data/production-ready-rag`）へ写す。モデルのキャッシュの volume は写さなくてよい
   （ネイティブのサービスは初回に実行ユーザーの `~/.cache` へ取得する）。確認後に volume と image を消してよい
   （`docker volume ls -q | grep -E '^(production-ready-rag|no1-production-ready-rag|rag)_'`、
   `docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(production-ready-rag|no1-production-ready-rag)-'`）。

### 既定の解析エンジンの変更（Unstructured → Docling）

- `RAG_PARSER_ADAPTER_BACKEND` と `model-settings.json` の `parser_adapters` を保存していない環境は、Docling が既定になる。
  Unstructured を使い続ける場合は「検索・回答設定 › 文書解析」で Unstructured を選んで保存する（または `RAG_PARSER_ADAPTER_BACKEND=unstructured`）。
- Docling は PDF と画像だけを解析する。それ以外の形式は取込を始める前に止まるので、「[既定の解析エンジン（Docling）で扱える形式と、それ以外の形式の取り込み方](#既定の解析エンジンdoclingで扱える形式とそれ以外の形式の取り込み方286)」のとおり処理レシピで Unstructured（または Office→PDF）を選ぶ。
- Unstructured の解析サービスは既定では配備しない（stack の `rag_enable_parser_unstructured`）。Docker で `parser-unstructured` を動かしていた Compute は、入力を有効にしてから `init_script.sh` を実行すると unit が作られる。入力を有効にしないと、以前の `compose_services.txt` を読む場合を除き unit は作られない。
- 抽出レシピの ID は解析エンジンを含むため、既定のままの文書は次の取込から再抽出になる。

## 既存環境の更新手順（#596 標準の回答フローのテーブルの削除）

#595 で標準の回答フロー（`standard`）のコードを削除した後、#596 で、それだけが使っていたデータベースのオブジェクトを片付けた。システムテーブルの更新（migration `20260930_005_retire_standard_engine_objects`）が次を行う。あるものだけを消し、無ければ何もしない（冪等）。

- **削除するテーブル**: `rag_prompt_versions`（system prompt の版）・`rag_generation_settings`（回答スタイルの設定）・`rag_agent_memories`（Agent Memory）。index・制約はテーブルと一緒に消える（sequence は持っていない）。システムテーブルの全再作成でも、残っていれば消す。
- **削除する行**: `rag_role_permissions` のメニュー権限 `menu.settings_grounding` / `menu.settings_generation` / `menu.settings_agentic`（#595 から読み込み時に捨てていた値。共通のロール管理の権限昇格の判定が生の値を読むため、行も消す）。

### 残すもの

- **`rag_answer_records.answer_engine` の列**: 保存済みの回答の記録（履歴）が、どの回答の方式で作られたかを表す。新しい記録には常に同じ値を書く。
- **監査 `rag_search_audit` の標準の回答フローの内訳の列**（`memory_plan_id`・`agent_memory_*` など）: 既存の監査の行を変えないため残し、今は既定値を書く。
- **関係情報の構築（`rag_graph_*`）と navigation の要約の取り込み**: 回答の検索には使わないが、ナレッジベースの関係図と文書詳細が使う（関係情報のうち、読む経路の無かった claims / community summary の表は #621 で削除した）。

### 手順

**システムテーブルを更新すると、3 つのテーブルの行は戻せない。** `rag_agent_memories` は更新の前に書き出す。`init_script.sh` の再実行と画面の「システム設定 > データベース > RAG システムテーブル」の更新も、システムテーブルを更新する（`init_script.sh` は `system_schema_cli initialize` を実行する）ので、書き出してから行う。この migration はデータを削除するので、#619 から更新は承認（CLI の `--allow-destructive`・画面の確認ダイアログ）が無ければ止まる（上の「既存環境の更新手順の共通の注意」）。

1. #596 の版のコードを取得する（`git pull` など）。まだ backend の再起動・`init_script.sh` の再実行・システムテーブルの更新はしない。
2. `rag_agent_memories` を書き出す。書き出しの CLI（`app.rag.legacy_export`）は標準ライブラリと既存の Oracle 接続だけを使うので、取得したコードを今の venv で実行できる（import のエラーになる場合は、先に backend の venv を `uv sync --locked --no-dev` で更新する。Compute ではリポジトリの所有者 `ubuntu` で実行する）。テーブルが無い環境では何も書かずに `"status": "table_missing"` を出して終わる。`rag_prompt_versions`・`rag_generation_settings` も残したい場合は、`--table` を変えて同じように書き出す。

   ```bash
   # OCI Compute（init_script.sh で配備した環境）
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u ragsvc mkdir -p /var/lib/production-ready-rag/exports
   sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.legacy_export \
     --table rag_agent_memories --output /var/lib/production-ready-rag/exports/rag_agent_memories.jsonl

   # ローカル
   cd rag/backend
   uv run python -m app.rag.legacy_export --table rag_agent_memories --output ~/rag-exports/rag_agent_memories.jsonl
   ```

   出力は 1 行 1 レコードの JSON Lines（列名は小文字、日時は ISO 8601、CLOB は文字列、JSON の列は object、`embedding` は数値の配列）。書き終えてから出力先に置くので、途中で失敗したときは出力先にファイルを残さない。記憶の本文など利用者の入力を含むので、ファイルは所有者だけが読める権限（600）で作る。保管と削除は運用の規則に従う。標準出力の `row_count` が DB の件数（`SELECT COUNT(*) FROM rag_agent_memories`）と合うことを確かめる。
3. backend と frontend を再起動する（`init_script.sh` を再実行する場合は、手順 4 もあわせて行われる）。削除するテーブルはもう使わないので、システムテーブルを更新する前でも動く。
4. システムテーブルを更新する。データを削除する migration なので承認を付ける（#619）。画面の「システム設定 > データベース > RAG システムテーブル」からでもよい（「作成・更新」の確認ダイアログで「削除して更新」を押す）。

   ```bash
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.system_schema_cli initialize --allow-destructive
   ```

5. `system_schema_cli status` が `ready` で、`retired_objects` と `pending_versions` が空であることを確かめる。

## 既存環境の更新手順（#595 標準の回答フローの削除）

#594 で回答を回答フローだけにした後、#595 で標準の回答フロー（`standard`）だけが使っていたコード・画面・API・設定を削除した。回答の挙動は #594 から変わらない。旧名との互換は持たない。一覧は [rag-engine.md の「標準の回答フローの設定の削除」](./rag-engine.md)。

- **画面**: 根拠確認（`/settings/grounding`）・回答スタイル（`/settings/generation`）・高度な検索（`/settings/agentic`）を削除した。ブックマークなどの古い URL は検索方法（`/settings/retrieval`）へ移す。検索方法は「回答の検索と生成」「回答の記録の保存期間」「質問履歴」の 3 カード、回答プロンプトは回答生成のプロンプトだけ（system prompt の版の作成・有効化は削除）。業務ビューの検索モード・検索オプション・根拠確認・回答スタイル・回答プロンプト（system prompt / 既定の言語）の欄と、RAG 検索の検索モードのチップ・検索の内訳の診断も削除した。
- **API**: `GET/PATCH /api/settings/retrieval`・`/grounding`・`/generation`・`/agentic`、`GET/POST /api/settings/prompts`・`POST /api/settings/prompts/{version_id}/activate` を削除した。`POST /api/search` などの `mode`・`strategy`・`rerank_top_n`・`generation_profile` と、チャットの送信の `mode` は、送っても 422 にせず読み捨てる。
- **メニュー権限**: `menu.settings_grounding` / `menu.settings_generation` / `menu.settings_agentic` を削除した。ロールに保存済みの値は読み込み時に捨てる（権限管理の画面にも出ない）。DB の行は #596 で削除した（上の「既存環境の更新手順（#596）」）。
- **環境変数**: 次の `RAG_*` は読まなくなった。`backend/.env` に残っていても起動し、値は使わない。次に編集するときに消してよい。
  - `RAG_CONTEXT_WINDOW_CHARS`・`RAG_CONTEXT_NEIGHBOR_WINDOW`・`RAG_CONTEXT_DIVERSITY_LAMBDA`・`RAG_CONTEXT_GROUP_EXPANSION_ENABLED`・`RAG_CONTEXT_ADAPTIVE_*`・`RAG_CONTEXT_DEPENDENCY_*`・`RAG_CONTEXT_COMPRESSION_*`（`RAG_CONTEXT_GROUP_MAX_CHUNKS` は回答フローの small-to-big が使うので残る）
  - `RAG_GROUNDING_CRAG_CONFIDENCE_THRESHOLD`・`RAG_CRAG_*`・`RAG_QUERY_EXPANSION_*`・`RAG_STREAM_REALTIME_ENABLED`・`RAG_AGENT_MEMORY_*`
  - `RAG_RETRIEVAL_STRATEGY`・`RAG_RETRIEVAL_GAP_STOP_ENABLED`・`RAG_RETRIEVAL_BUSINESS_FIT_WEIGHTING_ENABLED`・`RAG_RETRIEVAL_CORRECTIVE_ENABLED`・`RAG_REASONING_TREE_MAX_SECTIONS`・`RAG_POST_RETRIEVAL_PIPELINE`
  - `RAG_GENERATION_*`（回答スタイル・system prompt の上書き・既定の言語・カスタムプロンプトと版・設定の保存先・サービスの接続）・`RAG_AGENTIC_*`・`RAG_GROUNDING_SERVICE_*`・`RAG_RETRIEVAL_SERVICE_*`

  ```bash
  # 残っている行を確かめる（消すときは backend/.env のバックアップを取ってから編集する）
  grep -nE '^RAG_(CONTEXT_(WINDOW_CHARS|NEIGHBOR_WINDOW|DIVERSITY_LAMBDA|GROUP_EXPANSION_ENABLED|ADAPTIVE_|DEPENDENCY_|COMPRESSION_)|GROUNDING_CRAG_|CRAG_|QUERY_EXPANSION_|STREAM_REALTIME_ENABLED|AGENT_MEMORY_|RETRIEVAL_(STRATEGY|GAP_STOP_ENABLED|BUSINESS_FIT_WEIGHTING_ENABLED|CORRECTIVE_ENABLED|SERVICE_)|REASONING_TREE_MAX_SECTIONS|POST_RETRIEVAL_PIPELINE|GENERATION_|AGENTIC_|GROUNDING_SERVICE_)' backend/.env
  ```

- **サービス**: 検索・根拠確認・高度な検索・回答生成の段のサービス（`services/pipeline/{retrieval,grounding,agentic,generation}`）を削除し、サービス管理の一覧からも外した。いずれも backend の中で動く段（`deployable=False`）で systemd の unit を持たず、配備の対象ではなかったので、Compute での作業は要らない。
- **データベース**: #595 の更新では DDL・データの移行は要らない。`rag_prompt_versions`・`rag_generation_settings`・`rag_agent_memories` のテーブルは #596 で削除した（上の「既存環境の更新手順（#596）」。`rag_agent_memories` は更新の前に書き出す）。`rag_search_audit` の検索・context の内訳の列（`memory_plan_id`・`agent_memory_*` など）は残し、既定値を書く。
- **手順**: コードを更新して backend と frontend を再起動する（上の環境変数は消さなくてもよい）。

## 既存環境の更新手順（#594 回答の方式を 1 つにする）

#594 で回答の方式を、根拠照合・監査付きの回答（質問の振り分けと拡張 → 検索 → CRAG の根拠の判定 → 回答の生成 → 監査）の 1 つにし、回答エンジンの選択（`backend/.env` の `RAG_ANSWER_ENGINE` と、業務ビューの「回答エンジン」）を削除した。旧名との互換は持たない。仕組みと設定は [rag-engine.md](./rag-engine.md) を参照。

### 変わること

- **回答の方式が全員変わる。** 次の呼び出しは、これまでの標準の回答（`RAG_ANSWER_ENGINE` の既定 `standard`）から新しい方式に変わる。
  - DEFAULT 業務ビュー（`query` が空で、全体の既定を継承していた）。
  - 「回答エンジン」を継承していた業務ビューと、「標準」を明示していた業務ビューの RAG 検索・チャット。
  - 業務ビューを指定しない呼び出し（MCP の `rag_search` / `/api/search` のナレッジベースだけの指定、品質評価、staging smoke）。
  - ナレッジベースの検索テストとレシピの検索比較は、回答を作らずに検索だけを行う（#593 の検索だけの経路。LLM を呼ばない）。
- **LLM の呼び出しが増える。** 標準の回答は 1 回の回答で回答の生成が 1 回（とオプションのクエリ拡張など）だった。新しい方式は、既定（質問の拡張 = 自動ルーティング、回答の生成方式 = CRAG）で、質問の振り分け・CRAG の根拠の判定（根拠が足りなければ検索文を書き換えて最大 3 回）・回答の生成・監査の順に、1 回の回答で少なくとも 4 回呼ぶ。チャットで会話履歴があると質問の書き換えが 1 回、「画面目録で操作画面を探す」を有効にすると 1 回増える。回答の時間と OCI Enterprise AI の利用量が増えるので、`RAG_ANSWER_TIMEOUT_SECONDS`（既定 300 秒）と利用の上限を見直す。回数を減らすときは、検索・回答設定 › 検索方法の「回答の検索と生成」（業務ビューごとは「検索・回答設定」）で、質問の拡張を「単純検索」、回答の生成方式を「標準 RAG」にする。
- 回答の記録（`rag_answer_records`）は、すべての回答で保存する（保存期間は `RAG_ANSWER_RECORD_RETENTION_DAYS`）。品質評価の結果も新しい方式の回答になるので、更新の前の結果と比べるときは同じケースで評価し直す。
- 業務ビューの「検索方法」「検索オプション」「根拠確認」「回答スタイル」「回答プロンプト」と、検索・回答設定の同じ名前の画面の設定は、回答に使われない（#594 では入力を残し、「現在の回答では、この設定は使われません。」と表示していた。欄は #595 で削除した）。回答の上書きは、業務ビューの「質問の拡張」「回答の生成方式」「根拠の前後から加える数」「回答の検索のオプション」で行う（業務ビューの編集画面で常に表示する）。安全チェックは今までどおり質問と回答の両方に適用する。

### 更新の前に確かめる

`backend/.env` の `RAG_ANSWER_ENGINE` を確かめる（行が無いか `standard` なら、継承していた業務ビューもすべて変わる）。

```bash
grep '^RAG_ANSWER_ENGINE=' backend/.env
```

「回答エンジン」に標準（`standard`）を明示していた業務ビューを、Oracle で確かめる（`view_config` は業務ビューの設定の JSON）。

```sql
-- 「標準」を明示していた業務ビュー（#594 の後は新しい方式で回答する）
SELECT business_view_id, name, status
  FROM rag_business_views
 WHERE JSON_VALUE(view_config, '$.query.answer_engine') = 'standard'
 ORDER BY name;

-- 回答エンジンの設定ごとの件数（「(継承)」は全体の既定を継承。DEFAULT 業務ビューを含む）
SELECT NVL(JSON_VALUE(view_config, '$.query.answer_engine'), '(継承)') AS answer_engine,
       status,
       COUNT(*) AS view_count
  FROM rag_business_views
 GROUP BY NVL(JSON_VALUE(view_config, '$.query.answer_engine'), '(継承)'), status
 ORDER BY answer_engine, status;
```

標準の回答と比べたい業務ビューは、更新の前に代表の質問の回答を控えておく（品質評価のケースにしておくと、更新の後に同じケースで比べられる）。

### 手順

1. `backend/.env` から `RAG_ANSWER_ENGINE` の行を削除する（`sed -i '/^RAG_ANSWER_ENGINE=/d' backend/.env`。直前の説明のコメント行も消してよい）。行が残っていても読まない。
2. 業務ビューに保存済みの `query.answer_engine` は読み込み時に捨て、次に保存したときに消える（DDL・データの移行は要らない）。
3. 回答の時間と利用量を見直す（上の「LLM の呼び出しが増える」）。
4. 文書分割の既定を見直す（下の「文書分割の既定」）。
5. backend を再起動する。

### 文書分割の既定

新しい回答は、親子で分割した chunk（`small_to_big`。画面の表示名は「親子階層（small-to-big）」）を前提にする。親の本文（見出しの節）を回答の文脈にし、表は行グループごとに列見出しを付けた子で探し、根拠の元の要素（`source_record_refs`）から図の切り出しと要素ごとの強調を行う。そのため、#594 で `RAG_CHUNKING_STRATEGY` の既定を `structure_aware` から `small_to_big` に変えた。

- `backend/.env` に `RAG_CHUNKING_STRATEGY` を書いていない環境（文書分割の画面で保存したことがない環境）では、レシピで分割方式を上書きしていない文書は、次に Chunk を作るときから親子で分割する。既存の chunk は自動では作り直さない。
- 親子の分割は Docling の解析結果を入力にする。解析結果が Docling でない文書（Unstructured・MinerU・dots.ocr・OCI など）は、今までどおり構造認識で分割し、文書詳細の Chunk タブに「構造認識で分割しました」と表示する（[rag-engine.md の「使い方」2.](./rag-engine.md)）。
- 構造認識で作った既存の chunk も、そのまま検索と回答に使える（同じまとまりの chunk をつないで親の代わりにする）。ただし、親が節の区切りにならない・表の列見出しを繰り返さない・根拠の図を切り出せない、ため回答の文脈が粗くなる。回答の品質を上げるには、Docling で解析した文書の Chunk を再作成する（chunk が変わるので embedding をやり直す。OCI Generative AI の利用が増える）。
- 構造認識のまま使う場合は、`backend/.env` に `RAG_CHUNKING_STRATEGY=structure_aware` を書く（検索・回答設定 › 文書分割で構造認識を選んで保存しても同じ）。

## 既存環境の更新手順（#588 全文検索の分割方式の統合）

#588 で全文検索（Oracle Text）の検索語の分割を、Sudachi（形態素解析）と文字種の区切りを組み合わせた 1 つの方式にし、選択を削除した（分割の中身は [rag-architecture.md の「ハイブリッド検索」](./rag-architecture.md)）。旧名との互換は持たない。

- 「検索・回答設定 › 検索方法」と業務ビューの編集の「全文検索の分割方式」の選択は無くなった。
- `backend/.env` の `RAG_TEXT_SEARCH_TOKENIZER` は読まない。行が残っていても無視するが、混乱を避けるため削除する（`sed -i '/^RAG_TEXT_SEARCH_TOKENIZER=/d' backend/.env`。直前の説明のコメント行も消してよい）。
- 業務ビューに保存済みの上書き（`query.text_search_tokenizer`）は読み込み時に捨て、次に保存したときに消える（移行の作業は要らない）。
- rag_engine を単独で使う環境の `TEXT_SEARCH_TOKENIZER`（`auto` / `regex` / `sudachi`）も読まない。辞書の設定（`TEXT_SEARCH_TOKENIZER_SUDACHI_DICT` / `_CONFIG` / `_LATIN_STEMMER`）は今までどおり使う。
- 索引（`rag_chunks_text_idx` などの `RAG_TEXT_WORLD_LEXER`）は変えていないので、作り直しは要らない。backend を再起動すると新しい分割になる。
- Sudachi の辞書（`sudachidict_full`。backend の依存に含まれる）が入っていない環境では、自動で文字種の区切りだけで分割し、backend の log に「Sudachi を使えないため、全文検索の語は文字種の区切りだけで作ります」を 1 回出す。

## 既存環境の更新手順（#566 既定のテキストモデルの必須化）

#566 で「システム設定 › モデル」の既定のテキストモデルを必須にした（並びはテキスト → Vision）。既定のテキストモデルが未設定の環境は、
「システム設定 › モデル」で既定のテキストモデルを選んで保存する（以前と同じ動きにするなら、既定の Vision モデルと同じモデル）。
保存し直すまでは、従来どおり既定の Vision モデルを使う。詳細は [platform/README.md の「既存環境の更新手順（#566）」](../../platform/README.md#既存環境の更新手順566-既定のテキストモデルの必須化)を参照。

## 既存環境の更新手順（#499 既定のモデルの変数名）

#499 で既定のモデルを「既定のテキストモデル」と「既定の Vision モデル」の 2 つに分け（#566 で 2 つとも必須にした）、
共通 `.env` の `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_MODEL` / `_LLM_MODEL` / `_VLM_MODEL` を
`PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_TEXT_MODEL` / `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_VISION_MODEL` に改名した（旧名は読まない）。
`platform/.env` に既定のモデルを書いている環境は、backend と worker を止めてから
`platform/scripts/migrate_model_env_names.py`（確認 → `--apply`）で書き換え、起動し直す。OCI の parser は、backend が書き直す実行用の env を読むよう「サービス管理」画面から再起動する。旧名と新名の対応、`model-settings.json` の
扱い（旧 key は読み込み時に移す）は [platform/README.md の「既存環境の更新手順（#499）」](../../platform/README.md#既存環境の更新手順499-既定のモデルの変数名)を参照。

## 既存環境の更新手順（#211: 共通 .env への移行）

#211 で、3製品共通の設定は共通 `.env`（`platform/.env`、`PLATFORM_*`）、RAG 固有の設定は `backend/.env`（`RAG_*`）に分けた。
旧名は読まないため、この版へ更新する環境では次の手順で `.env` を移す。

1. backend と ingestion-worker を停止する（Compute の stack では
   `sudo systemctl stop production-ready-rag-backend.service production-ready-rag-ingestion-worker.service`、
   ローカルでは uvicorn を止める）。
2. 移行内容を確認する（書き換えない）。リポジトリ root で実行する。

   ```bash
   uv run --project platform/packages/backend_core \
       python platform/scripts/migrate_env_to_platform.py --product rag
   ```

   共通の変数（`OCI_*` / `ORACLE_*` / `OBJECT_STORAGE_*` / `UPLOAD_STORAGE_BACKEND` / `LOCAL_STORAGE_DIR` /
   `MODEL_SETTINGS_FILE` / Enterprise AI・Generative AI のモデル設定）は `PLATFORM_*` に改名して `platform/.env` へ、
   それ以外は `RAG_*` に改名して `backend/.env` に残る（`HF_TOKEN` / `HF_ENDPOINT` は `RAG_HUGGINGFACE_TOKEN` /
   `RAG_HUGGINGFACE_ENDPOINT`）。共通 `.env` に別の値が既にある変数は競合として表示されるので、どちらを残すか確認する。
3. 問題がなければ `--apply` を付けて書き換える（`<file>.bak-211` を作ってから書き換える）。

   ```bash
   uv run --project platform/packages/backend_core \
       python platform/scripts/migrate_env_to_platform.py --product rag --apply
   ```

4. 以前の Docker Compose の配備から移した環境では、画面から保存したモデル設定とモデル・parser の API key が
   `PLATFORM_LOCAL_STORAGE_DIR`（既定 `/u01/data/production-ready-rag`）の `model-settings.json` と同じディレクトリの `.env` に
   残っていることがある（#286 の移行で named volume `backend-local-storage` から写したもの）。`model-settings.json` を `platform/` へ、
   `PLATFORM_OCI_ENTERPRISE_AI_API_KEY`（旧 `OCI_ENTERPRISE_AI_API_KEY`）を `platform/.env` へ、`RAG_PARSER_*_API_KEY` を
   `backend/.env` へ移す（またはシステム設定画面で入力し直す）。
5. 再配備または再起動する。Compute の stack では更新した tag で `init_script.sh` を実行し直す（#286 の手順）。
   ローカルでは uvicorn を起動し直す。
6. `/api/ready` がすべて `ok` になること、システム設定画面（OCI 認証・アップロード保存先・モデル・データベース）に
   移行前の値が表示されることを確認する。確認後に `*.bak-211` を削除する。

## 既存環境の更新手順（#214 共通認証と権限管理）

RAG のログインは、`.env` の単一アカウント（`RAG_AUTH_USERNAME` / `RAG_AUTH_PASSWORD` / `RAG_AUTH_SESSION_SECRET`）から、
3製品共通の認証（構成管理者 `system_admin` と DB ユーザー）に変わった。`RAG_AUTH_MODE=production` の環境は、更新時に次を行う。

1. 共通 `.env` に構成管理者を設定する（NL2SQL と同じ値を共有する）。

   ```bash
   PLATFORM_ADMIN_LOGIN_USER_ID=system_admin
   PLATFORM_ADMIN_LOGIN_USER_PASSWORD=<12〜30 文字、大文字・小文字・数字を含み、admin と " を含まない>
   ```

   HTTPS で配信している場合は `PLATFORM_AUTH_COOKIE_SECURE=true` にする。`backend/.env` の
   `RAG_AUTH_USERNAME` / `RAG_AUTH_PASSWORD` / `RAG_AUTH_SESSION_SECRET` / `RAG_AUTH_SESSION_TIMEOUT_SECONDS` /
   `RAG_AUTH_COOKIE_NAME` / `RAG_AUTH_COOKIE_SECURE` はもう読まないので削除する。
2. 更新した版で起動し、システムテーブルを初期化する（`PLATFORM_*` がなければ作り、`RAG_ROLE_*` を追加する。冪等）。

   ```bash
   cd /u01/aipoc/no.1-production-ready-suite/rag/backend
   sudo -u ragsvc HOME=/var/lib/production-ready-rag .venv/bin/python -m app.rag.system_schema_cli initialize
   ```

   画面の「システム設定 > データベース > RAG システムテーブル」からでもよい。
3. `system_admin` でログインし、「ユーザーとロール」でロールとユーザーを作り、「セキュリティ設定 > 権限管理」で
   ロールごとのメニュー・業務ビュー・ナレッジベースを設定する。NL2SQL と同じ Oracle schema を使う場合、ユーザーとロールは NL2SQL と共有される。
4. 注意:
   - 会話と回答履歴の持ち主は、ログインしたユーザー（`user_uuid`）になる。旧方式で作った会話（持ち主は共通の `admin-user-id`）は、新しいユーザーからは見えない（移行はしない）。
   - 保存済みの回答（`rag_answer_records`）は #304 から持ち主（`user_id_hash`）で分ける。システムテーブルの初期化（migration `20260928_002_answer_record_owner`）で列を足し、既存の行はチャットの回答・検索の監査から利用者が 1 人に決まるものだけ持ち主を補う。補えなかった行は SYSTEM_ADMIN と `rag.feedback.manage` を持つ利用者だけが一覧・詳細・評価・削除できる（不要なら管理者が削除するか、保存期間の経過で消える）。
   - 利用者フィードバック（`rag_citation_feedback`）は #408 から送った利用者（`user_id_hash`）で分ける。一覧・件数・集計・詳細（評価ケースの作成・承認済み FAQ への反映を含む）は、SYSTEM_ADMIN（構成管理者・local の利用者を含む）だけがすべての利用者の分を扱い、ほかのロールは自分が送った分だけを扱う（`rag.feedback.manage` を持っていても同じ。NL2SQL の実行履歴と同じ規則）。他人のフィードバックの ID を指定すると 403 になる。列は以前から保存しているので schema の変更はない。送信者の記録がない古い行は SYSTEM_ADMIN だけに見える。
   - Cookie 名が `production_ready_rag_session` から `rag_session` / `rag_csrf` に変わるため、利用者は一度ログインし直す。
   - 評価・負荷試験の CLI（`app.rag.evaluation_cli` など）が `RAG_AUTH_MODE=production` の API を呼ぶ場合は、ログインしたセッションが必要になる。

## 既存環境の更新手順（#390 品質評価の job）

品質評価（評価・比較）を job にした。画面と評価 CLI（nightly を含む）は job の API を使う。

1. システムテーブルを更新する（migration `20260928_005_evaluation_jobs`。表 `rag_evaluation_jobs` と index
   `rag_evaluation_jobs_status_idx`・`rag_evaluation_jobs_owner_created_idx` を無ければ作るだけで、既存の表は変えない）。
   更新するまで、評価の実行は表が無いためエラーになる（同期の `/api/evaluation/run`・`/compare` は引き続き使える）。
2. backend を再起動する。job 全体の上限を変える場合は `backend/.env` の `RAG_EVALUATION_JOB_TIMEOUT_SECONDS` を設定する。
3. 評価 CLI を自前の CI から呼んでいる場合は、待つ時間（`--timeout` / `RAG_EVALUATION_TIMEOUT_SECONDS`、既定 3900 秒）が
   その CI の job の timeout に収まるようにする。以前の同期 API の URL を指定していても、CLI が job の API に読み替える。

## 既存環境の更新手順（#357 取込 job の lease と heartbeat）

取込 job（`rag_ingestion_jobs`）に lease の列（`lease_owner` / `heartbeat_at`）を追加し、実行中かどうかを
「開始からの経過時間」ではなく worker の heartbeat で判定するようにした。長い job が実行中に再キューされる
（worker が 1 つなら後ろの job が流れない・2 つ以上なら二重実行になる）ことと、worker の停止で job が
`RUNNING` のまま残ることを防ぐ。

1. システムテーブルを更新する（migration `20260928_004_ingestion_jobs_lease`。列と index
   `rag_ingestion_jobs_lease_idx` を追加するだけで、既存の行は変えない）。更新するまで取込 worker は
   「システムテーブルの作成・更新が必要」のログを出して待つ。
2. 取込 worker を再起動する（Compute の stack では `init_script.sh` の再実行が unit を書き直して restart する）。
   更新前から `RUNNING` の行は `heartbeat_at` が無いため、従来どおり `RAG_INGESTION_QUEUE_STALE_RUNNING_SECONDS`
   （開始から 300 秒）で回復する。
3. `backend/.env` で `RAG_INGESTION_QUEUE_HEARTBEAT_INTERVAL_SECONDS` / `RAG_INGESTION_QUEUE_LEASE_TTL_SECONDS` を
   変える場合は、TTL を間隔の 3 倍以上にする（満たさないと起動時にエラーになる）。

## 既存環境の更新手順（#341 文書のレシピ1の補完）

以前は文書のレシピ1（`rag_document_recipes` の slot 1）を `GET /api/documents/{id}/recipes` の中で遅延して作っていた。
#341 から GET では書き込まず、レシピ1は文書の登録・ジョブの投入・レシピの編集など書き込みの経路で作る。

1. システムテーブルを更新する（migration `20260928_003_default_document_recipes`）。レシピ行が 1 件も無い文書にだけ、
   文書の状態からレシピ1を作る。レシピ1を削除して他のレシピだけがある文書には作らない。
2. migration の前でも画面は動く。レシピ行の無い文書は、一覧・詳細でレシピ1を仮の行として返し、最初の書き込み
   （設定の保存・処理の開始など）で行を作る。

## 既存環境の更新手順（#556 項目抽出の標準の項目）

項目抽出の全体の既定に、どの文書にも通じる標準の 4 項目（文書の種類・文書タイトル・発行日・作成日・発行元・作成部署）を用意した。
定義は `backend/app/rag/extraction_field_adapter.py` の `STANDARD_FIELD_DEFINITIONS`。

- **標準の項目が有効になる条件**: 全体の既定の定義のファイル（`backend/extraction-fields.json`、`RAG_FIELD_SCHEMA_FILE` で
  場所を変えている環境はそのファイル）が**無い**環境だけ。一度も「項目の定義を保存」していない環境が当たる。
  項目抽出（`RAG_FIELD_EXTRACTION_ENABLED`、#537 から既定で有効）が有効なら、更新後の次の取込から
  **文書ごとに OCI Enterprise AI の呼び出しが 1 回増える**。
- 定義を保存したことのある環境（0 件で保存した場合を含む）は、ファイルがあるため何も変わらない。
  ナレッジベースで項目を定義している文書も、今までどおりその定義を使う（KB に定義が無い文書だけが全体の既定を使う）。
- 既存の文書の抽出値は変わらない。標準の項目で抽出するのは、次に解析（再処理）したときから。
  以前に定義 0 件で取り込んだ文書は、どの定義で作ったかの記録が無いため「作り直しが必要」（#550）の印は出ない。
  標準の項目で抽出し直したい文書は、文書のレシピから再処理する。
- **標準の項目を使わない方法**（どれか 1 つ）:
  - 項目抽出そのものを止める: `backend/.env` に `RAG_FIELD_EXTRACTION_ENABLED=false`、または「検索・回答設定 › 文書解析」の
    「解析後の処理」で「メタデータ/項目抽出」を無効にして保存する。
  - 項目抽出は有効のまま、全体の既定を空にする: 「抽出する項目の定義」ですべての項目を削除して「項目の定義を保存」する
    （0 件の定義を保存したファイルができ、標準の項目は使わない。KB で定義した項目は今までどおり抽出する）。
  - 自分の項目に置き換える: 項目を編集して保存する。
- 保存した定義を消して標準の項目に戻すときは、同じ画面の「標準の項目に戻す」（`DELETE /api/settings/extraction-fields`）を使う
  （定義のファイルを消す。以後は、その版の標準の項目を使う）。
- データの移行（migration）と `.env` の変更は不要。

## 既存環境の更新手順（#548 ナレッジベースごとの項目抽出の定義）

項目抽出の項目の定義をナレッジベースごとに持てるようにした（KB の詳細の「抽出する項目」）。

1. システムテーブルを更新する（migration `20260930_004_knowledge_base_extraction_fields`。`rag_knowledge_bases` に
   `extraction_fields JSON` 列を追加するだけで、既存の行は NULL のまま）。NULL の KB は全体の既定の定義
   （`extraction-fields.json`）を使うため、既存環境の抽出の挙動は変わらない。
2. 更新するまで取込 worker は「システムテーブルの作成・更新が必要」のログを出して待ち、KB の「抽出する項目」の
   読み書きは列が無いためエラーになる。backend を更新したら、システムテーブルを更新する。

## 既存環境の更新手順（#537 Vision と項目抽出を既定で有効にする）

「図・画像を AI で読み取る（Vision）」（`RAG_VISION_ENABLED`）と「メタデータ/項目抽出」（`RAG_FIELD_EXTRACTION_ENABLED`）の
既定を有効（`true`）にした。

- `backend/.env` で値を明示していない環境は、更新後に両方が有効になる。
  - Vision は、画像 1 枚ごとにモデル設定の既定の Vision モデルを呼ぶ。そのため、取込の時間と呼び出しが増える。
  - 項目抽出は、項目の定義（「検索・回答設定 › 文書解析」の「解析後の処理」）が 0 件なら何もしない。
    #556 から、定義を一度も保存していない環境は標準の項目を使う（上の「#556 項目抽出の標準の項目」）。
- 無効のままにしたい環境は、`backend/.env` に `RAG_VISION_ENABLED=false` / `RAG_FIELD_EXTRACTION_ENABLED=false` を書くか、
  文書解析の画面の「解析後の処理」で無効にして保存する。文書のレシピの上書きは、今までどおり優先される。
- データの移行（migration）は不要。既存の文書は、再解析したときに新しい既定で処理される。

## 既存環境の更新手順（#497 図・画像の読み取り（Vision）の統合）

「図・画像を AI で読み取る（Vision）」を解析エンジンに依存しない設定にし、「図表 VLM 要約」を削除した。
Vision は Docling を含む全ての解析エンジンで、解析の後に backend の共通の段（`app/rag/vision.py`）がモデル設定の
既定の Vision モデルで読み取る。docling サービスは Vision を行わない（LLM を呼ばない）。データの移行（migration）は不要。

1. 全体の既定を `backend/.env` の `RAG_VISION_ENABLED` へ移す。旧名は読まない（#211 と同じく互換を持たない）。
   - `RAG_PARSER_DOCLING_VISION_ENABLED=true` だった環境は `RAG_VISION_ENABLED=true` にする。
   - `RAG_ASSET_SUMMARY_ENABLED` / `RAG_ASSET_SUMMARY_MAX_ASSETS` は削除する（図表 VLM 要約は Vision に統合した。
     読み取る画像の件数の上限は設けない）。
   - 旧名が残っていても読まないので害はない。
2. 「検索・回答設定 › 文書解析」の Vision のスイッチは削除した。`model-settings.json` の
   `parser_adapters.docling_vision_enabled` は読まない（残っていても読み込みは失敗せず、次に保存すると消える）。
   画面のスイッチで有効にしていた環境は、手順 1 の `RAG_VISION_ENABLED=true` にするか、文書のレシピで Vision を有効にする。
3. 保存済みの文書レシピ・KB 構築設定は読み込み時に移す。`parser_docling_vision_enabled` は `vision_enabled` に、
   `asset_summary_enabled` は `vision_enabled` が未設定のときだけ `vision_enabled` にする。次に保存すると旧 key は消える。
4. `extraction_recipe_id` の key に Vision（`rag_vision_enabled`）を全ての解析エンジンで入れ、図表 VLM 要約を派生情報の
   レイヤーの key から外したため、layer の ID の版（`variant_keys.KEY_VERSION`）を `v5` に上げた。既存の配信中の chunk は
   そのまま検索対象に残る。Vision を有効にして読み取り直すには、その文書を再解析する。
5. docling サービスへ渡していた接頭辞なしの `OCI_ENTERPRISE_AI_*`（Vision 用）はサービス実行用の env ファイルに書かなくなった。
   backend を更新した後、サービス管理画面で docling サービスを再起動すると反映される（残っていても使わない）。

## 既存環境の更新手順（#270 文書解析エンジン Marker・Unlimited-OCR・GLM-OCR の削除）

文書解析エンジンの Marker（CPU）・Unlimited-OCR（GPU の外部 API）・GLM-OCR（GPU の外部 API）への対応を削除した。
更新時に次を確認する。データの移行（migration）は不要。

1. 保存済みの設定は読み込み時に既定へ寄せる（画面や取込は壊れない）。
   - `model-settings.json`（`parser_adapters`）や `RAG_PARSER_ADAPTER_BACKEND` に削除したエンジンが残っていれば、
     既定の解析エンジン（#286 以降は Docling）として扱う。「検索・回答設定 › 文書解析」で保存し直すと旧値は消える。
   - 文書レシピ・KB 構築設定に残っていれば「global 既定の解析エンジンを継承」として扱う（分割などの他の上書きは保つ）。
     これらのエンジンで作った既存の索引はそのまま検索対象に残り、文書の取込設定には parser のずれ（再処理が必要）と表示される。
     必要な文書だけ、利用できる解析エンジンを選び直して再処理する。
2. 使われなくなった設定を削除する（読まないので残っていても害はない）。
   - `backend/.env`: `RAG_PARSER_MARKER_ENABLED` / `RAG_PARSER_MARKER_SERVICE_URL` / `RAG_PARSER_UNLIMITED_OCR_*` / `RAG_PARSER_GLM_OCR_*`
     （画面から保存した `RAG_PARSER_UNLIMITED_OCR_API_KEY` / `RAG_PARSER_GLM_OCR_API_KEY` を含む）。
3. 以前の Docker Compose の環境で `parser-marker` のコンテナが残っていれば、#286 の移行（上の「ローカル」の手順 1 は
   project のコンテナをすべて消す）で消える。Terraform の stack で `rag_enable_parser_marker=true` にして配備した Compute は、
   `${APP_ROOT}/props/compose_services.txt` から `parser-marker` を消してから `init_script.sh` を実行し直すか
   （許可していない service として拒否されるため）、stack を更新して Compute を作り直す。stack の入力 `rag_enable_parser_marker` は削除した。

## 既存環境の更新手順（#310 compose の project 名の固定と parser image の作り直し）

#310 は Docker Compose の環境向けの変更（compose の project 名を `production-ready-rag` に固定し、parser-docling /
parser-unstructured の cv2 を headless 版の `opencv-python-headless` にして image を作り直す）だった。#286 以降は Docker を
使わないため、この手順は不要。旧 project（`no1-production-ready-rag` / `rag`）のコンテナ・volume・image が残っている環境は、
「[既存環境の更新手順（#286）](#既存環境の更新手順286-docker-compose-からネイティブ配備への移行)」の「ローカル」の手順で消す。
cv2 の headless 版は各サービスの `uv.lock` に固定されている（venv を作れば反映される）。

## 本番構成

OCI Resource Manager の統合 Terraform stack（monorepo root の [`terraform/stack/`](../../terraform/README.md)、#217）は、RAG 用の Compute 1 台に
NL2SQL / Agent と同じネイティブ配備（uv の venv + systemd + Nginx。#286）を作る。Docker は使わない。
ADB（Oracle Autonomous AI Database）と Wallet も stack が用意し（NL2SQL / Agent と共有する）、RAG の system schema はアプリの CLI（`app.rag.system_schema_cli initialize`）で適用する。

| 構成要素 | 内容 |
|---|---|
| 実行ユーザー | 専用の system user `ragsvc`（home `/var/lib/production-ready-rag`）。リポジトリは `ubuntu` の所有で、`ubuntu` が git pull・uv sync・frontend の build を行う |
| Python | `uv python install 3.12`（`/opt/uv/python`。`ragsvc` も読める）。各サービスの venv は `uv sync --locked --no-dev --python 3.12` |
| backend | `production-ready-rag-backend.service`（gunicorn + UvicornWorker、`127.0.0.1:8000`、workers 2、`RAG_ENVIRONMENT=prod`、in-process の取込 worker は無効） |
| 取込 worker | `production-ready-rag-ingestion-worker.service`（`python -m app.rag.ingestion_worker`、`KillMode=mixed`、`RAG_INGESTION_QUEUE_SHUTDOWN_GRACE_SECONDS=60`、`TimeoutStopSec=90`。停止は下の「取込 worker の lease と停止」） |
| 前処理 / parser | `production-ready-rag-<service>.service`（サービスごとの venv の gunicorn、`127.0.0.1:<port>`）。対象は stack の `rag_services`（前処理 7 つ・parser-docling と、選べば parser-unstructured・OCI parser 2 つ）。GPU の parser は配備しない |
| frontend | host の Node.js 24 で `platform` と `rag/frontend` を build し、Nginx が `rag/frontend/dist` を配信して `/api/` を backend へ proxy する |
| upload の上限 | Nginx の `client_max_body_size` は backend の `RAG_MAX_UPLOAD_BYTES`（`backend/.env`。既定 200 MiB）+ 10 MiB（MiB で切り上げ。Refs #306） |
| 設定 | `platform/.env`（`PLATFORM_*`）と `rag/backend/.env`（`RAG_*`）。どちらも `ragsvc` だけが読める 0600。OCI parser の unit は両方を EnvironmentFile で読む |
| サービス実行用 env | `rag/backend/service-runtime.env`（0600）。サービス管理が起動 / 再起動の前に HuggingFace 設定と実効 OCI Enterprise AI 設定を書き、前処理 / parser の unit が EnvironmentFile で読む |
| ログ | `/var/log/rag-init.log`、`journalctl -u production-ready-rag-<service>.service` |

### サービス管理画面と sudoers

画面の起動 / 停止 / 再起動 / ログは、backend（`ragsvc`）が次のコマンドだけを `sudo -n` で実行する。argv は固定で、
unit 名は backend のカタログの allowlist（前処理 / parser のうち配備したもの）で検証する（shell を通さない）。
backend・ingestion-worker の unit と、それ以外のコマンドは許可しない。状態は `systemctl show`（root 不要）と各サービスの `/health` で判定する。

```text
# /etc/sudoers.d/production-ready-rag-services（init_script.sh が生成。visudo -cf で検査してから 0440 で置く）
Cmnd_Alias RAG_SERVICE_CONTROL = /usr/bin/systemctl enable --now production-ready-rag-parser-docling.service, \
    /usr/bin/systemctl disable --now production-ready-rag-parser-docling.service, \
    /usr/bin/systemctl restart production-ready-rag-parser-docling.service, \
    /usr/bin/journalctl -u production-ready-rag-parser-docling.service -n 1000 --no-pager -o short-iso, \
    ...（配備した前処理 / parser の unit ごとに同じ 4 行）
ragsvc ALL=(root) NOPASSWD: RAG_SERVICE_CONTROL
```

- 起動 = `systemctl enable --now`、停止 = `systemctl disable --now`。利用者が最後に操作した状態を systemd の enable / disable で持ち、
  サーバーの再起動では enable の unit だけが起動する。再配備（`init_script.sh`）では、enable の unit は新しいコードで restart、
  disable の unit は停止のまま、初めての unit は起動する。stack で外したサービスの unit は止めて消す。
- 再起動（`systemctl restart`）は enable / disable を変えない。画面では稼働中・起動中・一部異常のサービスにだけ出す。
- ログは `journalctl` を 1000 行固定で取り、画面が求める行数に切り詰める（sudoers と argv を一致させるため）。
- 失敗の区別: unit が登録されていない → 404、sudoers の許可が無い・systemd が使えない → 503、`systemctl` の失敗 → 502。
  ログが 0 行（`-- No entries --`）は 200 で空の本文。
- 画面から起動 / 停止するには `RAG_SERVICE_CONTROL_ENABLED=true`（stack は true）。false のときは状態の表示だけ。

```bash
systemctl list-units --all 'production-ready-rag-*'
sudo journalctl -u production-ready-rag-parser-docling.service -f
sudo systemctl status production-ready-rag-backend.service
```

規模が大きくなった場合の推奨:

- Frontend / Backend: 同じネイティブ配備（uv の venv + systemd + Nginx）の Compute を増やし、OCI Load Balancer で振り分ける
  （自前のコードの Docker イメージは作らない。#286 / #356）。取込 worker は row lock で二重実行しないので、別の Compute に並べてよい。
- Storage: OCI Object Storage。
- DB: Oracle AI Database（対応バージョンは [terraform/README.md](../../terraform/README.md) の「Autonomous AI Database（全製品で共有）」）。RAG チャンクは `VECTOR(1536, FLOAT32)`。
- LLM/VLM: OCI Enterprise AI。
- Embedding/Rerank: OCI Generative AI。
- Observability: Prometheus、OpenTelemetry、Langfuse gateway。`RAG_TRACE_EXPORT_HTTP_ENDPOINT` を設定すると、脱機密化済み RAG span event を非同期 HTTP JSON で転送する。
- Secret: 共通 `.env`（`platform/.env`、`PLATFORM_*`）と `backend/.env`（`RAG_*`）から読み込む。
- Audit: `app.audit` の `rag_search_audit` / `rag_ingestion_audit` 構造化ログをログ基盤へ転送し、必要に応じて Oracle audit table に永続化する。利用者（production はログインしたユーザー、local は `X-User-ID`）と、local の `X-Tenant-ID` は raw 値を保存せず hash 化し（production は `X-Tenant-ID` を使わない。#225）、`RAG_AUDIT_CONTEXT_HASH_SALT` は `.env` から注入する。

backend は production entrypoint として Gunicorn + `uvicorn.workers.UvicornWorker` を使う（unit の ExecStart）。local 開発だけ `uvicorn app.main:app --reload` を使い、本番では `/api/ready` と golden set gate で昇格判定する。
frontend build は外部 font service に依存せず、CSS の日本語第一 font stack で表示する。これにより CI / build が Google Fonts などへの外向き通信に依存しない。

## リリース前チェック

Pull Request と `main` への push では `.github/workflows/ci.yml` が以下の品質門を実行する。ローカルで先に確認する場合も同じコマンドを使う。

backend:

```bash
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest
uv run bandit -c pyproject.toml -r app
uv run pip-audit
```

frontend:

```bash
cd frontend
npm test
npm run build
```

配備スクリプト（`init_script.sh` と systemd の unit・sudoers）:

```bash
bash rag/scripts/tests/init-script-deployment.test.sh
```

frontend の lint / dependency audit / Playwright E2E は `../nl2sql/` と同じく CI では実行しない。UI/UX を変更した PR では以下をローカルで実行し、結果を PR の `検証結果` に記載する(型検査は `npm run build` の `tsc --noEmit` が CI 上でも走る)。

```bash
cd frontend
npm run lint
npm audit --audit-level=moderate
npm run test:e2e
```

RAG 品質:

```bash
cp evaluation/golden-set.example.json evaluation/golden-set.json
# document id と期待キーワードを対象環境に合わせて編集してから実行する
# 評価は job で動く（#390）。投入して返った job_id で、終わるまで状態を取得する。
curl -X POST http://localhost:8000/api/evaluation/jobs/run \
  -H 'Content-Type: application/json' \
  -d @evaluation/golden-set.json
curl http://localhost:8000/api/evaluation/jobs/<job_id>
```

CI / nightly / staging 昇格では curl ではなく評価 gate CLI を使い、レスポンス JSON を artifact として保存する。

```bash
cd backend
uv run python -m app.rag.evaluation_cli \
  ../evaluation/golden-set.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/evaluation-result.json \
  --trend-output ../evaluation/evaluation-trend.json

uv run python -m app.rag.evaluation_cli \
  ../evaluation/compare.example.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/evaluation-compare-result.json \
  --trend-output ../evaluation/evaluation-compare-trend.json

uv run python -m app.rag.search_load_cli \
  ../evaluation/search-load.example.json \
  --api-base-url https://<staging-host> \
  --output ../evaluation/search-load-result.json \
  --trend-output ../evaluation/search-load-trend.json

uv run python -m app.rag.file_processing_golden_cli \
  ../docs/evaluation/file-processing-golden-set.json \
  --output ../evaluation/file-processing-report.json

uv run python -m app.rag.parser_adapter_contract_cli \
  --output ../evaluation/parser-adapter-compatibility.json

# --strict と file-processing staging の strict は、選んだ adapter(Docling)の parser サービスが要る。
# 動いていなければ、別の端末で `RAG_SERVICES_TORCH=cpu scripts/rag-services.sh run parser-docling`(rag/ で)を起動しておく。
uv run python -m app.rag.parser_adapter_contract_cli \
  --strict \
  --manifest ../docs/evaluation/file-processing-golden-set.json \
  --source-kind pdf \
  --source-kind html \
  --source-kind email \
  --source-kind office \
  --source-kind image \
  --output ../evaluation/parser-adapter-compatibility-strict.json

uv run python -m app.rag.file_processing_staging_cli \
  ../docs/evaluation/file-processing-golden-set.json \
  --output ../evaluation/file-processing-staging-report.json \
  --cleanup \
  --parser-adapter-contract-strict
```

`evaluation/golden-set.example.json`、`evaluation/compare.example.json`、`evaluation/search-load.example.json` は API / CLI request schema に合うテンプレートとしてテストで検証する。運用する `evaluation/golden-set.json` には `thresholds` を含める。複雑文書の case では `expected_content_kind` と `expected_section_paths` を指定し、`content_kind_hit_rate` / `section_coverage` threshold で document-level recall だけでは拾えない表・図・コード・メール・章節 lineage の退化を止める。compare の `experiments[].rag_overrides` では質問の拡張・回答の生成方式・根拠の前後から加える数・rerank の有無・RRF 定数・同じ group から足す child の上限・Oracle vector target accuracy を一時的に上書きし（query expansion・context window などの旧 standard のキーは #591 / #595 で削除した）、staging の golden set で安全に比較できる。評価 CLI は入力 JSON に `experiments` がある場合は compare request とみなし、`--api-base-url` から評価 job の API（compare は `/api/evaluation/jobs/compare`、それ以外は `/api/evaluation/jobs/run`）を自動で選んで投入し、job の完了を待つ（`--api-url` に以前の同期 API の URL を渡しても job の API に読み替える。`app/rag/evaluation_cli.py`）。search load CLI は `cases`、`repeat`、`concurrency`、`thresholds` を受け取り、`/api/search` の client/server p50/p95、error rate、回答フローの工程ごとの p95（`diagnostics.answer.execution_steps` の工程名で集計。#595 で `diagnostics.stream_stage_timings` から変えた）を query / answer 原文なしで artifact 化する。file-processing golden CLI は local parser / chunk / citation 契約に加えて parser fallback、低信頼文書率、失敗 segment 率などの取込品質 metric を検証し、OCI Enterprise AI / Oracle / Object Storage を伴う確認が残る場合は `passed: true` でも `promotion_ready: false` と `promotion_blockers` を artifact に出す。parser adapter compatibility CLI は Docling / Unstructured がインストール済みの環境だけ実 adapter remap smoke を実行し、未導入・未選択 adapter は status として記録する。artifact は source kind、status、parser backend、schema count、source-kind contract reason code だけを含み、抽出本文は保存しない。PDF/image では page lineage、image では bbox/asset lineage、HTML/email/Office では semantic/header/slide/sheet/table lineage も contract として見るため、単に element が 1 件返っただけでは合格にしない。本番昇格判定では `passed` だけでなく `promotion_ready` を必ず確認し、`pending_staging_checks` や `extraction_page_coverage` などの staging 必須 threshold が残る場合は `rag-file-processing-staging` を実行するか、CI で `fail_on_file_processing_pending=true` を指定して失敗扱いにする。file-processing staging CLI は `report.passed` でも promotion blocker が残る場合は exit `1` を返すため、Object Storage artifact cache などの必須 runtime check が skip された環境を CI で止められる。`file-processing-trend` CLI は保存済みの trend baseline と current trend を比較し、table QA / page hit / bbox / preview addressability / fallback rate / ingestion p95 / blocker count の退化を exit `1` で止める。どの CLI も gate 失敗時は exit `1`、入力不備は exit `2` を返す。tenant 分離を検証する評価では `--tenant-id` / `RAG_EVALUATION_TENANT_ID` または `RAG_SEARCH_LOAD_TENANT_ID` を設定する。tenant/user の raw 値は CLI 出力に表示しない。GitHub Actions の `RAG Evaluation Nightly` workflow は `RAG_EVALUATION_API_BASE_URL` repository variable が未設定なら skip し、設定済みなら evaluation result/trend と search-load result/trend を同じ artifact としてアップロードする。search load だけを外す場合は `workflow_dispatch` の `search_load_path` を空文字にする。

`RAG Evaluation Nightly` は API base URL が未設定でも `parser-adapter-compatibility.json` と file-processing artifact を先に作る。adapter の可用性は backend の venv の package ではなく parser サービスの `/health` で判定する(contract CLI と file-processing staging CLI は `RAG_PARSER_READINESS_PROBE_ENABLED` の指定に依らず常に。#343 / #366)。通常実行では parser サービスを起動しないため、選択済み adapter(既定は Docling)が `missing` でも status を記録するだけで gate を失敗にしない(実行環境が見つかった adapter の fallback / schema remap 失敗、選択した adapter の feature flag が OFF の設定矛盾は blocking のまま)が、`run_file_processing_staging=true` かつ `require_real_world_file_processing_manifest=true` の production staging では workflow が strict adapter contract を自動的に有効化する。単独 smoke を厳格化したい場合は `workflow_dispatch` で `parser_adapter_contract_strict=true` を指定して同じ経路を使える。strict では選択済み adapter の parser サービスに到達できる必要があり、未達は blocking failure になる(backend の venv に parser の extra は入れない)。そのため strict のときだけ、workflow は job の中で Docling の parser サービスを起動する(#366): `RAG_SERVICES_TORCH=cpu scripts/rag-services.sh sync parser-docling` で CPU 版 torch の venv を作り、`scripts/rag-services.sh run parser-docling` を背景で動かして `http://127.0.0.1:18020/health` が ok になるまで待ってから gate を実行し、最後に止める。サービスのログは artifact の `parser-docling-service.log` に残す。Docling のモデル(`~/.cache/docling`・`~/.cache/huggingface`)は docling の `uv.lock` を key に `actions/cache` で持ち、uv の cache は setup-uv が backend と docling の `uv.lock` を key に持つ(`uv cache prune --ci` 後)。strict でも Unstructured は選択されない(status は `ignored` で blocking にならない)ため起動しない。ローカルの実測(空のキャッシュ)は、sync とモデルの取得で約 60 秒、`run` の再 sync と起動で約 11 秒、strict gate で約 30〜40 秒(マシンの負荷が高いときは、それぞれ約 160 秒・約 45 秒・約 180 秒)。golden manifest の strict は、Docling の PDF 2 件と画像 1 件の schema remap を検証し、Docling が扱わない Office / HTML / email は `unsupported`(blocking にしない)として記録する。Office / HTML / email の adapter を strict で検証するのは、Unstructured を選んだときの follow-up。strict が有効な場合、workflow は parser adapter contract CLI へ `--manifest ../${file_processing_manifest_path}` と `--strict`、file-processing staging CLI へ `--parser-adapter-contract-strict` を渡す。必要に応じて `parser_adapter_contract_source_kinds=pdf,html,email,office,image` のように対象 source kind を絞る。strict mode は CLI の `--strict` と同じく adapter backend を `auto` 相当にし、Docling / Unstructured の feature flag を有効化した runtime snapshot で、manifest の `fixture_root` と `adapter_schema_remap=true` が付いた `cases[].fixture` を case 単位に実 package へ通して schema remap を検証する。file-processing staging でも同じ strict settings を preflight、実 ingestion/search client、`adapter_contract_coverage` artifact に使うため、runtime が local のままなのに adapter contract だけ合格する状態を避ける。`parser-adapter-compatibility.json` と staging payload の `parser_adapter_contract` は fixture root / fixture file name / case id を hash label に置き換えるため、非機密 real-world manifest を使っても CI artifact から顧客文書名を読めない。`--preflight-only` でも strict 時は同じ manifest fixture contract を実行し、installed/active だけで schema remap 証跡がない adapter を先に止める。production 昇格では synthetic golden manifest だけでなく、`staging_dataset_policy` 付きの非機密 real-world manifest を `file_processing_manifest_path` に指定し、`fixture_kind=real_world`、`data_sensitivity=non_sensitive`、`reviewed_for_public_ci=true`、`staging/` fixture 隔離を manifest validation で通す。workflow の `require_real_world_file_processing_manifest` は既定 true で、staging 実行時に `rag-file-processing-staging --require-real-world-policy` を渡すため、synthetic-only manifest は real OCI / Oracle client 作成前に失敗する。staging payload の `staging_dataset_policy` は manifest 合規件数に加えて `executed_real_world_case_count`、`executed_compliant_real_world_case_count`、`missing_executed_source_kinds`、`missing_executed_scenarios` も返すため、real-world case を宣言しただけで本実行から漏れた場合は promotion blocker になる。`file_processing_trend_baseline_path` / `file_processing_staging_trend_baseline_path` を指定すると、current trend と baseline trend を比較し、`file-processing-trend-regression.json` / `file-processing-staging-trend-regression.json` を artifact に保存する。staging trend 比較では `promotion_ready` だけでなく、adapter contract の scenario set / passed scenario / missing scenario / blocking scenario、backend/source passed pair、backend/scenario passed pair、backend/source/status bad count、backend/source passed count、warning code count、blocking failure reason count、executed real-world case 数、compliant executed case 数、実行済み source kind / scenario 数、missing executed source/scenario、execution error count の退化も blocker にする。package missing、adapter fallback、fixture missing、schema remap empty、trend regression などの blocking failure が残れば workflow を失敗させる。full matrix では Docling/email・Office・HTML のような非 routing 対象 pair は `unsupported` として記録するだけだが(source kind ごとの adapter の候補は、対応形式の正本 `rag_parser_core.capabilities.ADAPTER_CAPABILITIES` が宣言する backend に絞る。Docling は PDF と画像だけ。#366)、CLI で `--strict --backend docling --source-kind email` のように backend/source を明示した場合は虚偽の対応表明として blocking failure にする。

## 運用パラメータ

- `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP`: 通常の構造認識・再帰文字・固定長では、既定の 800 / 120 から評価する。親子階層（small-to-big）はこの 2 つを使わず、`RAG_CHUNK_CHILD_TARGET_CHARS` などの 5 項目で分割する(解析結果が Docling でない文書は構造認識へ縮退し、この 2 つを使う。[rag_poc から移植した機能のガイド](./rag-engine.md#設定一覧))。設定可能範囲は chunk size が 200-32,000 文字、overlap が 0-8,000 文字で、overlap は chunk size 未満にする。
- 見出し単位・ページ単位では、見出し/ページを第一境界として保つため 32,000 / 0 を推奨する。32,000 文字は長大な単位だけを同じ境界内で再分割する安全上限であり、chunk を常に大きくする目標値ではない。[Cohere Rerank 4](https://docs.oracle.com/en-us/iaas/Content/generative-ai/cohere-rerank-4-0.htm) の context は 32,000 token だが、文字数上限と token 上限は同一ではない。
- `RAG_CONTEXT_GROUP_MAX_CHUNKS`: 回答フローが、根拠の child と同じ `chunk_group_id` の兄弟 chunk を文脈（small-to-big）に足す上限（1〜20、既定 4）。根拠の前後から足す child の数は `RAG_NEIGHBOR_CHILD_COUNT`（[rag-engine.md の設定一覧](./rag-engine.md#設定一覧)）。旧 standard の context の設定（`RAG_CONTEXT_WINDOW_CHARS`・`RAG_CONTEXT_DIVERSITY_LAMBDA`・`RAG_CONTEXT_GROUP_EXPANSION_ENABLED`・`RAG_CONTEXT_NEIGHBOR_WINDOW`・`RAG_CONTEXT_COMPRESSION_*`）と `RAG_QUERY_EXPANSION_*`・`RAG_STREAM_REALTIME_ENABLED` は #595 で削除した（上の「既存環境の更新手順（#595）」）。
- `RAG_MIN_SIMILARITY`: recall を落としすぎないよう、評価セットで確認して調整する。
- `RAG_RRF_K`: hybrid retrieval の Reciprocal Rank Fusion 定数。小さいほど上位 rank を強く優先する。golden set で keyword/vector の寄与と citation 安定性を確認して調整する。
- `RAG_EMBEDDING_CACHE_ENABLED` / `RAG_EMBEDDING_CACHE_MAX_ENTRIES` / `RAG_EMBEDDING_BATCH_SIZE`: backend process 内で OCI Generative AI embedding 結果を LRU cache する。cache key は本文そのものではなく、model id、input type、dimension、本文 SHA-256 から作る。batch 内や連続検索で同じ query/chunk が出た場合は miss だけを OCI へ送る。miss は最大 96 件かつ合計 100,000 文字の先に達した単位で OCI embedding request に分割し、返却順を元入力順へ戻す。単一入力は 100,000 文字を超えると拒否し、本文を暗黙に切り詰めない。[Cohere Embed 4](https://docs.oracle.com/en-us/iaas/Content/generative-ai/cohere-embed-4.htm) の 128k token はリクエスト全入力の token 総量であり、この文字数予算とは別の保守的な保護値である。worker 間共有はしないため、容量と batch size は worker 数、メモリ、OCI payload limit、p95 latency を見て調整する。`MAX_ENTRIES=0` は無効化と同じ。
- `RAG_RERANK_CACHE_ENABLED` / `RAG_RERANK_CACHE_MAX_ENTRIES`: backend process 内で OCI Generative AI rerank 結果を LRU cache する。cache key は query/document 原文ではなく SHA-256、model id、top_n、document 順序から作る。候補順や top_n が変わると別 cache entry になる。頻出 FAQ / 評価実行 / 再検索の p95 latency と OCI 呼び出し数を見て調整する。`MAX_ENTRIES=0` は無効化と同じ。
- `RAG_ANSWER_TIMEOUT_SECONDS`: LLM を呼ぶ回答生成（`/api/search`・`/api/search/stream`・チャットの送信・MCP・品質評価の 1 ケース）の通しの timeout（既定 300 秒、上限は LLM 1 回の timeout の上限と同じ 600 秒。#375 / #383）。agentic（検索の計画・multi_hop の再分解）を使う業務ビューでは LLM を最大 3 回呼ぶため、推論型のモデルの p95 latency × 呼び出し回数より長くする。画面は SSE を打ち切らず、`/api/search` の非ストリームは 630 秒、Nginx（`init_script.sh` が生成する回答生成・評価・MCP の `location`）は 660 秒待つ（backend の 504 と理由が画面に届くよう、外側ほど長くする）。
- 品質評価（golden set）は job（`/api/evaluation/jobs/run`・`/jobs/compare`。#390）で動く。1 ケースを `RAG_ANSWER_TIMEOUT_SECONDS` で打ち切り、時間切れになった工程をケースの結果（`error_stage` と `error_message`）に残して次のケースへ進む。job 全体は `RAG_EVALUATION_JOB_TIMEOUT_SECONDS`（既定 3600 秒、60〜86400）で打ち切り、残りのケースは実行せずに失敗（`error_type=EvaluationTimeBudgetExceeded`）として結果を返す。画面は job の状態を 2 秒ごとに取得して進捗（終わったケースの数 / 全体・実行中のケース・経過時間）を出し、取り消しもできる。評価 CLI は job の終わりを `RAG_EVALUATION_TIMEOUT_SECONDS`（既定 3900 秒）まで待ち、超えたら job を取り消す。
  - job は投入を受けた backend のプロセスの中で動き、状態は `rag_evaluation_jobs` に保存する（Gunicorn の別の worker に状態の取得・取り消しが届いても同じ結果になる）。backend の停止・再起動では実行中の job を失敗にし（別のプロセスは引き継がない）、プロセスが落ちて heartbeat が 120 秒途絶えた job も、次の状態の取得で失敗にする。同時に実行できる job は 2 件まで（全プロセスの合計）。終わった job は 7 日で消す（結果は評価 artifact の `rag_evaluation_runs` にも残る）。
  - 以前の同期の `/api/evaluation/run`・`/compare` は、外部から直接呼ぶ利用のために残している。評価全体を 600 秒で打ち切り、Nginx は 660 秒待つ（#383）。
- `RAG_SEARCH_TIMEOUT_SECONDS`（品質評価の 1 ケースの上限、既定 30 秒）は #383 で削除した。品質評価の 1 ケースも `RAG_ANSWER_TIMEOUT_SECONDS` で打ち切る。既存の `rag/backend/.env` に残っていても読まれない（害はない）ので、次に編集するときに消す。
- `RAG_OCI_GUARDRAILS_TIMEOUT_SECONDS`: OCI Guardrails 検査の timeout。既定 5 秒。障害時は `regulated` が fail-closed、その他はローカル検査へ縮退し、非機密 warning と metrics / audit code を残す。
- `RAG_ORACLE_VECTOR_TARGET_ACCURACY`: Oracle AI Vector Search の問い合わせ側 `FETCH APPROX ... WITH TARGET ACCURACY`。既定は 95。staging / golden set で召回率とレイテンシを見ながら調整する。
- `PLATFORM_OCI_ENTERPRISE_AI_ENDPOINT` / `PLATFORM_OCI_ENTERPRISE_AI_LLM_PATH` / `PLATFORM_OCI_ENTERPRISE_AI_VLM_PATH`: OCI Enterprise AI の OpenAI-compatible gateway endpoint と LLM/VLM path。Enterprise AI は `PLATFORM_OCI_ENTERPRISE_AI_API_KEY` による Bearer 認証で呼び出し、staging smoke で VLM/LLM 契約を確認する。Enterprise AI の model deployment / gateway response は `prediction(s)`、`output(s)`、`inference_response`、OpenAI 風 `choices`、JSON 文字列 envelope を正規化してから Pydantic schema / text 抽出へ進める。
- `PLATFORM_OCI_ENTERPRISE_AI_SECONDARY_ENDPOINT` / `_SECONDARY_PROJECT_OCID` / `_SECONDARY_API_KEY`: OCI Enterprise AI のセカンダリ接続（#533。任意。設定するときは 3 つとも必須。#542）。登録モデルごとに接続を選び、回答はテキストのモデルの接続、VLM 抽出・図の読み取り・OCI parser は既定の Vision モデルの接続で呼ぶ。詳細は `platform/README.md` の「OCI Enterprise AI のプライマリ接続・セカンダリ接続・ターシャリ接続」。
- `PLATFORM_OCI_ENTERPRISE_AI_TERTIARY_ENDPOINT` / `_TERTIARY_PROJECT_OCID` / `_TERTIARY_API_KEY`: ターシャリ接続（#786。任意）。OpenAI / OpenAI 互換 API 向けで、設定するときは Endpoint URL と API key が必須、Project OCID は OCI Enterprise AI に使うときだけ入れる。呼び出しは Responses API（`/responses`）。
- `PLATFORM_OCI_ENTERPRISE_AI_VLM_INPUT_MODE`: Enterprise AI VLM への入力搬送方式。`auto` は画像を inline data URL、PDF など非画像を `/files` 経由にする。`files_api` は画像も含めて VLM 入力を明示的に `/files` へアップロードし、`file_id` を `/responses` payload へ渡す。`inline_image` は画像だけ inline で送り、PDF/Office fallback など非画像は設定変更を促して停止する。`API パス` は通常 `/responses` のままにし、`/files` は endpoint から自動生成する。
- `PLATFORM_OCI_ENTERPRISE_AI_LLM_MAX_OUTPUT_TOKENS` / `PLATFORM_OCI_ENTERPRISE_AI_VLM_MAX_OUTPUT_TOKENS`: OpenAI-compatible Responses payload の `max_output_tokens`。既定値は LLM 1200、VLM/OCR 65536。`status=incomplete` / `reason=max_output_tokens` は取込エラーとして利用者に返す。
- `RAG_PDF_SEGMENTATION_ENABLED` / `RAG_PDF_MAX_PAGES_PER_SEGMENT` / `RAG_PDF_MAX_SEGMENTS`: PDF 取込時に元 PDF を page segment へ分けて VLM へ送る。既定は有効、10 ページ/segment、最大 300 segment。segment が `max_output_tokens` で途切れた場合は単ページに分割して再試行する。
- `RAG_PARSER_ADAPTER_BACKEND`: 任意の外部 parser adapter 選択。`local` は本プロジェクト標準 parser のみ、`auto` は有効化済み adapter を source-aware に選ぶ。PDF は `docling` → `unstructured` → `mineru`、画像は `unstructured` → `docling` → `dots_ocr` → `mineru`、Office/HTML は `docling` → `unstructured`、email は `unstructured`、単純 text/markdown/csv/json は local parser を優先する。`docling` / `unstructured` / `mineru` / `dots_ocr` を明示するとその adapter を優先するが、現行 adapter 実装が扱えない source では `*_adapter_source_unsupported` warning を残して標準 parser / Enterprise AI fallback へ戻す。adapter 出力は必ず `StructuredExtraction` / `DocumentElement` / citation metadata へ再マップし、Oracle AI Database、OCI Enterprise AI、OCI Generative AI Cohere の確定スタックは変更しない。
- `RAG_PARSER_DOCLING_ENABLED` / `RAG_PARSER_UNSTRUCTURED_ENABLED`: Docling / Unstructured adapter の feature flag。**外部 parser は services/parsers/<name> の独立サービスへ切り出した**ため、backend の venv と `scripts/start-backend.sh` は parser 依存を同期しない(`uv sync` は lean)。各 parser のバージョンは各サービスの pyproject(`docling==2.103.0` / `unstructured[all-docs]==0.23.1`)で固定する。parser ごとに依存が大きく異なり独立して upgrade したいことが、サービス分離の理由。backend は取込時に各サービスへ HTTP 委譲し、サービス未達なら標準 parser / Enterprise AI fallback へ戻して `*_adapter_service_unreachable` を warning に出す。`RAG_PARSER_<name>_SERVICE_URL` で URL、`RAG_PARSER_SERVICE_TIMEOUT_SECONDS` で timeout、`RAG_PARSER_READINESS_PROBE_ENABLED` で readiness の /health 問い合わせを制御する。設定と導入状態は `GET /api/settings/parser-adapters` と `rag-file-processing-staging --preflight-only` の `parser_adapters` で確認する。schema remap smoke の実行証跡は必要時に `python -m app.rag.parser_adapter_contract_cli` で確認し（画面と API からは #492 で削除）、通常の readiness 取得では重い fixture parse を走らせない。両方の出力には `parser_adapter_scorecard` も含まれ、readiness と file-processing golden/staging 指標から推奨 backend を機械可読に返す。外部 adapter を staging metrics で推奨するには retrieval recall、table QA、page hit、element lineage、fallback rate の中核証拠が必要で、不足時は `adapter_metric_evidence_incomplete` を warning として返し local fallback を優先する。file-processing staging では selected adapter が未導入の場合、`parser_adapter_preflight` を失敗させ、実 OCI / Oracle client 作成前に停止する。明示 adapter が staging 指標で local fallback 未満の場合も `parser_adapter_scorecard_mismatch` を promotion blocker として返す。staging payload の `parser_adapter_source_routes` は `source_kind` ごとの `candidate_order`、`attempted_order`、`active_order`、`selected_backend`、warning を返し、PDF / image / Office / HTML / email / audio / text の routing が CI artifact として監査できる。audio は現時点では転写サービスを有効化していないため `candidate_order=[]`、`selected_backend=local`、`unsupported_audio` / `audio_transcription_not_configured` として明示し、外部 adapter へ誤って流さない。legacy ingestion で `SourceProfile` が欠落していても、parser registry は `audio/*` content-type を同じ unsupported path に固定する。
  - Unstructured adapter は対応する runtime では `include_page_breaks=true`、PDF/画像では `strategy=auto` と `infer_table_structure=true` を要求する。adapter 関数の signature を見て未対応 kwargs は渡さないため、古い Unstructured API でも不要な fallback を増やさない。
  - 外部 adapter の block metadata に `parent_id` / `section_path` / heading level が含まれる場合は `DocumentElement.parent_id` / `section_path` へ再マップする。metadata が不足する場合も title block の reading order から section stack を補完し、citation / chunk lineage を保持する。
  - 外部 adapter が `FigureCaption` / `TableCaption` を parent_id なしで返す場合は、reading order 上の直前 figure / table へ parent-child lineage を補完する。figure caption は同一 chunk の `dependency_edges` へ入り、table caption も `content_kind=table` として filter / citation lineage に残す。
  - 外部 adapter の table `cells` / `table_cells` が row / col / text / bbox / span / confidence を持つ場合は `ExtractionTableCell` へ保持する。caption text より cell structure を優先して chunk text を作るため、table QA と table cell review が flat markdown だけに依存しない。
  - 外部 adapter の `Formula` / `Equation` block は `latex` / `formula` / `mathml` などの metadata から本文を復元し、`DocumentElement(content_kind=equation)` と chunk metadata の `equation_format` に残す。公式 block が `text` を持たない場合でも検索・citation から落とさない。
  - 外部 adapter の bbox は `x/y/width/height`、`x/y/w/h`、`left/top/right/bottom`、`xmin/ymin/xmax/ymax` などを `DocumentElement.bbox` / `ExtractionTableCell.bbox` / `ExtractionAsset.bbox` の `xyxy` へ正規化し、要素 chunk では `bbox_coordinate_mode` / `bbox_unit` も metadata に残す。preview overlay / citation jump / table cell review は adapter 固有の座標 key に依存しない。
  - 外部 adapter の `Image` / `Picture` / `Figure` block は `DocumentElement(content_kind=figure)` だけでなく `ExtractionAsset` にも昇格し、chunk metadata へ `asset_id` を残す。figure citation から asset export / preview audit へ辿れるようにする。
- `GET /api/documents/{document_id}/recipes/{recipe_id}/extraction-export?format=json|markdown|html|chunks`: レシピの保存済み extraction を JSON / Markdown / escaped HTML / chunk view として返す API。`chunks` は embedding を含めず、HTML は原本 HTML を実行せず escaped review source として返す。`download=true` を付けると同じ本文を `Content-Disposition: attachment`（ファイル名は `<文書名>_レシピ<N>.md|.html|.json`、chunk は `<文書名>_レシピ<N>_chunks.json`）で返す（#561）。文書の詳細の「抽出エクスポート」（Markdown / HTML / JSON のダウンロード・コピー）と「Chunk / Citation」（chunk の JSON のダウンロード）、CI artifact、parser adapter 比較の確認に使う。原本再解析や外部 parser の直接呼び出しは行わない。
  - `tables[].cells` がある表は safe `<table>` として再構成し、`data-table-id` / row / col / bbox lineage を保持する。cells がない旧 extraction は escaped `<pre>` に fallback する。
  - `assets[]` は Markdown / HTML 監査 view に `asset_id` / kind / page / bbox / alt text として表示する。HTML export では asset 実体や Object Storage path を埋め込まず、escaped text と `data-asset-id` / `data-kind` / `data-page` / `data-bbox` のみを返す。
  - `DocumentChunkView.metadata` と `RetrievedChunk.metadata` は recursive JSON metadata を保持できる。`element_ids`、`dependency_edges`、table row group、bbox などの lineage は配列/オブジェクトのまま返せるため、chunk preview / citation jump / CI artifact が文字列 split に依存しない。
- `rag-file-processing-staging` の promotion gate は、実測 metrics の合否に加えて中核閾値の弱体化も検査する。`table_qa_accuracy`、`page_hit_accuracy`、`retrieval_recall`、bbox / section / dependency / parser fallback 系の閾値が基準より緩い場合は `promotion_threshold_too_loose` で昇格を止める。
- `rag-file-processing-staging` の metrics は staging gate の実測値に加えて、local contract で証明済みの `parser_routing_accuracy`、`parser_warning_taxonomy_coverage`、`reading_order_consistency`、`table_structure_fidelity`、`visual_chunk_metadata_completeness` などを同じ artifact に統合する。これにより OCI / Oracle が必要な gate と local parser/chunker で十分検証できる gate を分離しつつ、promotion 判定は 1 つの metrics payload で完結する。
- file-processing staging の通常実行 payload には `chunk_template_scorecard` も含まれる。manifest の `expected_chunk_template` と staging/golden metrics を使い、`pdf_layout` / `office_slide` / `office_sheet` / `markdown_by_heading` / `html_semantic` / `email_thread` / `table_preserve_rows` / `ocr_page` などの template 健康度を評価する。`chunk_block_integrity`、`chunk_contextual_coherence`、`chunk_size_compliance` などの core 指標が低い場合は `chunk_template_scorecard_blocked` を promotion blocker として返す。さらに template ごとの expected / measured case count、covered / missing source kinds、covered / missing scenarios を artifact に残し、ある template の未測定を別 template の良好な aggregate 指標で隠さない。
- `RAG_INGESTION_QUEUE_STARTUP_RECOVERY_ENABLED` / `RAG_INGESTION_QUEUE_STARTUP_DRAIN_LIMIT` / `RAG_INGESTION_QUEUE_STALE_RUNNING_SECONDS` / `RAG_INGESTION_QUEUE_WORKER_CONCURRENCY` / `RAG_INGESTION_JOB_MAX_ATTEMPTS`: 永続化済み取込 job の起動時回復、stale RUNNING 判定、同時実行数、最大試行回数を制御する。QUEUED/RUNNING job は `/api/documents/ingestion-jobs/{job_id}/cancel` で `CANCELLED` にできる。job の状態遷移（cancel・完了・失敗）は遷移元の status を条件にした UPDATE で行い、cancel 済み job を `SUCCEEDED` / `FAILED` で上書きせず、完了済み job を `CANCELLED` で上書きしない（後者は 409）。cancel API は job の状態だけを変え、文書・レシピの status は cancel を検知した worker が戻す（レシピの job はレシピ行だけを戻し、文書の status には触れない）。取り消した job の次工程は自動投入しない。`/api/documents/ingestion-jobs/{job_id}/retry` は、レシピの job なら同じレシピの job として再投入し、他のレシピの出力は初期化しない（#305）。実行中の Enterprise AI / Oracle 呼び出しを強制中断するものではないため、外部 timeout と stale recovery も併用する。`RAG_INGESTION_QUEUE_STALE_RUNNING_SECONDS` は heartbeat の無い `RUNNING` 行だけに使う（heartbeat のある行は下の lease の TTL。#357）。取込中に Oracle の接続が一時的に切れた場合（python-oracledb の `is_session_dead` / `isrecoverable`。`DPY-4011` など）は、`attempt_count < RAG_INGESTION_JOB_MAX_ATTEMPTS` なら文書・レシピの status を工程の前に戻して job を `QUEUED` に戻し、再実行する（使い切ったら `FAILED`）。DB の transaction も、commit の前に接続が切れた場合だけ新しい接続で 1 回やり直す。ログ（`ingestion_job_failed` / `ingestion_job_requeued_transient_db_error` / `oracle_transaction_retry` / `oracle_rollback_failed`）には `oracle_error_code` と `full_code` を出す（#341）。
- `RAG_INGESTION_QUEUE_HEARTBEAT_INTERVAL_SECONDS` / `RAG_INGESTION_QUEUE_LEASE_TTL_SECONDS` / `RAG_INGESTION_QUEUE_SHUTDOWN_GRACE_SECONDS`（取込 worker の lease と停止。#357）:
  - worker はプロセスごとに一意の識別子（`host:pid:乱数`）を持ち、job の claim で lease を取る（`rag_ingestion_jobs.lease_owner` / `heartbeat_at`）。
    実行中は `HEARTBEAT_INTERVAL_SECONDS`（既定 15 秒）ごとに、自分の lease の `RUNNING` job の `heartbeat_at` を更新する。heartbeat は親の worker が打つ（process isolation の子は打たない）。
  - stale の回復（worker の起動時と、アイドル時に `RAG_INGESTION_QUEUE_RECOVERY_INTERVAL_SECONDS` ごと）は、heartbeat が `LEASE_TTL_SECONDS`（既定 90 秒。間隔の 3 倍以上）を超えて途絶えた job だけを戻す。
    自分の lease の job は戻さない。`heartbeat_at` の無い行（lease 導入前の行）は従来どおり `RAG_INGESTION_QUEUE_STALE_RUNNING_SECONDS` で判定する。job の長さの上限は job の timeout（`RAG_INGESTION_JOB_SUBPROCESS_TIMEOUT_SECONDS`）だけが持つ。
    戻す UPDATE にも同じ条件を入れるため、回復の途中で heartbeat が打たれた job や完了した job は戻さない。
    heartbeat の時刻（claim の時点を含む）と TTL の判定は DB の時計（`SYSTIMESTAMP`）で行うため、worker を別の host で動かしても host の時計のずれに左右されない（#359）。
  - 完了・失敗・一時的な DB エラーでの再キューと、取り消しの後始末（文書・レシピの status の戻し）は、自分の lease の job のときだけ行う（#359）。
    heartbeat が TTL を超えて途絶え、別の worker が claim し直した後の古い実行の結果は捨て、文書・レシピも変えずに `ingestion_job_stale_result_discarded`（`job_id` / `lease_owner` / `discarded_status` / `current_status` / `current_lease_owner`）を warning で出す。
    古い実行は、工程の途中の取り消しの確認でも lease を失ったことを検知して止まる。
  - dispatch は、同じ文書の別の job が `RUNNING` の `QUEUED` job（claim できない）と、自分が実行中の job を除いて古い順に取る。先頭の claim できない job で後ろの job が止まらない。
  - 停止（SIGTERM / SIGINT）では新しい job を取らず、実行中の job の完了を `SHUTDOWN_GRACE_SECONDS`（既定 60 秒）まで待つ。終わらなければ子プロセスを止め（SIGTERM、10 秒で SIGKILL）、自分の lease の `RUNNING` job を `QUEUED` に戻す。
    claim で増やした attempt は戻し（停止は job の失敗ではない）、文書・レシピの status は stale の回復と同じく工程の前へ戻す（レシピの job はレシピ行だけ）。子の runner（`app.rag.ingestion_job_runner`）も SIGTERM で job の実行を取り消し、後始末をして終了コード 143 で終わる。
  - API プロセス内の worker（`INPROCESS_WORKER_ENABLED=true`。ローカル開発の `uvicorn --reload` など）は停止を待たせないため grace 0 で、実行中の job の子をすぐ止めて `QUEUED` に戻す。
  - systemd の worker unit は `KillMode=mixed`（SIGTERM は main process だけに送り、子は worker が止める）で、`TimeoutStopSec`（90 秒）を grace（unit で 60 秒に固定）+ 子の停止待ち 10 秒 + 戻す DB の処理より長くしている。grace を変えるときは `init_script.sh` の `WORKER_SHUTDOWN_GRACE_SECONDS` / `WORKER_TIMEOUT_STOP_SEC` を一緒に変える（テストで照合する）。
  - worker が SIGKILL などで後始末できずに止まった場合は、lease の TTL 後に他の（または再起動した）worker が回復する。
- `RAG_INGESTION_QUEUE_DEDICATED_WORKER_ENABLED` / `RAG_INGESTION_QUEUE_INPROCESS_WORKER_ENABLED` / `RAG_INGESTION_QUEUE_POLL_INTERVAL_SECONDS`: 取込実行を API の event loop から切り離す専用ワーカー機構。`DEDICATED_WORKER_ENABLED=false`(既定)では従来どおりリクエスト後のバックグラウンドタスクで取込を実行する。`true` にすると API はキュー投入のみ行い、`app.rag.ingestion_worker.IngestionQueueWorker` がキュー(`rag_ingestion_jobs`)を `claim_ingestion_job` の row lock 付きで消費する。`INPROCESS_WORKER_ENABLED=true`(既定)なら同じ API プロセスの lifespan 内でワーカーを起動するため単一プロセスでも完結する。**ただし in-process ワーカーは Gunicorn worker プロセスごとに 1 つ起動するため、`WEB_CONCURRENCY>1` だと実効同時取込数が `WEB_CONCURRENCY × RAG_INGESTION_QUEUE_WORKER_CONCURRENCY` まで増え、OCI/Oracle を過負荷にし得る**(row lock で二重実行はしないが総並行数が乗算される)。in-process ワーカーを使う場合は `WEB_CONCURRENCY=1` にするか、API では `INPROCESS_WORKER_ENABLED=false` にして取込を別プロセスへ切り出すこと。別プロセスへ切り出す場合は `python -m app.rag.ingestion_worker` を別途起動する(本番は systemd の `production-ready-rag-ingestion-worker.service`)。起動時には `ingestion_inprocess_worker_enabled` warning ログで多重化の注意を出す。重い解析・PDF 分割・base64・チャンク・graph index・埋め込みなどの CPU/同期処理は取込・検索とも `asyncio.to_thread` でワーカースレッドへ退避し、event loop を塞がない。ワーカーは複数同時起動しても row lock により同一 job の二重実行が起きないため、worker のプロセスを増やして水平スケールできる。`POLL_INTERVAL_SECONDS` は QUEUED ジョブのポーリング間隔で、同一プロセス内の enqueue は即時起床通知で待たずに拾う。
- `PLATFORM_OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` / `PLATFORM_OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE`: Enterprise AI gateway ごとの request shape が標準 payload と異なる場合にだけ設定する JSON object template。文字列 placeholder は `${prompt}` / `${context}` / `${mime_type}` / `${data_base64}`、object placeholder は `"${messages}"` / `"${parameters}"` / `"${response_format}"` / `"${structured_extraction_schema}"` のように完全な文字列値として置く。未設定なら標準 payload を使い、VLM には upload metadata の MIME type を渡す。
- `PLATFORM_OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH` / `PLATFORM_OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH`: Enterprise AI gateway の response が既知 envelope ではなく独自の深い JSON 構造に包まれる場合だけ指定する JSON Pointer。例: `/payload/results/0/generated/text`、`/payload/results/0/document`。未設定なら既知 envelope を自動判定する。
- backend の Gunicorn の worker 数・timeout: `init_script.sh` が作る unit（`production-ready-rag-backend.service`）の
  `--workers 2 --timeout 60 --graceful-timeout 30 --keep-alive 5`。worker 数は Compute の CPU、OCI / Oracle の p95 latency、
  同時実行数から決める。Uvicorn の worker では Gunicorn の `--timeout` は worker の生存確認で、1 件の request の長さ（`RAG_ANSWER_TIMEOUT_SECONDS` まで）を打ち切らない。
- `RAG_TRACE_EXPORT_HTTP_ENDPOINT`: 空なら構造化ログ + Prometheus のみ。設定時は `rag.trace_span` event を OpenTelemetry / Langfuse gateway へ非同期 POST する。query 本文、context 本文、OCR 原文、prompt、例外 message は送らない。
- `RAG_TRACE_EXPORT_HTTP_BEARER_TOKEN` / `RAG_TRACE_EXPORT_TIMEOUT_SECONDS` / `RAG_TRACE_EXPORT_QUEUE_SIZE`: trace export の認証、送信 timeout、queue 上限。queue full や送信失敗は `rag_trace_export_dropped` / `rag_trace_export_failed` として記録し、RAG request は失敗させない。
- `RAG_GUARDRAIL_MAX_QUERY_CHARS`: UI 側の入力制限と合わせる。
- `RAG_RATE_LIMIT_ENABLED`: 高コスト API の app 内 limiter。外部 API Gateway / Ingress limiter と併用できる。
- `RAG_RATE_LIMIT_WINDOW_SECONDS`: fixed-window limiter の窓幅。
- `RAG_RATE_LIMIT_SEARCH_REQUESTS` / `RAG_RATE_LIMIT_EVALUATION_RUNS` / `RAG_RATE_LIMIT_UPLOADS` / `RAG_RATE_LIMIT_INGEST_REQUESTS`: tenant/user hash 単位の窓内上限。OCI / Oracle / LLM の quota と業務ピークに合わせて調整する。
- `X-RAG-Allowed-Document-Ids` / `X-RAG-Allowed-Category-Names`: `RAG_AUTH_MODE=local` のときだけ使う、認証ゲートウェイまたはアプリケーション権限層が認可済み scope として backend へ付与する request header（`production` ではログインした利用者の権限から範囲を決め、この header は使わない。#214）。backend は raw 値を監査ログへ出さず、document 一覧、詳細、chunk count、retrieval に deny-by-default の scope filter として適用する。外部クライアントから直接信頼しない。
- `RAG_GUARDRAIL_MASK_SENSITIVE_IDENTIFIERS`: query / answer 内の個人番号、口座番号、電話番号、メールアドレスらしき値を `[機微情報]` にマスクする。外部 DLP と責務分担する場合だけ無効化を検討する。

安全チェックを含むリリースは、コード展開後に `uv run python -m app.rag.chat_history_sanitization --dry-run --format json` を実行し、Oracle バックアップを取得してから `--apply` する。適用後は `rag_guardrail_findings_total` の `guardrail_backend_unavailable` / `prompt_injection` / `low_groundedness` と阻止率を監視する。CLI は冪等で、原文の別バックアップをアプリ DB 内には作成しない。

## OCI へ切り替えるときの順序

1. Oracle AI Database に document / chunk / audit tables を作成する。まず backend の venv（`rag/backend`）または CI runner で `uv run python -m app.rag.oracle_schema --output ../artifacts/oracle-schema.sql --manifest-output ../artifacts/oracle-schema.manifest.json` を実行し、DDL 成果物と manifest の hash / statement 数をレビューする。既存 DB を現行 DDL 契約へ寄せる場合は `uv run python -m app.rag.oracle_schema --migration --output ../artifacts/oracle-schema-migration.sql --manifest-output ../artifacts/oracle-schema-migration.manifest.json` を実行し、migration artifact をレビューして適用する。V3 の構築 artifact(`rag_chunk_sets`、`rag_document_extractions`、`rag_artifact_layers`、`rag_kb_chunk_set_bindings`、`rag_chunks.chunk_set_id`)を既存データへ反映する場合は、あわせて `uv run python -m app.rag.variant_backfill_cli --format sql --checks-only --output ../artifacts/variant-backfill-checks.sql` と `uv run python -m app.rag.variant_backfill_cli --format json --output ../artifacts/variant-backfill.manifest.json` を生成し、[oracle-variant-backfill-runbook.md](./oracle-variant-backfill-runbook.md) の acceptance を staging artifact として保存する。生成 SQL は document table に `content_sha256`、`file_size_bytes`、`duplicate_of_document_id`、`tenant_id_hash` を含め、`content_sha256` と `tenant_id_hash, status, uploaded_at` に索引を作る。chunk table は `VECTOR(1536, FLOAT32)`、HNSW ベクトル索引(`COSINE`、目標精度 `95`、neighbors `32`、efconstruction `500`)、`tenant_id_hash`、`document_id + chunk_index`、`chunk_set_id + chunk_index` 用索引を含め、retrieval で tenant 条件、request access scope 条件、KB serving chunk_set 条件を必ず適用できるようにする。audit table は query 本文、OCR 原文、tenant/user id の raw 値を保存せず、hash、request id、trace id、guardrail code、検索件数・citation 件数、設定 fingerprint、error type を保存する（旧 standard の検索・context の内訳の列は #595 以降は既定値。既存の監査の行を変えないため列は残す。#596）。レビュー済み SQL を SQLcl や管理された migration 手順で適用してから次へ進む。

2. `ObjectStorageClient` の OCI Object Storage SDK 実装を有効化する。`PLATFORM_OBJECT_STORAGE_REGION` / `PLATFORM_OBJECT_STORAGE_NAMESPACE` / `PLATFORM_OBJECT_STORAGE_BUCKET` を設定し、保存 URI が `oci://namespace/bucket/key` になり、取得時に namespace / bucket 不一致を拒否することを staging で確認する。取込前に Object Storage から取得した bytes が document table の `file_size_bytes` / `content_sha256` と一致することも確認する。
3. `OracleClient` の python-oracledb pool、vector search、keyword search、document/chunk persistence、同じ chunk group の兄弟 chunk の取得を有効化する。`PLATFORM_ORACLE_USER` / `PLATFORM_ORACLE_DSN` / `PLATFORM_ORACLE_PASSWORD` または `PLATFORM_ORACLE_WALLET_DIR`（Thick mode では `PLATFORM_ORACLE_CLIENT_LIB_DIR/network/admin`）に配置した wallet を設定する。`INGESTING` / `ERROR` への状態遷移では該当 document の chunk/index 行と古い抽出結果を削除し、検索対象は `INDEXED` に限定する。staging では `VECTOR_DISTANCE`、`FETCH APPROX ... WITH TARGET ACCURACY`、Oracle Text `CONTAINS`、document 別 chunk count、`chunk_index` window による同一 document 前後 chunk 取得、`INDEXED` 文書が hybrid search の citation に含まれることを確認する。
4. `OciGenAiClient` の embedding / rerank は OCI Generative AI Inference SDK 実装を使う。staging では `PLATFORM_OCI_CONFIG_FILE` / profile / region / compartment / model id、Cohere Embed v4 の 1536 次元、Cohere Rerank v4 fast の返却件数・候補 index 範囲・index 重複なし・finite score を確認する。
5. `OciEnterpriseAiClient` は `PLATFORM_OCI_ENTERPRISE_AI_ENDPOINT`、`PLATFORM_OCI_ENTERPRISE_AI_API_KEY`、既定のテキストモデル・既定の Vision モデル（画面の「モデル」、または `PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_TEXT_MODEL`・`PLATFORM_OCI_ENTERPRISE_AI_DEFAULT_VISION_MODEL`。#499）、`PLATFORM_OCI_ENTERPRISE_AI_LLM_PATH`、`PLATFORM_OCI_ENTERPRISE_AI_VLM_PATH` を使って Enterprise AI endpoint を呼び出す。標準 payload で合わない model deployment / gateway は `PLATFORM_OCI_ENTERPRISE_AI_LLM_PAYLOAD_TEMPLATE` / `PLATFORM_OCI_ENTERPRISE_AI_VLM_PAYLOAD_TEMPLATE` で request shape を差し替え、response envelope が独自の場合は `PLATFORM_OCI_ENTERPRISE_AI_LLM_RESPONSE_PATH` / `PLATFORM_OCI_ENTERPRISE_AI_VLM_RESPONSE_PATH` で候補 node を指定する。staging ではまず `uv run python -m app.rag.enterprise_ai_probe --surface both --dry-run` で URL、template 使用有無、response path 使用有無、payload key / shape、JSON byte 数を確認し、その後 `uv run python -m app.rag.enterprise_ai_probe --surface both` で LLM/VLM を直接呼び出す。probe は回答本文や OCR 本文を出さず、text 文字数・element 件数だけを artifact に残す。ここで Bearer 認証、timeout/retry、VLM の MIME type / 構造化抽出 JSON schema、LLM の citation-grounded 生成 payload、response parsing を実 endpoint で確認する。VLM response は `StructuredExtraction` へ検証し、LLM response は空 text を fail fast する。
6. staging にデプロイし、`/api/ready` の checks がすべて `ok` になることを確認する。production 昇格時は `RAG_ENVIRONMENT=production` にして、追加 checks の `audit_context_salt` も `ok` にする。Oracle は `PLATFORM_ORACLE_USER` / `PLATFORM_ORACLE_DSN` に加えて `PLATFORM_ORACLE_PASSWORD` または `PLATFORM_ORACLE_WALLET_DIR`（Thick mode では `PLATFORM_ORACLE_CLIENT_LIB_DIR/network/admin`）に存在する Wallet が必要。
7. staging 環境でまず `uv run python -m app.rag.staging_smoke --preflight-only` を実行する。OCI/Oracle 接続設定、Enterprise AI LLM/VLM 設定、Cohere embedding/rerank 設定、`PLATFORM_UPLOAD_STORAGE_BACKEND=oci`、Object Storage namespace/bucket がそろっていることを JSON の `checks` で確認する。preflight 失敗時は外部依存へ接続せず、secret 値も出力しない。
8. preflight が `ok=true` なら `uv run python -m app.rag.staging_smoke` を実行する。Object Storage put/get、Oracle document 作成、Enterprise AI VLM、chunking、embedding、Oracle indexing、hybrid search、Enterprise AI LLM 生成を 1 回通し、作成した smoke document が citation に含まれることを確認する。既定 query は一意な `SMOKE-...` marker の原文引用を要求し、検索は新規 `document_id` に限定される。既定 query では LLM 回答にも marker が含まれない場合に `stage=rag_answer_marker` で失敗する。JSON 出力の `ok`、`marker`、`query`、`answer_contains_marker`、`trace_id`、`chunk_count`、`citation_count`、`cleanup`、`diagnostics`（`config_fingerprint`、回答フローの実行記録 `diagnostics.answer.execution_steps` など）を保存し、staging gate の artifact にする。既定では evidence として作成物を保持し、`cleanup` は `skipped` になる。DB/Object Storage を汚したくない一時確認では `uv run python -m app.rag.staging_smoke --cleanup` を使い、成功・失敗どちらでも作成済み Oracle document/chunk と Object Storage object の削除 status を確認する。失敗時は preflight の `checks` または本実行の `stage` / `cause_type` を見て、Object Storage、Oracle、取込（VLM・chunking・embedding・索引）、検索と回答（`rag_search`）、回答の確認（`rag_answer_marker`）のどこで止まったかを切り分ける。回答フローの中の工程は `diagnostics.answer.execution_steps` で確かめる。query を変える場合は `--query "確認用キーワード {marker} を要約してください"` のように `{marker}` placeholder を残す。
9. golden set 評価と負荷試験を通してから production へ昇格する。
