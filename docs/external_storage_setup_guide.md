# 外部ストレージ導入・接続ガイド

最終更新: 2026-08-16

## 1. この文書の目的

現在は外部ストレージサービスを新規契約せず、Databricks内のmanaged Volumeとローカル
ファイルでETLを検証する。将来、Amazon S3、Azure Storage、Microsoft SharePointを利用
するときに、同じSilverテーブルへ接続先だけ追加できるようにする。

具体的な料金、無料枠、利用可能リージョン、Beta提供状況は変更されるため、契約直前に
各サービスの公式ページで再確認する。

## 2. 推奨する実施順序

1. 契約不要: ローカル → Databricks managed Volume → Silver
2. AWS利用時: S3 → Unity Catalog External Location → Silver
3. Azure利用時: ADLS Gen2 → `abfss://` → Silver
4. Microsoft 365利用時: SharePoint → Lakeflow Connect → Silver

外部サービスを一度に契約しない。まずローカル経路で共通変換・重複排除・監査列・DQを
完成させ、各サービスでは「接続と差分取得」だけを検証する。

## 3. 契約なしで今すぐ検証する方法

### 使用するDatabricks資産

- Bronze Volume: `/Volumes/workspace/edinet_bronze/raw`
- Silver schema: `workspace.edinet_silver`
- Gold schema: `workspace.edinet_gold`
- SQL Warehouse: `Serverless Starter Warehouse`

### ローカルファイルのアップロード

```bash
databricks fs mkdir \
  dbfs:/Volumes/workspace/edinet_bronze/raw/local \
  --profile edinet-dev

databricks fs cp data/facts.csv \
  dbfs:/Volumes/workspace/edinet_bronze/raw/local/facts.csv \
  --overwrite \
  --profile edinet-dev
```

アップロード後、`sql/01_local_to_silver.sql` をServerless SQL Warehouseで実行する。

### 外部サービスを模擬するディレクトリ

```text
/Volumes/workspace/edinet_bronze/raw/
  local/
  mock_s3/
  mock_azure/
  mock_sharepoint/
```

同じCSVをそれぞれへ配置し、`source_system`だけ変更して処理することで、契約前でも次を
確認できる。

- ソース別設定
- schema drift
- ファイル追加・更新・削除
- MERGEによる重複排除
- 監査列とDQレポート
- 複数ソースを統合したSilver/Goldビュー

確認できないのは、各サービス固有のOAuth/IAM、ネットワーク、API制限、差分通知、転送費用。

## 4. Amazon S3を導入する場合

対象Databricks WorkspaceがAWS `us-west-2`にあるため、最初の外部ストレージ候補はS3が
最も構成を単純にしやすい。

### 契約・作成

1. AWSアカウントを用意する。
2. 原則としてDatabricksと同じリージョンに検証用bucketを作る。
3. Block Public Accessを有効にする。
4. Bucket Versioningを有効にする。
5. 検証prefixを作る。例: `s3://<bucket>/databricks-poc/input/`
6. ライフサイクルルールで古い検証ファイルの削除時期を設定する。

### Databricks接続

推奨はCatalog Explorerの自動セットアップ。

1. Databricksで **Catalog → Connect → External Locations** を開く。
2. **Create external location → Set up Automatically** を選ぶ。
3. S3 URIを入力する。
4. 最初はread-onlyを選ぶ。
5. AWS側でIAM delegation requestを承認する。
6. 作成されたStorage CredentialとExternal Locationを確認する。
7. External Volumeを作り、Auto Loaderまたは`COPY INTO`で取り込む。

### 渡してもらう非機密情報

```text
bucket: <bucket-name>
prefix: databricks-poc/input/
region: us-west-2
access: read-only
```

Access Key/Secret Keyをチャット、`.env`、ノートブックへ保存しない。IAM roleを使う。

### 合格テスト

- 初回CSVを取り込める。
- 追加した2ファイル目だけが増分処理される。
- 同じファイルを再実行しても重複しない。
- 更新/削除を検知し、Silverへ状態を残せる。
- S3 URI、ETag、更新日時を追跡できる。

## 5. Azure Storageを導入する場合

新規なら通常のBlob StorageよりADLS Gen2を優先する。Azure側でHierarchical namespaceを
有効にしたStorage Accountを作成する。

### 契約・作成

1. Azure subscriptionを用意する。
2. Storage Accountを作成する。
3. Hierarchical namespaceを有効にする。
4. Containerを作る。例: `databricks-poc`
5. 読み取り対象prefixを作る。例: `input/`
6. Microsoft Entra IDで検証用Service Principalを作る。
7. 対象container/prefixに必要最小限のBlob読み取り権限を付ける。

### Databricks接続

AWS DatabricksからAzureへクロスクラウド接続するため、Unity CatalogのAWS用S3
External Locationではなく、SparkのAzure Storage connectorを使う。

```python
account = "<storage-account>"
tenant_id = "<tenant-id>"
client_id = "<application-id>"

spark.conf.set(
    f"fs.azure.account.auth.type.{account}.dfs.core.windows.net",
    "OAuth",
)
spark.conf.set(
    f"fs.azure.account.oauth.provider.type.{account}.dfs.core.windows.net",
    "org.apache.hadoop.fs.azurebfs.oauth2.ClientCredsTokenProvider",
)
spark.conf.set(
    f"fs.azure.account.oauth2.client.id.{account}.dfs.core.windows.net",
    client_id,
)
spark.conf.set(
    f"fs.azure.account.oauth2.client.secret.{account}.dfs.core.windows.net",
    dbutils.secrets.get("external-storage", "azure-client-secret"),
)
spark.conf.set(
    f"fs.azure.account.oauth2.client.endpoint.{account}.dfs.core.windows.net",
    f"https://login.microsoftonline.com/{tenant_id}/oauth2/token",
)
```

接続先例:

```text
abfss://databricks-poc@<storage-account>.dfs.core.windows.net/input/
```

### 渡してもらう非機密情報

```text
storage_account: <name>
container: databricks-poc
prefix: input/
tenant_id: <directory-id>
client_id: <application-id>
```

Client Secretはチャットへ貼らず、Databricks Secretへ直接登録する。

### 注意点

- AWSとAzure間のデータ転送費用・遅延を測定する。
- 本番データ量が多い場合は、同一cloudへ寄せる構成も比較する。
- Storage Account KeyよりService Principal/OAuthを優先する。
- DBFS mountは使用しない。

## 6. Microsoft SharePointを導入する場合

SharePoint Onlineを含むMicrosoft 365環境が必要。Databricksのmanaged SharePoint
connectorはBetaのため、検証用途から始める。

### 準備

1. Microsoft 365 tenantとSharePoint Onlineを用意する。
2. 検証用siteまたはdocument libraryを作る。
3. `csv/`、`excel/`、`documents/`フォルダを作る。
4. 個人情報・機密情報を含まないサンプルだけを置く。
5. DatabricksのPreview設定でSharePoint connectorを有効にする。

### Databricks接続

1. **Data Ingestion / Lakeflow Connect** を開く。
2. Microsoft SharePoint connectorを選ぶ。
3. Databricks-managed OAuth U2Mを選ぶ。
4. Microsoft 365ユーザーでログインし、対象siteへのアクセスを承認する。
5. Site/folder URLを入力する。
6. 形式をCSV、EXCEL、BINARYFILEなどから選ぶ。
7. 出力先を`workspace.edinet_bronze`のsource tableにする。
8. Silver変換pipelineを別に作る。

Databricks-managed OAuth U2Mなら、通常は独自のAzure App Registrationは不要。本番の
無人実行や組織ポリシー上必要な場合はOAuth M2Mまたはcustom-managed方式を検討する。

### 渡してもらう非機密情報

```text
site_url: https://<tenant>.sharepoint.com/sites/<site>
folder_url: <対象フォルダURL>
formats: CSV / Excel / PDF / DOCX
```

MicrosoftのパスワードやOAuth tokenを共有しない。ブラウザ上で本人が認証する。

### 合格テスト

- CSV、Excel、PDFを各1ファイル取り込める。
- ファイル更新が次回同期へ反映される。
- ファイル削除を検知できる。
- 元のSharePoint URLと更新日時を追跡できる。
- PDF本文をSilverへ抽出できる。

## 7. 契約前に決めること

各サービスについて次を決めてから契約する。

| 判断項目 | 確認内容 |
|---|---|
| データ量 | 初期容量、月間増加量、最大ファイルサイズ |
| 更新頻度 | 日次、時間単位、リアルタイム |
| データ所在地 | 日本/米国、クロスクラウド可否 |
| 保持期間 | 原本、Silver、ログを何年残すか |
| 機密性 | 個人情報、財務情報、社外秘の有無 |
| 利用者 | 管理者、開発者、Genie One利用者 |
| 障害要件 | 復旧時間、再処理可能期間 |
| 費用 | 保存、API、compute、外向き転送、ログ |

## 8. 推奨PoCデータ

どのソースにも同じ小さなデータセットを置く。

```text
companies.csv       企業マスター 10行
financials.csv      財務時系列 30行
employees.xlsx      人員情報 10行
annual_report.pdf   ダミー年次報告書 1件
```

次の3状態を用意する。

1. 初回: 全ファイルを配置
2. 差分: `financials.csv`へ1行追加
3. 変更/削除: 既存値を1件修正し、PDFを削除

これによりソースごとの差ではなく、接続方式・増分処理・削除検知の差を比較できる。

## 9. セキュリティ原則

- パスワード、API Key、Client Secret、SAS、AWS Secret KeyをGitへ保存しない。
- SecretはDatabricks Secretまたは各cloudのrole/managed identityで管理する。
- PoCでもPublic bucket/containerを使用しない。
- 外部接続は読み取り専用・限定prefixから始める。
- 個人アカウントではなく、運用移行時にService Principalへ切り替える。
- アクセスログとDatabricks audit logを保持する。

## 10. 解約・後片付け

PoC終了時に以下を確認する。

- 実データを削除または保持方針へ移行
- S3 bucket、Azure container、SharePoint siteの不要ファイルを削除
- IAM role、Service Principal、OAuth connectionの権限を失効
- Databricks External Location、Connection、pipelineを停止
- Secretを削除・ローテーション
- 自動更新、Versioning、ログによる残存課金を確認

## 11. 公式資料

- [S3 External Location](https://docs.databricks.com/aws/en/connect/unity-catalog/cloud-storage/s3/automatedsetups3)
- [AWS DatabricksからAzure Storageへ接続](https://docs.databricks.com/aws/en/connect/storage/azure-storage)
- [SharePoint ingestion setup](https://docs.databricks.com/aws/en/ingestion/lakeflow-connect/sharepoint-source-setup-overview)
- [Unity Catalog Volume](https://docs.databricks.com/aws/en/volumes/volume-files)
