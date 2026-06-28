# 依存関係セキュリティ例外

## GHSA-537c-gmf6-5ccf — cryptography wheel 内 OpenSSL

- 状態: production mitigation 済み / upstream 解消待ち
- 対象: `cryptography 46.0.7`
- 理由: OCI Python SDK 2.179.0 が `cryptography>=3.2.1,<47.0.0` を要求し、修正版
  `48.0.1` と同時に解決できない。
- 影響: advisory は PyPI wheel に静的リンクされた OpenSSL を対象とする。
- 対策: production Docker image では `cryptography` の wheel を禁止し、security update
  適用済みの Debian `libssl-dev` に対して sdist を build する。runtime stage でも同じ
  security update を適用する。
- 監査: OCI SDK が 48.0.1 以上を許可した時点で direct upgrade し、この例外を削除する。

ローカル開発環境は production credential を使用しない。依存監査は、上記 production
mitigation を確認したうえで次を実行する。

```bash
pip-audit --local --ignore-vuln GHSA-537c-gmf6-5ccf
```

参考: [pyca advisory](https://github.com/pyca/cryptography/security/advisories/GHSA-537c-gmf6-5ccf)
