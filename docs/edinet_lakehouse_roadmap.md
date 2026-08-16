# EDINET Lakehouse / Genie 構築ロードマップ

最終更新: 2026-08-16

## 1. 目的

金融庁 EDINET API v2 から有価証券報告書を継続取得し、原本を Bronze、監査可能な
XBRL 正規化データを Silver、企業比較に使える標準指標を Gold として Unity Catalog
へ公開する。最終的に Databricks Genie Agent（旧 Genie Space）で、企業・年度・業種を
またぐ自然言語分析を構成し、Genie One から利用できる状態にする。

`edinetdb.jp` は同じデータをコピーする対象ではなく、提供粒度を決めるための参考とする。
最初のリリースでは「上場企業の年度別財務時系列」を再現し、その後、指標・対象書類を
段階的に増やす。

## 2. 成功条件（MVP）

- 上場企業の有価証券報告書（`docTypeCode=120`）を日次で差分取得できる。
- API レスポンスと取得 ZIP を改変せず保存し、任意の値を原本まで追跡できる。
- 同じジョブを再実行しても重複せず、失敗書類だけ再処理できる。
- 売上高、営業利益、経常利益、当期純利益、総資産、純資産、営業 CF などの主要指標を
  企業×会計年度で比較できる。
- 連結/個別、当期/前期、期間/時点、単位、訂正報告を区別できる。
- Genie が検証済みビューだけを参照し、代表質問に対して正しい SQL を生成する。
- すべての Gold 値に `doc_id`、XBRL concept、context、変換規則が残る。

## 3. レイヤ構成

```text
EDINET API v2
  ├─ documents.json（提出日ごとの一覧）
  └─ documents/{doc_id}?type=1（XBRL ZIP）
             │
             ▼
Bronze: 原本・取得台帳（再取得せず再処理できる）
             │
             ▼
Silver: filings / contexts / units / facts / labels / parse_log
             │
             ▼
Gold: companies / annual_financials / ratios / segments / workforce
             │
             ▼
Genie Agent用 semantic view または Unity Catalog metric view
             │
             ▼
Genie One（チャット、ダッシュボード、共有エントリーポイント）
```

重要: Silver は原本の意味を落とさない正規化層、Gold は業務上の同義概念を統合する層と
する。`NetSales` と `Revenue` のような複数 concept を Silver で一つに潰さない。

## 4. 目標データモデル

### Bronze

| オブジェクト | 主キー/配置例 | 内容 |
|---|---|---|
| `document_lists` | `date=YYYY-MM-DD/documents.json` | API の生レスポンス |
| `filing_zips` | `submit_date=.../doc_id=.../document.zip` | type=1 の生 ZIP |
| `ingestion_manifest` | `request_date, doc_id` | HTTP 状態、hash、byte 数、取得日時、再試行回数 |

原本は append-only とし、秘密情報（API キー）は絶対に保存しない。ZIP は SHA-256 を
記録する。対象WorkspaceはAWSのため、Unity Catalog managed Volume
`/Volumes/workspace/edinet_bronze/raw` を使用する。

### Silver

| テーブル | 粒度 | 主な列 |
|---|---|---|
| `filings` | 1提出書類 | doc_id、EDINET/証券コード、書類種別、期間、提出日時、訂正元、原本パス |
| `contexts` | 1 context | entity、period、連結/個別、dimension/member |
| `units` | 1 unit | 通貨・株数・比率、分子/分母 |
| `facts` | 1 XBRL fact | concept、context、unit、raw_value、numeric_value、decimals、nil |
| `concept_labels` | 1 taxonomy concept×言語 | 日本語/英語ラベル、taxonomy version |
| `parse_log` | 1 doc_id×処理版 | processed/excluded/error、件数、エラー、処理版 |

既存 `notebooks/edinet_zip_to_silver.py` の5テーブルを土台にする。ただし本番化前に、
API一覧メタデータとの結合、数値型列、dimensionの構造化、taxonomy/label、hashと処理版を
追加する。

### Gold（edinetdb.jp 相当の分析粒度）

初期は次の3オブジェクトに絞る。

1. `companies`: 企業マスター（EDINETコード、証券コード、名称、業種、上場状態）
2. `annual_financials_long`: 企業×会計年度×標準指標。値と証跡を保持
3. `annual_financials_wide`: Genie/BI 向け。主要指標を1年度1行へ横持ち

第2段階で `ratios`、`segments`、`workforce`、`text_blocks` を追加する。EDINET DB が公開する
財務時系列は多数の項目を持つが、最初から全項目を横持ちにせず、利用質問と照合できた
指標から増やす。

`annual_financials_long` の最低限の列:

```text
edinet_code, sec_code, fiscal_year, period_start, period_end,
accounting_standard, consolidation_scope, metric_key, metric_label_ja,
value, unit, source_doc_id, source_concept, source_context_id,
mapping_rule_id, mapping_version, quality_status
```

## 5. 数値を正しく選ぶ規則

Gold への昇格は concept 名の単純な完全一致だけでは行わない。

1. 有効な最新提出を決める。訂正有価証券報告書がある場合は訂正対象と提出順を解決する。
2. 上場企業 MVP では証券コードありを基本とし、投資信託等は別データセットへ分離する。
3. 当期かつ連結を優先し、連結が存在しない企業のみ個別を採用する。
4. duration 指標は会計年度の start/end、instant 指標は期末日と一致させる。
5. segment、内訳、過年度比較用 dimension を企業全体の代表値から除外する。
6. accounting standard と taxonomy version ごとに concept 候補へ優先順位を持たせる。
7. decimals と unit を使って `DECIMAL(38, ...)` に安全に変換する。欠損と0を区別する。
8. 同順位の候補が複数なら推測せず `ambiguous` として隔離する。

各マッピングは `metric_key`、候補 concept、context 条件、優先度、適用 taxonomy、根拠、
テストケースを設定ファイルで版管理する。

## 6. 実装順序

### Phase 0 — 契約・対象範囲（現在）

- [x] 既存コード、サンプル ZIP、SQLite、Databricks ノートブックを棚卸し
- [x] Bronze/Silver/Gold/Genie の責務と MVP を定義
- [x] edinetdb.jp の公開データ粒度を参考調査
- [x] AWS Workspace、Unity Catalog、レイヤ別スキーマ、managed Volumeを確定・作成
- [ ] 対象年度と初回バックフィル範囲を確定

完了条件: この文書の未確定値を決め、命名・保持期間・SLAをデータ契約にする。

### Phase 1 — Bronze 収集を堅牢化

- [x] ローカル収集器で `documents.json` の生レスポンスを日付パーティションへ保存
- [x] ローカル収集器のZIP配置を `submit_date/doc_id` で統一
- [ ] 同じBronze配置をAzure Storage上の本番収集ジョブへ適用
- [ ] SHA-256、HTTP状態、取得日時を manifest Delta テーブルへ保存
- [ ] 429/5xx retry、日付checkpoint、再実行、取得不能書類をテスト
- [ ] Databricks Workflow で日次実行（JST）とバックフィルを分離

完了条件: ローカルDBなしでも Bronze だけから全下流を再構築できる。

### Phase 2 — Silver 正規化

- [ ] API一覧を `filings` として先に取り込み、ZIPから書類種別を推測しない
- [ ] XBRLを `contexts`、`units`、`facts` に冪等 MERGE
- [ ] `raw_value` と型変換済み `numeric_value/date_value/text_value` を併存
- [ ] dimensionを JSON 文字列だけでなく map/子テーブルとして検索可能にする
- [ ] taxonomy label と role（presentation/calculation）を抽出
- [ ] DQ: PK重複、orphan context/unit、件数急減、parse error、hash不一致

完了条件: サンプル企業10社×3年でファクトをEDINET原本へ逆引きできる。

### Phase 3 — Gold 標準指標

- [ ] 主要20指標のマッピング表を作る
- [ ] 連結/個別、当期/前期、訂正優先ロジックをSQL化
- [ ] `annual_financials_long` と `annual_financials_wide` を作る
- [ ] 成長率、利益率、ROE、ROA、自己資本比率を計算
- [ ] EDINET DB API または原本とのサンプル突合（正解データとして盲信しない）
- [ ] 欠損理由を `not_applicable / not_disclosed / ambiguous / parse_error` で管理

完了条件: 基準企業の主要指標が、値・期間・連結範囲・単位まで一致する。

### Phase 4 — Genie Agent / Genie One

- [ ] EDINET分析用 Genie Agent（旧 Genie Space）を作成
- [ ] Genie Agentには原則 `annual_financials_wide` 1ビューのみ接続
- [ ] Unity Catalogに日本語のテーブル/列コメント、同義語、単位を設定
- [ ] 「今年度」は最新提出年ではなく各社の最新会計年度等、解釈規則を記載
- [ ] 検証済みSQLを最低5件登録
- [ ] benchmark質問を最低10件作り、期待SQL/結果を固定
- [ ] AI/BIダッシュボードを作成し、AgentとともにGenie Oneへ共有
- [ ] Genie Oneのホーム、ロゴ、歓迎文、ピン留めコンテンツを設定
- [ ] SQL Warehouse・Unity Catalog・Genie Agent・Consumer accessを最小権限で設定

代表質問:

- 「トヨタの過去5年の売上高と営業利益率を見せて」
- 「情報・通信業でROEが高く、自己資本比率40%以上の会社は？」
- 「任天堂の営業キャッシュフローが前年から減った年度と減少率は？」
- 「同じ会計年度でA社とB社の売上成長率を比較して」
- 「この値の提出書類とXBRL conceptを示して」

完了条件: benchmarkの結果正答率100%、生成SQLの意味正答率90%以上を目標とし、対象の
業務ユーザーがGenie OneからEDINET Agentとダッシュボードを利用できる。

### Phase 5 — 拡張・運用

- [ ] セグメント、従業員/人的資本、大株主、有報本文を追加
- [ ] Databricks SQL alertsで遅延・件数・DQを監視
- [ ] mapping version変更時の影響比較と再計算手順を自動化
- [ ] コスト、実行時間、保存量を月次レビュー

## 7. 直近の作業単位

次は Phase 1 を行う。

1. Bronze はUnity Catalog managed Volumeを使用し、EDINET APIキーをSecretへ登録する。
2. API一覧JSONとZIPを同一 `doc_id` で結べる取り込み台帳を実装する。
3. 既存ノートブックを `01_bronze_to_silver` として整理する。
4. 上場企業3社（JP-GAAP、IFRS、金融）を各3年取得し、主要指標候補を観察する。
5. 観察結果から最初の20指標マッピングと自動テストを作る。

## 8. 現時点の既存資産と注意点

- `src/edinet_collector/`: API取得、ZIP解析、SQLite保存のローカル検証器として再利用可能。
- `notebooks/edinet_zip_to_silver.py`: SparkでZIPを並列解析しDeltaへMERGEする土台。
- 現在のノートブックはZIP内タイトルから `docTypeCode=120` を推定している。本番では
  BronzeのAPI一覧メタデータを正とする。
- 現在の `facts.value` は文字列のみ。Gold計算前に安全な数値変換列が必要。
- 現サンプルは証券コードが空の運用会社/ファンド系書類で、上場事業会社MVPの代表検証には
  適さない。JP-GAAP、IFRS、金融のサンプルを追加する。
- 既存ノートブックはAzure Blob前提だが、接続先はAWS Workspaceである。AWS版では
  Unity Catalog managed VolumeとService Principalを使う実装へ置き換える。

## 9. 参考資料

- [EDINET](https://disclosure2.edinet-fsa.go.jp/week0010.aspx)
- [EDINET DB API仕様](https://edinetdb.jp/docs/api)
- [EDINET DB 指標リファレンス](https://edinetdb.jp/docs/metrics)
- [Databricks: Genie Spaceの設定](https://docs.databricks.com/aws/genie/set-up)
- [Databricks: Genie Space API](https://docs.databricks.com/aws/en/genie/conversation-api)
- [Databricks: Genieのベストプラクティス](https://docs.databricks.com/gcp/en/genie/best-practices)
- [Databricks: Genie One](https://docs.databricks.com/aws/en/genie-one/)
- [Databricks: Genie Agents](https://docs.databricks.com/aws/en/genie-agents/)
