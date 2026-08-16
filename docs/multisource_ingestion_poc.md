# Databricks 複数データソース取り込みPoC

最終更新: 2026-08-16

## 1. 検証目的

Azure Storage、Amazon S3、ローカルファイル、Microsoft SharePointに点在するデータを
Databricksへ取り込み、共通の監査列と品質ルールを持つSilver Deltaテーブルへ変換できる
ことを確認する。

このPoCでは性能だけでなく、次を比較する。

- 認証情報をコードへ置かずに接続できるか
- 初回全量と2回目以降の差分を区別できるか
- 再実行しても重複しないか
- 更新・削除・スキーマ変更を検知できるか
- 原本、取得日時、ソースURI、hashを追跡できるか
- 障害時にソース単位・ファイル単位で再処理できるか

## 2. 共通アーキテクチャ

```text
Azure Blob / ADLS ─┐
Amazon S3 ─────────┤
Local files ───────┼─> Bronze landing / source table
SharePoint ────────┘          │
                              ▼
                     共通標準化・品質検査
                              │
                              ▼
             workspace.edinet_silver.<source>_<entity>
                              │
                              ▼
                 Gold / Genie Agent / Genie One
```

Bronzeは原本またはコネクタの未加工出力を保持する。Silverには、業務列に加えて必ず次の
監査列を付与する。

```text
source_system, source_uri, source_file_name, source_modified_at,
source_size, source_etag, source_sha256, ingested_at, pipeline_run_id,
schema_version, record_status, rescued_data
```

## 3. ソース別の検証方式

| ソース | Bronzeへの経路 | 認証 | 差分方式 | PoC入力 |
|---|---|---|---|---|
| Local | Databricks CLI `fs cp` → managed Volume | Databricks OAuth | hash/ファイル名 | `data/facts.csv`、EDINET ZIP |
| S3 | Unity Catalog External Location/Volume → Auto Loader | AWS IAM role | file events/checkpoint | CSV/JSON/Parquet各1ファイル |
| Azure | `abfss://` → Auto LoaderまたはCOPY INTO | Entra service principal推奨、PoCはSAS/keyも可 | checkpoint/ETag | 既存containerの限定prefix |
| SharePoint | Lakeflow Connect managed SharePoint connector | Databricks-managed OAuth U2M | connector管理 | CSV/Excel/PDF各1ファイル |

SharePoint managed connectorはBetaで、WorkspaceのPreview有効化が必要。標準connectorを
使う場合はDatabricks Runtime 17.3以降とPreview channelが必要になる。

## 4. Silver共通契約

### 構造化ファイル

- 列名をsnake_caseへ統一する。
- 日付・数値は明示的にcastし、変換不能値は捨てず`rescued_data`へ隔離する。
- 行の一意キーがある場合はDelta `MERGE`、ない場合はファイルhash＋行番号を技術キーとする。
- ソース削除を即時物理削除せず、`record_status='deleted'` として履歴を残す。

### PDF/DOCX/画像等

- Bronzeはbinaryとファイルメタデータを保持する。
- Silverは本文・表・ページ・抽出エラーを構造化する。
- EDINET XBRLの数値と文書AI抽出値を混ぜず、出典種別を明示する。

## 5. 実施順序

### Step 1 — Local（実行中）

- [x] `data/facts.csv`をmanaged Volumeへアップロード
- [x] CSVから`workspace.edinet_silver.poc_local_facts`を作成
- [x] 件数・schema・再実行時の結果を確認
- [ ] サンプルZIPのbinary ingestionを確認

実測結果（2026-08-16）:

- Bronze: `/Volumes/workspace/edinet_bronze/raw/local/facts.csv`
- Silver: `workspace.edinet_silver.poc_local_facts`
- 255行、1書類、提出日2026-07-24
- `source_uri`欠損0件
- 同じ処理を2回実行しても255行であり、論理重複なし
- 実行SQL: `sql/01_local_to_silver.sql`

### Step 2 — S3

- [ ] 検証用bucket/prefixを決定
- [ ] 読み取り専用IAM roleとExternal Locationを作成
- [ ] Auto Loaderで初回・追加・更新ファイルを検証
- [ ] checkpointをmanaged Volumeへ保存

### Step 3 — Azure

- [ ] Storage account、container、限定prefixを決定
- [ ] Databricks Secretへ認証値を登録
- [ ] AWS Databricksから`abfss://`への疎通確認
- [ ] Auto Loader/COPY INTOで初回・差分を検証
- [ ] 通信コストとクロスクラウド構成の妥当性を評価

### Step 4 — SharePoint

- [ ] SharePoint PreviewとLakeflow Connectを有効化
- [ ] 検証用site/folderを決定
- [ ] Databricks-managed OAuth U2M connectionを作成
- [ ] CSV/Excel/PDFの追加・更新・削除を検証
- [ ] managed connector出力をSilver共通契約へ変換

### Step 5 — 横断評価

- [ ] 全ソースを同じDQレポートで比較
- [ ] コスト、所要時間、運用負荷、再実行性を採点
- [ ] 推奨方式と本番化しない方式を決定

## 6. 合格条件

- 各ソースから最低1ファイルをSilver Deltaテーブルへ格納できる。
- 同じ入力を再実行してもSilverの論理行数が増えない。
- 新規・更新・削除の各ケースを識別できる。
- Silver行から原本URIとpipeline runへ逆引きできる。
- 秘密情報がノートブック、テーブル、ログ、Git管理ファイルへ出ない。
- 1ソースの障害が他ソースの処理を妨げない。

## 7. ユーザーから必要な情報

秘密値そのものをチャットへ貼る必要はない。

- S3: bucket名と検証prefix、AWSアカウントでIAM設定を承認できる人
- Azure: storage account名、container名、検証prefix、ADLS Gen2かBlobか
- SharePoint: site/folder URL、接続を承認できるMicrosoft 365ユーザー
- データ: 各ソースで検証に使ってよいファイルと、期待件数または期待値

## 8. 参考

サービス未契約の状態からの準備、接続、セキュリティ、後片付けは
`docs/external_storage_setup_guide.md` を参照する。

- [Databricks: SharePoint ingestion](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-source-setup-overview)
- [Databricks: Azure Storage接続](https://docs.databricks.com/aws/en/connect/storage/azure-storage)
- [Databricks: S3 External Location](https://docs.databricks.com/aws/en/connect/unity-catalog/cloud-storage/s3/s3-external-location-manual)
- [Databricks: Unity Catalog Volumeのファイル操作](https://docs.databricks.com/aws/en/volumes/volume-files)
