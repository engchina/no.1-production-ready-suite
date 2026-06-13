# Terraform / OCI Resource Manager

このディレクトリは本番 OCI 構成の雛形を置く場所です。現時点では、アプリケーションコード側の参照実装を優先し、実リソース作成は最小 skeleton に留めています。

想定リソース:

- OKE または Container Instances
- OCI Object Storage bucket
- Oracle 26ai Autonomous Database
- OCI Vault secret
- Prometheus / Logging / Alarms
- IAM dynamic group / policy

`main.tf` は意図を示す skeleton です。実テナンシーへ適用する前に compartment、network、database sizing、Vault policy を環境ごとに具体化してください。
