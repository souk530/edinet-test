# Databricks環境

確認日: 2026-08-16

## Workspace

- Cloud: AWS (`us-west-2` metastore)
- Host: `https://dbc-059f6db4-6782.cloud.databricks.com`
- Workspace ID: `7474657115075241`
- CLI profile: `edinet-dev`（OAuth U2M。資格情報はリポジトリ外で管理）
- SQL Warehouse: `Serverless Starter Warehouse`
- Warehouse ID: `053676116f32122e`

## Unity Catalog

| レイヤ | オブジェクト | 用途 |
|---|---|---|
| Bronze | `workspace.edinet_bronze` | 原本と取得台帳 |
| Bronze Volume | `workspace.edinet_bronze.raw` | EDINET一覧JSON・ZIP |
| Silver | `workspace.edinet_silver` | XBRL正規化テーブル |
| Gold | `workspace.edinet_gold` | 標準財務指標・Genie用ビュー |

Volumeの論理パスは `/Volumes/workspace/edinet_bronze/raw` を使用する。物理S3パスは
Unity Catalogが管理するため、ジョブやノートブックへ直接記述しない。

## 認証方針

- 開発時のCLI操作: OAuth user-to-machine (`edinet-dev`)
- Databricks Workflow: Databricks内の実行IDまたはService Principal
- EDINET APIキー: Databricks Secretへ保存し、ノートブックへ直書きしない
- AWSストレージ: Unity Catalog managed storageを利用し、AWSキーをコードへ置かない

作成済みSecret Scopeは `edinet`。APIキーは `edinet-api-key` というキー名で登録し、
ノートブックから `dbutils.secrets.get("edinet", "edinet-api-key")` で参照する。

## 注意

既存 `notebooks/edinet_zip_to_silver.py` はAzure Blobの `wasbs://` とストレージキーを
前提にした検証版であり、このWorkspaceでは使用しない。AWS/Unity Catalog Volume対応版へ
置き換えてから実行する。
