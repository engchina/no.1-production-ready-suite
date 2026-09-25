# Agent Runtime design（廃止）

本プロジェクトは Runtime から Control Plane へ再定義されました。正本は
[agent-control-plane-design.md](./agent-control-plane-design.md) です。

旧 in-process Runtime は `X-Agent-API-Version: 1` の移行互換と `legacy-native` 履歴参照にのみ
残り、新規 v2 Run の実行先にはなりません。
