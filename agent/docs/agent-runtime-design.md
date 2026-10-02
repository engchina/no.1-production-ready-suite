# Agent Runtime design（廃止）

本プロジェクトは Runtime から Control Plane へ再定義されました。正本は
[agent-control-plane-design.md](./agent-control-plane-design.md) です。

旧 in-process Runtime（v1 の Run・planner・Memory）は #756 で削除しました。Run はすべて組み込み Runtime
（OpenAI Agents SDK + OCI Enterprise AI。#754）で実行します。
