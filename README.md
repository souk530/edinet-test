# EDINET Collector

Lakehouse化（Bronze → Silver → Gold）とDatabricks Genieへの公開計画は
[docs/edinet_lakehouse_roadmap.md](docs/edinet_lakehouse_roadmap.md) を参照してください。
Azure・S3・ローカル・SharePointの横断取り込みPoCは
[docs/multisource_ingestion_poc.md](docs/multisource_ingestion_poc.md) を参照してください。
外部ストレージをまだ契約していない状態からの導入手順は
[docs/external_storage_setup_guide.md](docs/external_storage_setup_guide.md) を参照してください。

図解マニュアルサイトは `site/` にあり、`main` ブランチへのpush後にGitHub Pagesへ
自動デプロイされます。

EDINET API v2から提出書類を取得し、ZIP内のXBRLを正規化してSQLiteへ保存する
Python CLIです。追加ライブラリなしで動作します。

## 収集するデータ

- `documents`: 提出者、EDINETコード、証券コード、書類種別、対象期間など
- `contexts`: 会計期間、連結・個別などのディメンション
- `units`: JPY、sharesなどの単位
- `facts`: 要素名、値、context、unit、decimals、nil情報

XBRL要素は年度や業種別タクソノミで変わるため、特定の勘定科目だけに限定せず、
全ファクトを保持します。

## セットアップ

Python 3.11以上を用意します。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

EDINETの利用登録画面でAPIキーを発行し、環境変数に設定します。

```bash
export EDINET_API_KEY='発行されたAPIキー'
```

## 使い方

### APIから収集

指定した提出日のXBRL付き書類をすべて収集します。

```bash
edinet-collector collect --from 2026-07-01 --to 2026-07-03
```

有価証券報告書（書類種別コード `120`）だけを最大10件収集する例:

```bash
edinet-collector collect \
  --from 2026-06-25 \
  --to 2026-06-30 \
  --doc-type-code 120 \
  --limit 10
```

企業を絞る場合:

```bash
edinet-collector collect \
  --from 2026-06-01 \
  --to 2026-07-01 \
  --edinet-code E00000
```

再実行時、既にあるZIPは再利用します。`--overwrite` を付けると再取得します。

取得した原本は、デフォルトで次のBronze配置へ保存します。

```text
data/bronze/document_lists/date=YYYY-MM-DD/documents.json
data/bronze/filing_zips/submit_date=YYYY-MM-DD/doc_id=S100XXXX/document.zip
```

前者は書類一覧APIの応答を再シリアライズせずそのまま保存し、後者はEDINETのXBRL ZIPを
そのまま保存します。保存先は `--list-dir` / `--download-dir` で変更できます。

### ダウンロード済みZIPを解析

```bash
edinet-collector parse path/to/S100XXXX.zip --doc-id S100XXXX
```

### CSVへ出力

```bash
edinet-collector export --output data/facts.csv
```

CSVはExcelでも開きやすいUTF-8（BOM付き）です。

## SQLiteでの利用例

売上高に相当するローカル名を探索:

```sql
SELECT d.filer_name, d.period_end, f.local_name, f.value, u.expression
FROM facts f
JOIN documents d ON d.doc_id = f.doc_id
LEFT JOIN units u
  ON u.doc_id = f.doc_id
 AND u.source_file = f.source_file
 AND u.unit_id = f.unit_ref
WHERE f.local_name LIKE '%Revenue%'
   OR f.local_name LIKE '%NetSales%'
ORDER BY d.period_end DESC;
```

同じ要素でも、`context_ref` が表す期間・連結個別・セグメントが異なる場合があります。
分析時は必ず `contexts` と結合してください。

## 注意事項

- EDINET API v2の利用にはAPIキーが必要です。
- 書類一覧は日付単位で取得します。長期間の初回収集は範囲を分けてください。
- この実装はEDINET ZIP内の標準XBRLインスタンス（`.xbrl`）を対象にします。
- 訂正報告書、重複context、タクソノミ差異は削らず原形に近い形で保存します。
- APIの利用条件、メンテナンス情報、最新仕様はEDINET公式サイトを確認してください。

## DatabricksでSilverテーブルを作成

> **注意:** 現在接続済みのDatabricks WorkspaceはAWS版です。以下のノートブックは
> Azure Blob向けの旧検証版であり、そのまま実行しないでください。接続環境と作成済みの
> Unity Catalog構成は [docs/databricks_environment.md](docs/databricks_environment.md)
> を参照してください。

[notebooks/edinet_zip_to_silver.py](notebooks/edinet_zip_to_silver.py) は、
Azure Blob Storage上のEDINET ZIPをDatabricksで並列解析するソースノートブックです。
Databricks WorkspaceへImportし、先頭のWidgetを設定して実行してください。

このノートブックは現時点では検証用です。本番化では、ZIP内のタイトルから書類種別を
推定せず、EDINET APIの書類一覧メタデータをBronzeへ保存して結合します。

ノートブック先頭の `DATABRICKS_ENV` セルへローカル `.env` と同じ値を記入すると、
そのノートブックのPythonプロセス内で環境変数として利用できます。共有ノートブックへ
認証値を保存したくない場合は、クラスタ環境変数またはDatabricks Secret Scopeも
利用できます。

ノートブックは書類種別コード120（有価証券報告書）だけを選別し、次のDeltaテーブルを
冪等に作成・更新します。

- `edinet_silver_documents`
- `edinet_silver_contexts`
- `edinet_silver_units`
- `edinet_silver_facts`
- `edinet_silver_ingestion_log`
