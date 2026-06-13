# frontend — production-ready RAG UI

Next.js 15 (App Router) + TypeScript + Tailwind v4 + shadcn/ui + TanStack Query + Zustand。

UI/UX 構造は本リポジトリの AGENTS.md と `frontend/src` を正本として管理する。

## セットアップ

```bash
npm ci
cp .env.example .env.local
npm run dev          # http://localhost:3000
```

`/api/*` は `BACKEND_URL`（既定 http://localhost:8000）へプロキシされる。

## 開発コマンド

```bash
npm run lint
npm run typecheck
npm run build
```

## コンテナ

本番用 Docker image は `package-lock.json` を前提に `npm ci` で再現可能に依存解決する。
runtime stage は `npm ci --omit=dev` で production dependencies のみに絞り、公式 `node` ユーザーで `next start` を実行する。

## 構成

```
src/
  app/                 App Router（dashboard / upload / file-list / search ...）
  components/
    layout/Sidebar     サイドナビ
    StatusBadge        ファイル状態バッジ
  lib/                 routes / i18n(ja) / utils
```

> 画面の設計・実装は AGENTS.md の指示に従い `ui-ux-pro-max` skill を使う。
