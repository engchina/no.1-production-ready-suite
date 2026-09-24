# Agent Runtime Production Validation

この runbook は、ローカル CI では完了できない外部環境依存の検証を release evidence として残すための
手順です。secret / token / DB password は出力に含めず、各 script の JSON summary と実行日時だけを記録します。

## 0. One-Shot Evidence Collector

全項目を 1 つの evidence JSON にまとめる場合は collector を使います。まず dry-run で設定解決だけを確認します。
ローカル / self-hosted runner では wrapper script も使えます。

```bash
scripts/validate-production-evidence.sh
```

evidence → review scaffold → release gate → bundle manifest の形だけをローカルで予行する場合は rehearsal script を
使います。これは dry-run evidence に `--allow-dry-run` を明示して通す **予行専用** の流れであり、release
approval の代替にはしません。

```bash
scripts/rehearse-production-release-chain.sh
```

`scripts/check-all.sh` は標準チェックの一部として軽量 rehearsal を実行します。ローカルで一時的に省略する場合は
`SKIP_RELEASE_REHEARSAL=1` を指定します。

live evidence を収集する場合:

```bash
VALIDATION_MODE=live \
VALIDATION_ENVIRONMENT=production \
VALIDATION_ROTATION_INTERVAL_SECONDS=300 \
scripts/validate-production-evidence.sh
```

live mode の wrapper はまず `backend/validation-runner-readiness.<environment>.json` を保存し、その後
`backend/validation-preflight.<environment>.json` を保存します。runner readiness / preflight のどちらかが
失敗した場合は collector / validator へ進まず終了します。
runner readiness JSON は required secret group、container runtime、entrypoint、validation manifest、archive
root、OCI Object Storage upload 前提を確認します。`VALIDATION_OCI_UPLOAD_EXECUTE=1` を使う場合は
`VALIDATION_OCI_RETENTION_CONFIRMED=1` も指定し、bucket 側の retention / immutable 設定確認を明示してください。
preflight JSON の `release_chain` には、required secret group、runner/container runtime、release artifact
target、entrypoint script、validation manifest の readiness がまとまります。`release_chain.ready=true` が
live evidence 収集前の目安です。secret の値は出力せず、missing variable / missing group 名だけを表示します。
`--summary-markdown` を指定すると同じ内容を人間向けの表として出力し、GitHub workflow summary ではこの Markdown
を優先表示します。

GitHub Actions では `Production Validation Evidence` workflow を手動実行できます。実行前に repository /
environment secrets として Oracle、IdP、MCP gateway の接続情報を登録し、Oracle / IdP / MCP / container
runtime に到達できる runner を `runner` input に指定します。job は input の GitHub Environment に紐づくため、
`production` environment には reviewer / approval rule を設定してください。workflow は live collector を実行し、
collector の前に `agent_runtime_runner_readiness_check.py` と `agent_runtime_validation_preflight.py` で required env、
container runtime command、release chain entrypoint、archive / upload 前提を確認します。
その後 `agent_runtime_validate_evidence.py` で release gate を通してから JSON / Markdown evidence artifact と
検証に使った manifest artifact を 14 日 retention でアップロードし、runner readiness、preflight、evidence
summary を workflow summary に表示します。
`review_json_path` input を指定した場合は、validator 通過後に `agent_runtime_release_gate_check.py` と
`agent_runtime_build_release_bundle.py` も実行します。`review_json_path` / `bundle_json_path` の相対パスは
repository root から解決し、`bundle_json_path` を空にすると `backend/validation-bundle.<environment>.json`
へ出力します。

Required GitHub secrets:

Env / secret 名の一覧は
[`docs/agent-runtime-production-validation.env.example`](./agent-runtime-production-validation.env.example)
にもまとめています。実値は secret manager / GitHub Environment secrets に登録し、この example file には書き込まないでください。
release gate の機械可読な完了条件は
[`docs/agent-runtime-production-validation.manifest.json`](./agent-runtime-production-validation.manifest.json)
に固定しています。

| Secret | Purpose |
|---|---|
| `AGENT_RUNTIME_ORACLE_DSN` | Oracle runtime store DSN |
| `AGENT_RUNTIME_ORACLE_USER` | Oracle validation user |
| `AGENT_RUNTIME_ORACLE_PASSWORD` | Oracle validation password |
| `AGENT_RUNTIME_BASE_URL` | Deployed Agent Runtime base URL |
| `AGENT_RBAC_JWT_JWKS_URL` | IdP JWKS endpoint |
| `AGENT_RBAC_JWT_SAMPLE_TOKEN` | Sample JWT for backend status/run probe |
| `AGENT_EXTERNAL_MCP_BASE_URL` | MCP JSON-RPC gateway URL |
| `AGENT_EXTERNAL_MCP_OAUTH_TOKEN_URL` | MCP OAuth token endpoint |
| `AGENT_EXTERNAL_MCP_OAUTH_CLIENT_ID` | MCP OAuth client id |
| `AGENT_EXTERNAL_MCP_OAUTH_CLIENT_SECRET` | MCP OAuth client secret |

Optional GitHub secrets:

| Secret | Purpose |
|---|---|
| `AGENT_RBAC_POLICY_URL` | External RBAC policy service probe |
| `AGENT_RBAC_POLICY_API_KEY` | External RBAC policy service bearer |
| `AGENT_EXTERNAL_MCP_API_KEY` | MCP fallback bearer when OAuth is not used |
| `AGENT_EXTERNAL_MCP_SESSION_ID` | MCP streamable HTTP session continuity |
| `AGENT_EXTERNAL_MCP_OAUTH_SCOPE` | OAuth client credentials scope |

Runner requirements:

- Can connect to Oracle, deployed Agent Runtime, IdP JWKS, OAuth token endpoint, and MCP gateway.
- Has Python 3.12 and `uv`.
- Has the configured container runtime (`docker` or `podman`) in `PATH`.
- Can create/write the configured `archive_root` when local archive staging is enabled.
- Has OCI SDK / OCI auth only when `execute_upload=true`; dry-run upload manifest generation does not require OCI SDK.
- For sandbox evidence, runtime should be rootless, seccomp-capable, `no-new-privileges` capable, and able to run
  `busybox:latest` or the selected smoke image.

```bash
cd backend
uv run python scripts/agent_runtime_collect_validation_evidence.py \
  --mode dry-run \
  --environment staging \
  --validator "$USER" \
  --rotation-interval-seconds 0
```

実環境では必要な env / secret を設定してから live mode を実行します。

```bash
uv run python scripts/agent_runtime_runner_readiness_check.py \
  --environment production \
  --container-runtime docker \
  --manifest ../docs/agent-runtime-production-validation.manifest.json \
  --output validation-runner-readiness.production.json \
  --summary-markdown validation-runner-readiness.production.md
```

```bash
uv run python scripts/agent_runtime_validation_preflight.py \
  --environment production \
  --container-runtime docker \
  --summary-markdown validation-preflight.production.md
```

```bash
cd backend
uv run python scripts/agent_runtime_collect_validation_evidence.py \
  --mode live \
  --environment production \
  --validator "$USER" \
  --rotation-interval-seconds 300 \
  --output validation-evidence.production.json \
  --summary-markdown validation-evidence.production.md
```

release gate では validator を通します。既定では `mode=live` の evidence だけを合格にします。
validator は evidence JSON 内の Bearer token、JWT、password / secret / token / api key 形式の文字列も
検出し、見つかった場合は値を出力せず path と rule 名だけを返して fail します。
bundle builder / archiver も、release record に含める runner readiness、preflight、evidence summary、
review、manifest などの artifact 本文を再スキャンし、secret-like な内容が混入した artifact を archive
しません。

```bash
uv run python scripts/agent_runtime_validate_evidence.py \
  validation-evidence.production.json \
  --manifest ../docs/agent-runtime-production-validation.manifest.json
```

dry-run rehearsal の schema contract だけを確認する場合は明示的に許可します。

```bash
uv run python scripts/agent_runtime_validate_evidence.py \
  validation-evidence.production.json \
  --manifest ../docs/agent-runtime-production-validation.manifest.json \
  --allow-dry-run
```

## 0.1 Release Review Gate

live evidence が validator を通った後は、reviewer が artifact を確認し、`validation-review.<environment>.json`
を作成します。雛形は
[`docs/agent-runtime-validation-review.example.json`](./agent-runtime-validation-review.example.json)
です。`evidence_sha256` と `manifest_sha256` は、release 対象の artifact が差し替わっていないことを
機械的に確認するため必須です。

まず scaffold script で hash と checklist key を自動入力します。既定では安全のため
`decision=pending_review`、checklist はすべて `false` です。

```bash
cd backend
uv run python scripts/agent_runtime_scaffold_release_review.py \
  validation-evidence.production.json \
  --manifest ../docs/agent-runtime-production-validation.manifest.json \
  --environment production \
  --reviewer "$USER" \
  --output validation-review.production.json
```

reviewer が artifact を確認し、承認する場合だけ明示的に approved review を生成または編集します。

```bash
uv run python scripts/agent_runtime_scaffold_release_review.py \
  validation-evidence.production.json \
  --manifest ../docs/agent-runtime-production-validation.manifest.json \
  --environment production \
  --reviewer "$USER" \
  --decision approved \
  --mark-checklist-complete \
  --output validation-review.production.json
```

review JSON を保存したら、release review gate を実行します。

```bash
uv run python scripts/agent_runtime_release_gate_check.py \
  validation-evidence.production.json \
  validation-review.production.json \
  --manifest ../docs/agent-runtime-production-validation.manifest.json \
  --environment production
```

この gate は evidence validator を再実行し、review JSON の `decision=approved`、reviewer、
reviewed_at、environment、sha256、必須 checklist を検証します。dry-run evidence、hash mismatch、
未承認 decision、未完了 checklist は release 不可として fail します。
必須 checklist には runner readiness の確認(`runner_readiness_accepted`)も含めます。
`--allow-dry-run` は `scripts/rehearse-production-release-chain.sh` からの予行専用です。production release では
指定しません。

release gate が通ったら、監査用の bundle manifest を生成します。bundle manifest は runner readiness /
preflight / evidence / Markdown summary / review / validation manifest の `sha256` と size を 1 つに固め、
`bundle_content_sha256`
を残します。生成時には preflight JSON の `ok=true` と `release_chain.ready=true`、artifact 本文
secret scan も再検証します
(dry-run rehearsal の synthetic preflight は `--allow-dry-run` 指定時のみ許可)。`--bundle-summary-markdown`
を指定すると、最終証跡の hash 表を Markdown として保存できます。
長期保存先は bundle 内の `archive_policy` にも記録します。GitHub Actions artifact retention は短期確認用
の 14 日なので、release owner は bundle JSON / Markdown と全 required artifact を `archive_policy` の
`archive_location` へコピーし、少なくとも `retention_days` 期間は immutable な release record として
保管してください。

```bash
uv run python scripts/agent_runtime_build_release_bundle.py \
  validation-evidence.production.json \
  validation-review.production.json \
  --runner-readiness validation-runner-readiness.production.json \
  --preflight validation-preflight.production.json \
  --summary-markdown validation-evidence.production.md \
  --manifest ../docs/agent-runtime-production-validation.manifest.json \
  --environment production \
  --output validation-bundle.production.json \
  --bundle-summary-markdown validation-bundle.production.md \
  --retention-days 365 \
  --archive-location release-record/evidence-bundle \
  --archive-owner "$USER"
```

生成済み bundle を長期保存先へ staging する場合は、archiver で bundle と required artifacts の hash を
再検証してから archive directory を作成します。`archive-record.json` には bundle hash、コピー先、全 artifact
hash、`archive_policy` が残ります。`--archive-summary-markdown` を指定すると workflow summary 用の
Markdown も保存し、archive directory 内にも `archive-record.md` を残します。

```bash
uv run python scripts/agent_runtime_archive_release_bundle.py \
  validation-bundle.production.json \
  --bundle-summary-markdown validation-bundle.production.md \
  --archive-root /mnt/release-record/agent-runtime \
  --output validation-archive.production.json \
  --archive-summary-markdown validation-archive.production.md \
  --generated-by "$USER"
```

archive directory を長期保存先へ移動または upload する前に、offline verifier で `archive-record.json` と
`artifacts/` 配下の copied artifact を再度 hash 検証します。この verifier は `archive_name` から相対解決するため、
archive directory を別 mount / 別ホストへ移動した後でも同じコマンドで復元検証できます。

```bash
uv run python scripts/agent_runtime_verify_release_archive_dir.py \
  validation-archive.production.json \
  --environment production \
  --output validation-archive-dir.production.json \
  --summary-markdown validation-archive-dir.production.md
```

OCI Object Storage へ保存する場合は、上記 archive directory を immutable bucket / retention rule 付き bucket
へアップロードします。まず upload manifest を dry-run 生成し、object name と hash を確認します。

```bash
uv run python scripts/agent_runtime_upload_release_archive.py \
  validation-archive.production.json \
  --bucket-name <release-record-bucket> \
  --namespace <object-storage-namespace> \
  --object-prefix agent-runtime/release-archives \
  --output validation-upload.production.json \
  --upload-summary-markdown validation-upload.production.md
```

実アップロードは OCI SDK と runner の OCI 認証設定が揃い、bucket の immutable retention を確認済みの場合のみ、
同じコマンドに `--execute-upload --retention-confirmed` を追加して実行します。SDK が無い runner でも
dry-run upload manifest までは生成できます。

最後に chain verifier で preflight / evidence / review / bundle / archive / upload の相互参照と hash を
一括検証します。個別 gate が成功していても、後からファイルが差し替わった場合はここで fail します。

```bash
uv run python scripts/agent_runtime_verify_release_chain.py \
  --environment production \
  --runner-readiness validation-runner-readiness.production.json \
  --preflight validation-preflight.production.json \
  --evidence validation-evidence.production.json \
  --evidence-summary validation-evidence.production.md \
  --review validation-review.production.json \
  --bundle validation-bundle.production.json \
  --bundle-summary validation-bundle.production.md \
  --archive-record validation-archive.production.json \
  --archive-dir-verification validation-archive-dir.production.json \
  --upload-manifest validation-upload.production.json \
  --manifest ../docs/agent-runtime-production-validation.manifest.json \
  --output validation-chain.production.json \
  --summary-markdown validation-chain.production.md \
  --require-archive \
  --require-archive-dir-verification \
  --require-upload
```

ローカル wrapper で evidence 収集後に同じ gate まで一気通貫で実行する場合は、review JSON の path を渡します。
この場合、release gate 通過後に `backend/validation-bundle.<environment>.json` も自動生成されます。

```bash
VALIDATION_MODE=live \
VALIDATION_ENVIRONMENT=production \
VALIDATION_REVIEW_JSON=backend/validation-review.production.json \
VALIDATION_RETENTION_DAYS=365 \
VALIDATION_ARCHIVE_LOCATION=release-record/evidence-bundle \
VALIDATION_ARCHIVE_OWNER="$USER" \
VALIDATION_ARCHIVE_ROOT=/mnt/release-record/agent-runtime \
VALIDATION_OCI_UPLOAD_BUCKET_NAME=<release-record-bucket> \
VALIDATION_OCI_UPLOAD_NAMESPACE=<object-storage-namespace> \
VALIDATION_OCI_UPLOAD_OBJECT_PREFIX=agent-runtime/release-archives \
VALIDATION_OCI_RETENTION_CONFIRMED=1 \
scripts/validate-production-evidence.sh
```

`VALIDATION_ARCHIVE_ROOT` を指定した場合は、bundle 生成後に archiver まで自動実行します。指定しない場合は
bundle 生成までで停止し、後続の保存先コピーは release owner が手動で実行します。
`VALIDATION_OCI_UPLOAD_BUCKET_NAME` も指定した場合は upload manifest まで生成します。実アップロードを行う
場合のみ `VALIDATION_OCI_UPLOAD_EXECUTE=1` を追加します。

GitHub workflow で review gate / bundle まで実行する場合は、手動 workflow の `review_json_path` に
`backend/validation-review.production.json` のような repository root 相対パスを指定します。必要なら
`bundle_json_path` で出力先を上書きできます。長期保存ポリシーは `retention_days`、
`archive_location`、`archive_owner` で上書きでき、`archive_owner` が空の場合は
`github-actions:<actor>` が記録されます。self-hosted runner に長期保存用 filesystem が mount されている
場合は、`archive_root` を指定すると workflow 内でも archiver が実行されます。
さらに `upload_bucket_name` を指定すると upload manifest を生成し、`execute_upload=true` の場合のみ OCI
Object Storage へ実アップロードします。実アップロード時は `retention_confirmed=true` も指定し、runner readiness
で retention / immutable bucket 確認を release evidence に残します。

## 1. Oracle Runtime Store

目的:

- normalized projection table の write / audit query latency を実 Oracle で測定する。
- partition migration 後の audit query plan と SLA を確認する。

Command:

```bash
cd backend
uv run python scripts/agent_runtime_oracle_load_check.py \
  --runs 1000 \
  --audit-iterations 20 \
  --audit-limit 100 \
  --write-mode incremental \
  --sla-write-ms 60000 \
  --sla-audit-p95-ms 1000
```

Evidence:

- `ok`
- `write_duration_ms`
- `write_p95_ms`
- `audit_p95_ms`
- `violations`
- Oracle plan note: partition / index used

## 2. RBAC / IdP JWKS Rotation

目的:

- 実 IdP の JWKS endpoint が取得できること。
- rotation window 後に `kid` の追加または削除を検出できること。
- sample JWT で backend status / run 作成が通ること。

Command:

```bash
cd backend
uv run python scripts/agent_runtime_gateway_jwks_check.py \
  --backend-status \
  --rotation-check \
  --rotation-interval-seconds 300 \
  --min-rotated-kids 1
```

Evidence:

- `checks.jwks.key_count`
- `checks.jwks_rotation.added_kids`
- `checks.jwks_rotation.removed_kids`
- `checks.backend_status.ok`

## 3. MCP OAuth / JWKS Gateway

目的:

- MCP gateway 用 OAuth client credentials token を取得できること。
- token を使って `tools/list` が成功すること。
- gateway が IdP / JWKS と同じ trust boundary で検証できること。

Command:

```bash
cd backend
uv run python scripts/agent_runtime_mcp_oauth_check.py \
  --require-oauth \
  --require-jwks \
  --tools-list \
  --rotation-check \
  --rotation-interval-seconds 300
```

Evidence:

- `auth_mode`
- `checks.oauth_token.expires_in`
- `checks.tools_list.tool_count`
- `checks.jwks.key_count`
- `checks.jwks_rotation.rotated_count`

## 4. Container Sandbox

目的:

- rootless Docker / Podman が利用されていること。
- seccomp と `no-new-privileges` が有効であること。
- network none / user namespace / non-root user で smoke command が成功すること。

Command:

```bash
cd backend
uv run python scripts/agent_runtime_container_sandbox_check.py \
  --runtime docker \
  --image busybox:latest \
  --network none \
  --security-opt no-new-privileges:true \
  --security-opt seccomp=default \
  --userns private \
  --user 65532:65532 \
  --require-rootless \
  --require-seccomp \
  --require-no-new-privileges \
  --require-network-none
```

Evidence:

- `checks.runtime_info.ok`
- `checks.security_profile.properties.rootless`
- `checks.security_profile.properties.seccomp`
- `checks.security_profile.properties.no_new_privileges`
- `checks.smoke.ok`

## Evidence Template

```json
{
  "validated_at": "YYYY-MM-DDTHH:MM:SSZ",
  "environment": "staging|production",
  "validator": "name/team",
  "oracle": {},
  "rbac_jwks": {},
  "mcp_oauth": {},
  "container_sandbox": {},
  "notes": []
}
```
