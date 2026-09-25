# アプリ CI で共有 UI を解決する

各アプリ（RAG / NL2SQL / Agent）の frontend は `@engchina/production-ready-ui` に依存する。
CI でこれを解決する方法は、リンク方式の段階によって 2 通り。

## 現在: `file:` リンク期（sibling checkout）

アプリ CI は **UI repo を sibling パスに checkout し、build してから** アプリを `npm ci` する。
ディレクトリ配置が `file:../../no.1-production-ready-platform/packages/ui` と一致するよう `path:` を指定するのが要点。

```yaml
jobs:
  frontend:
    runs-on: ubuntu-latest
    steps:
      # アプリと共有 UI を sibling に並べる（file: の相対パスを満たす）
      - uses: actions/checkout@v4
        with:
          path: no.1-production-ready-<app>
      - uses: actions/checkout@v4
        with:
          repository: engchina/no.1-production-ready-platform
          ref: main                       # 安定タグに固定してもよい（例: v0.1.0）
          path: no.1-production-ready-platform
          token: ${{ secrets.SHARED_UI_REPO_TOKEN }}  # private repo の場合に必要な PAT

      - uses: actions/setup-node@v4
        with:
          node-version: "22"

      # 共有 UI を先に build（アプリは dist の d.ts / index.js / tokens.css を参照する）
      - run: npm ci
        working-directory: no.1-production-ready-platform
      - run: npm run build
        working-directory: no.1-production-ready-platform

      # アプリの検証
      - run: npm ci
        working-directory: no.1-production-ready-<app>/frontend
      - run: npm run typecheck
        working-directory: no.1-production-ready-<app>/frontend
      - run: npm run build
        working-directory: no.1-production-ready-<app>/frontend
```

> `SHARED_UI_REPO_TOKEN` は UI repo へ read 権限のある PAT。public repo なら不要で、
> `token` 行を削ってよい（既定の GITHUB_TOKEN で同一 owner の public repo は checkout 可）。

## 切替後: GitHub Packages 期

publish 済みなら sibling checkout は不要。registry 認証付きで `npm ci` するだけ。

```yaml
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          registry-url: "https://npm.pkg.github.com"
          scope: "@engchina"
      - run: npm ci
        working-directory: frontend
        env:
          NODE_AUTH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
      - run: npm run typecheck --workspace ... # 以降は同じ
```
