# Terraform / OCI Resource Manager

このディレクトリは本番 OCI 構成の雛形を置く場所です。現時点では、アプリケーションコード側の参照実装を優先し、実リソース作成は最小 skeleton に留めています。

想定リソース:

- OKE または Container Instances
- OCI Object Storage bucket
- Oracle 26ai Autonomous Database
- Prometheus / Logging / Alarms
- IAM dynamic group / policy

`main.tf` は意図を示す skeleton です。実テナンシーへ適用する前に compartment、network、database sizing を環境ごとに具体化してください。

Oracle 26ai の table / vector index / audit table DDL は Terraform skeleton に直接埋め込まず、backend の `uv run python -m app.rag.oracle_schema --output ../artifacts/oracle-schema.sql --manifest-output ../artifacts/oracle-schema.manifest.json` で生成した artifact をレビューしてから、SQLcl または管理された migration 手順で適用します。
