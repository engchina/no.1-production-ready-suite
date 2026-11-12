# JSONLへ変換した原文の閲覧と強調（#4888）

CSV・Excel・Wordを構造化レコードJSONLへ変換した資料は、解析画面とRAGの「根拠を表示」で同じ共有`EvidenceReader`を使う。CSV/Excelの表は行番号・シート・セル範囲、Wordは段落/表の位置を示す。原Officeのページ版式やbboxを新しく作る機能ではない。元ファイルは既存のダウンロードで取得できる。

## 表示の根拠

1. 引用は保存されたChunk版→抽出版、解析画面は選択中のレシピの抽出版へ固定する。
2. その版の派生ファイルを読み、保存された`source_derivation.derived_sha256`と照合する。現在の別レシピや別版のJSONLへ置き換えない。
3. JSONLを既存の厳格な契約で読み、record IDと再構成した本文が保存要素と一致する記録だけを構造化表示する。行列や段落の位置は変換器の記録を使う。同じ値を持つ別記録を文字検索で選ばない。
4. 引用/選択元の記録を強調する。文字単位の主張の正しさを証明する印ではなく、記録単位の強調であることを案内する。文脈として追加した前後の記録は強調しない。未確定の引用行から開いたときは既存の強調抑止を維持する。

表の見出し・複数行セル・Wordの段落と表は共有DataTable/文字高亮で表示する。Word旧形式の変換後位置は原本の頁位置と区別する。前後文は既存readerの文字数/ブロック数で制限する。記録が部分窓になる場合や表の構造化表現が同じ読取予算を超える場合は、表全体を重ねず保存本文の有界な表示へ戻す。旧記録に派生hashが無ければ従来の保存本文を読み、原生のセル座標へは投影しない。修正済み本文がJSONLと異なる要素にも原生座標を付けない。

解析出力・artifact版、KB所属、検索/回答プロファイル、検索/生成/LLM呼出しを変更しない。対応するJSONLと位置が保存されている資料は再解析不要。旧記録の欠けた位置を作る場合は対象資料だけを必要な工程から再処理する。

## API

- `GET /api/documents/{id}/reader?chunk_id=...` — 引用版の原文。JSONLは保存した派生hashとrecordから構造化表示を追加する。
- `GET /api/documents/{id}/recipes/{recipe}/reader?expected_revision=...&element_id=...` — 解析画面の原文。抽出版が変われば409。既存のレシピ閲覧権限で判定する。
- `GET .../preview-source?chunk_set_id=...` — 引用版の表示属性に`is_structured_records`を追加する。原拡張子ではなく実際の派生ファイルの種類でOffice PDFとJSONLを分ける。

## 局所検証コマンド

リポジトリ直下から、それぞれ実行する。

```bash
cd rag/backend
uv run --locked --no-sync pytest tests/test_structured_source_reader.py tests/test_structured_reader_routes.py tests/test_source_reader.py tests/test_citation_preview_source.py tests/test_json_source_routes.py -q
uv run --locked --no-sync mypy app/services/structured_source_reader.py app/services/source_reader.py app/api/routes/documents.py
```

```bash
cd platform/packages/ui
npx vitest run tests/source-record-view.test.tsx tests/evidence-reader.test.tsx
npm run typecheck
npm run build
```

```bash
cd rag/frontend
npm run lint
npm run build
npx playwright test e2e/document-office-preview.spec.ts e2e/citation-text-preview.spec.ts -g 'JSONLの構造化原文|Chatの原文の段落を強調する'
```

合成JSONLと同じ保存本文から作ったAPI fixtureは`frontend/e2e/fixtures/structured-source-reader.json`。ブラウザ検証はfixtureを使う局所検証で、配備後の実環境受入とは分ける。

配備が承認された後、リポジトリ直下で:

```bash
sudo -n ./platform/deploy/suite-update.sh
```
