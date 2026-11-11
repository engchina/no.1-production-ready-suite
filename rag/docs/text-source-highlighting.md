# テキスト原本の定位と強調（#4881・#4882）

TXT（`.txt`）とMarkdown（`.md`・`.markdown`）は、画像のbboxを作らず原本のUnicode文字範囲を使う。Unstructuredで解析するときに短い原本も位置表を保存し、文書の解析画面で選んだ要素の位置を原本の窓へ投影する。選択を変えると該当範囲を取り直し、共有のプレビューで強調する。文字範囲は原本のhashと対応表の版を確認して使う。KBの所属範囲と検索・回答プロファイルの設定は変更しない。

Chatの「根拠を表示」は保存済みの引用元要素から原順序で読む。引用が複数の要素にまたがる場合も、空白の差だけを吸収した一意な文字対応を各要素へ投影する。文字単位で照合できないが引用元が確定している場合は、その段落全体を強調し、文字単位の位置が未確定という案内を残す。同じ引用が複数回あって位置を確かめられない場合は文字の強調をしない。回答の引用行そのものが未確定の候補の場合も、従来の通知と強調の抑止を維持する。

方式の参考は [WeKnoraの原文引用](https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/18-chat-experience.md)。原本の位置と文字範囲で表示し、追加のLLM・再検索・回答の再生成は行わない。WeKnoraのコードやUIをコピーせず、既存の共有部品で実装する。

## 既存文書と配備

解析artifact版は13。旧版の解析結果を新しい位置表として使い直さない。配備後、対象文書のレシピで「解析」から再処理する。再分割だけでは位置表を作れない。自動的に全既存文書を処理し直すことはない。版の異なる原本や確かめられない位置を、正確な範囲に見せない。

配備が承認された後、リポジトリ直下で実行する:

```bash
sudo -n ./platform/deploy/suite-update.sh
```

## 検証コマンド

リポジトリ直下から:

```bash
cd rag/backend
uv sync --locked --dev
uv run --locked --no-sync pytest tests/test_source_reader.py tests/test_inspection_scope.py tests/test_inspection_scope_api.py tests/test_source_map_resolve.py -q
uv run --locked --no-sync pytest ../packages/rag_parser_core/tests/test_source_map.py tests/test_source_map_window.py tests/test_inspection_chunk_partitions.py -q
uv run --locked --no-sync mypy app/services/source_reader.py app/services/inspection_scope.py
```

実Unstructuredの位置記録（リポジトリ直下から。サービスの設定・モデルを用意した環境）:

```bash
cd rag/services/parsers/unstructured
uv run --locked --no-sync --with pytest python -m pytest -q tests/test_source_map_real.py
```

共有部品（リポジトリ直下から）:

```bash
cd platform
npm run build
npx vitest run packages/ui/tests/document-preview.test.tsx packages/ui/tests/evidence-reader.test.tsx
npm run typecheck -w @production-ready/ui
```

画面の局所回帰（リポジトリ直下から。APIをmockする検証で配備後受入とは別）:

```bash
cd rag/frontend
npm run lint
npm run build
npx playwright test e2e/document-workspace-file-processing.spec.ts -g '原本の保存座標'
npx playwright test e2e/citation-text-preview.spec.ts -g 'Chatの原文の段落'
```

desktopと375pxで3形式ずつを確認する。位置記録の実サービス検証、保存済み回答の再生、画面のfixture回帰と、配備後のDB・画面の受入を区別して報告する。

## XMLツリーJSONの原本同期（#4886）

XMLツリーJSONは保存済みの`json_source_map`を使う。構造化要素またはChunkを選ぶと、その要素IDを原本窓の取得へ渡し、JSON Pointerと本文範囲を原本文字列の座標へ投影する。左の原本プレビューでも該当文字列を強調する。同じ本文を持つ別ノードはPointerで区別し、JSONのUnicode escapeや引用符のescapeも原本上の範囲へ写す。原本hash・本文・対応が一致しない場合は精密な強調を出さない。

右側の「原本の対応箇所」とRAG根拠閲覧の既存機能は維持する。表示時の座標投影の修正で、parser出力やartifact版は変えない。対応表が保存されている文書は再解析不要。対応表の無い旧文書は既存の表示用再構築で一致した要素だけを使う。

局所検証（それぞれリポジトリ直下から）:

```bash
cd rag/backend
uv run --locked --no-sync pytest tests/test_inspection_scope.py tests/test_json_source_window.py tests/test_json_source_rebuild.py tests/test_json_source_routes.py -q
```

```bash
cd rag/frontend
npx playwright test e2e/document-workspace-file-processing.spec.ts -g '構造化要素から解析版を固定して原本ノード'
npx playwright test e2e/citation-text-preview.spec.ts -g '原本ノードは展開時だけ'
```

## 通常JSONとHTMLの原本対応（#4891）

引用の「原本の対応箇所」は、XMLツリーJSONの `json_source_map` に加え、通常JSONの `source_map` / `json_pointer` とHTML/HTMの `source_map` / `html_source` を読む。原本のhash・対応表の版・文字数・保存した範囲を検証し、1回8192文字以内の窓を返す。JSONは記録された値のまとまり、HTMLは記録されたタグ片を示す。抽出正文の引用語と原本の単位は別の精度であり、文字単位の対応がない場合は要素の対応と案内する。

HTMLは原文コードを文字列として表示し、タグ・script・外部resourceを実行しない。原本と処理後のどちらも引用時点の抽出版から取得し、hashが合うものだけを使用する。古い記録・壊れた範囲・未知の要素は推測して埋めない。解析の出力、検索・生成、LLM呼出しは変更しない。

リポジトリ直下からの局所確認:

```bash
cd rag/backend
uv run --locked --no-sync pytest tests/test_generic_source_window.py tests/test_json_source_window.py tests/test_json_source_routes.py tests/test_json_source_rebuild.py tests/test_evidence_read_authz.py -q
uv run --locked --no-sync mypy app/services/json_source_window.py tests/test_generic_source_window.py
```

```bash
cd platform/packages/ui
npm run build
npx vitest run tests/evidence-reader.test.tsx tests/original-source-panel.test.tsx
```

```bash
cd rag/frontend
npm run lint
npm run build
npx playwright test e2e/citation-text-preview.spec.ts
```
