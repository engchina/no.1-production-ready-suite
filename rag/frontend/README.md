# frontend — production-ready RAG UI

Vite + React Router + TypeScript + Tailwind v4 + shadcn/ui + TanStack Query + Zustand。

UI/UX 構造は本リポジトリの AGENTS.md と `frontend/src` を正本として管理する。

## セットアップ

```bash
npm ci
cp .env.example .env.local
npm run dev          # http://localhost:3000
```

`/api/*` は `BACKEND_URL`（既定 http://localhost:8000）へプロキシされる。
サーバ状態は TanStack Query、UI 永続状態（例: サイドバー折りたたみ）は Zustand store で管理する。

## 開発コマンド

```bash
npm run lint
npm run typecheck
npm run build
```

## コンテナ

本番用 Docker image は `package-lock.json` を前提に `npm ci` で再現可能に依存解決する。
runtime stage は Vite の `dist/` を nginx で配信し、`/api/*` は `BACKEND_URL` へリバースプロキシする。

## 構成

```
src/
  main.tsx             Vite エントリ
  App.tsx              React Router ルート定義
  globals.css          Tailwind v4 / shadcn/ui theme tokens
  components/
    layout/Sidebar     サイドナビ
    StatusBadge        ファイル状態バッジ
  lib/                 api / queries / Zustand ui-store / routes / i18n(ja) / utils
```

> 画面の設計・実装は AGENTS.md の指示に従い `ui-ux-pro-max` skill を使う。
