# Databricks notebook source
# MAGIC %md
# MAGIC # EDINET ZIP → Silver Delta tables
# MAGIC
# MAGIC Azure Blob Storage に保存した EDINET の ZIP を読み込み、書類種別コード `120`
# MAGIC （有価証券報告書）だけを Silver 層の Delta テーブルへ格納します。
# MAGIC
# MAGIC 作成するテーブル:
# MAGIC
# MAGIC - `edinet_silver_documents`: 書類単位の情報
# MAGIC - `edinet_silver_contexts`: XBRL context
# MAGIC - `edinet_silver_units`: XBRL unit
# MAGIC - `edinet_silver_facts`: XBRL fact
# MAGIC - `edinet_silver_ingestion_log`: 処理済み・除外・エラーの記録
# MAGIC
# MAGIC > ローカル PC の `.env` は Databricks へ自動では引き継がれません。
# MAGIC > 次のセルへ `.env` と同じ値を記入すると、このノートブックのPythonプロセス内で
# MAGIC > 環境変数として利用できます。共有ノートブックへ実値を保存しないでください。

# COMMAND ----------
# MAGIC %md
# MAGIC ## 1. `.env` の内容を環境変数へ設定
# MAGIC
# MAGIC 右辺の空文字を、ローカル `.env` にある同名キーの値へ置き換えて実行します。
# MAGIC `KEY=value` ではなく、Pythonの `"KEY": "value"` 形式で記入してください。
# MAGIC
# MAGIC この方法で設定した値の有効範囲は、現在のノートブックを実行している
# MAGIC Pythonプロセスです。値自体は画面へ表示しません。

# COMMAND ----------
import os

# Databricks上でこのセルだけ編集し、.envの値を右辺へ貼り付ける。
# 例: "STORAGE_ACCOUNT": "myaccount"
DATABRICKS_ENV = {
    "EDINET_API_KEY": "",
    "STORAGE_ACCOUNT": "",
    "CONTAINER": "",
    "STORAGE_KEY": "",
    "blob_base_url": "",
    "list_url": "",
}

# 空欄は既存のクラスタ環境変数を上書きしない。
for env_name, env_value in DATABRICKS_ENV.items():
    if env_value:
        os.environ[env_name] = env_value

# COMMAND ----------
# MAGIC %md
# MAGIC ## 2. 実行パラメータ
# MAGIC
# MAGIC `storage_account` と `container` は、クラスタ環境変数
# MAGIC `STORAGE_ACCOUNT` / `CONTAINER`（上のセルで設定した値を含む）があれば
# MAGIC 初期値として使用します。
# MAGIC `blob_prefix` はコンテナ直下をすべて対象にする場合は空欄のままにします。

# COMMAND ----------
import re

dbutils.widgets.text(
    "storage_account", os.getenv("STORAGE_ACCOUNT", ""), "01 storage account"
)
dbutils.widgets.text("container", os.getenv("CONTAINER", ""), "02 container")
dbutils.widgets.text("blob_prefix", "", "03 ZIP prefix (optional)")
dbutils.widgets.text("secret_scope", "", "04 secret scope (recommended)")
dbutils.widgets.text("secret_key", "STORAGE_KEY", "05 secret key name")
dbutils.widgets.text("catalog", "main", "06 Unity Catalog")
dbutils.widgets.text("schema", "edinet", "07 schema")
dbutils.widgets.text("table_prefix", "edinet_silver", "08 table prefix")

storage_account = dbutils.widgets.get("storage_account").strip()
container = dbutils.widgets.get("container").strip()
blob_prefix = dbutils.widgets.get("blob_prefix").strip().strip("/")
secret_scope = dbutils.widgets.get("secret_scope").strip()
secret_key_name = dbutils.widgets.get("secret_key").strip()
catalog = dbutils.widgets.get("catalog").strip()
target_schema = dbutils.widgets.get("schema").strip()
table_prefix = dbutils.widgets.get("table_prefix").strip()

if not storage_account or not container:
    raise ValueError(
        "storage_account と container をWidgetまたはクラスタ環境変数で指定してください"
    )

# SQL識別子へ任意の文字列が混入しないように制限する。
for name, value in {
    "catalog": catalog,
    "schema": target_schema,
    "table_prefix": table_prefix,
}.items():
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"{name} に使用できない文字が含まれています: {value!r}")

# COMMAND ----------
# MAGIC %md
# MAGIC ## 3. Azure Blob Storage の認証設定
# MAGIC
# MAGIC このセルは認証値を表示しません。通常は最初のセルで設定した環境変数
# MAGIC `STORAGE_KEY` を参照します。`secret_scope` Widgetを指定した場合だけ、
# MAGIC Secret Scopeの値を優先します。

# COMMAND ----------
if secret_scope:
    storage_key = dbutils.secrets.get(scope=secret_scope, key=secret_key_name)
else:
    storage_key = os.getenv("STORAGE_KEY")

if not storage_key:
    raise ValueError(
        "Secret Scope、またはクラスタ環境変数 STORAGE_KEY にストレージキーを設定してください"
    )

blob_host = f"{storage_account}.blob.core.windows.net"
spark.conf.set(f"fs.azure.account.key.{blob_host}", storage_key)

source_root = f"wasbs://{container}@{blob_host}"
source_path = f"{source_root}/{blob_prefix}" if blob_prefix else source_root

# 不要になったPython側の参照を落とす。Spark設定値は以降のストレージ読み込みで使用される。
del storage_key

# COMMAND ----------
# MAGIC %md
# MAGIC ## 4. ZIPをXBRLレコードへ変換する関数
# MAGIC
# MAGIC `binaryFile` で各ZIPをSpark executorへ配り、ZIP内の
# MAGIC `XBRL/PublicDoc/*.xbrl` を解析します。監査報告書の `AuditDoc` は対象外です。
# MAGIC
# MAGIC ZIPファイル単体にはEDINET APIの `docTypeCode` が明示されないため、
# MAGIC `DocumentTitleCoverPage` が正規化後に「有価証券報告書」と完全一致する書類を
# MAGIC コード120として採用します。「訂正有価証券報告書」などは除外されます。

# COMMAND ----------
import io
import json
import zipfile
import xml.etree.ElementTree as ET

from pyspark.sql import Row
from pyspark.sql import functions as F
from pyspark.sql import types as T

XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
XSI = "http://www.w3.org/2001/XMLSchema-instance"


def split_tag(tag):
    """Clark記法のXMLタグを namespace と local name に分ける。"""
    if tag.startswith("{"):
        namespace, local_name = tag[1:].split("}", 1)
        return namespace, local_name
    return "", tag


def normalized_text(element):
    """要素配下のテキストを結合し、連続する空白を1文字へ正規化する。"""
    return " ".join("".join(element.itertext()).split())


def parse_context(element):
    identifier = element.find(f".//{{{XBRLI}}}identifier")
    period = element.find(f"./{{{XBRLI}}}period")

    period_type = instant = start_date = end_date = None
    if period is not None:
        instant_element = period.find(f"./{{{XBRLI}}}instant")
        start_element = period.find(f"./{{{XBRLI}}}startDate")
        end_element = period.find(f"./{{{XBRLI}}}endDate")
        forever_element = period.find(f"./{{{XBRLI}}}forever")
        if instant_element is not None:
            period_type = "instant"
            instant = normalized_text(instant_element)
        elif start_element is not None and end_element is not None:
            period_type = "duration"
            start_date = normalized_text(start_element)
            end_date = normalized_text(end_element)
        elif forever_element is not None:
            period_type = "forever"

    dimensions = {}
    for member in element.findall(f".//{{{XBRLDI}}}explicitMember"):
        dimensions[member.attrib.get("dimension", "")] = normalized_text(member)
    for member in element.findall(f".//{{{XBRLDI}}}typedMember"):
        dimensions[member.attrib.get("dimension", "")] = normalized_text(member)

    return {
        "context_id": element.attrib["id"],
        "entity_identifier": (
            normalized_text(identifier) if identifier is not None else None
        ),
        "entity_scheme": (
            identifier.attrib.get("scheme") if identifier is not None else None
        ),
        "period_type": period_type,
        "instant": instant,
        "start_date": start_date,
        "end_date": end_date,
        "dimensions_json": json.dumps(
            dimensions, ensure_ascii=False, sort_keys=True
        ),
    }


def parse_unit(element):
    measures = [
        normalized_text(item)
        for item in element.findall(f"./{{{XBRLI}}}measure")
    ]
    if measures:
        return " * ".join(measures)

    numerator = [
        normalized_text(item)
        for item in element.findall(
            f".//{{{XBRLI}}}unitNumerator/{{{XBRLI}}}measure"
        )
    ]
    denominator = [
        normalized_text(item)
        for item in element.findall(
            f".//{{{XBRLI}}}unitDenominator/{{{XBRLI}}}measure"
        )
    ]
    return f"{' * '.join(numerator)} / {' * '.join(denominator)}"


# 書類テーブルへ昇格させる代表的なDEI要素。
DOCUMENT_FIELDS = {
    "DocumentTitleCoverPage": "document_title",
    "EDINETCodeDEI": "edinet_code",
    "SecurityCodeDEI": "sec_code",
    "FilerNameInJapaneseDEI": "filer_name",
    "CurrentFiscalYearStartDateDEI": "period_start",
    "CurrentFiscalYearEndDateDEI": "period_end",
    "FilingDateDEI": "submit_date",
}


def blank_record():
    """全レコード種別で共用する、nullableな中間レコードを返す。"""
    return {
        "record_type": None,
        "status": None,
        "doc_id": None,
        "source_file": None,
        "source_path": None,
        "source_modification_time": None,
        "source_length": None,
        "doc_type_code": None,
        "document_title": None,
        "edinet_code": None,
        "sec_code": None,
        "filer_name": None,
        "period_start": None,
        "period_end": None,
        "submit_date": None,
        "context_id": None,
        "entity_identifier": None,
        "entity_scheme": None,
        "period_type": None,
        "instant": None,
        "start_date": None,
        "end_date": None,
        "dimensions_json": None,
        "unit_id": None,
        "unit_expression": None,
        "fact_index": None,
        "concept": None,
        "namespace_uri": None,
        "local_name": None,
        "context_ref": None,
        "unit_ref": None,
        "decimals": None,
        "precision": None,
        "value": None,
        "is_nil": None,
        "error_class": None,
        "error_message": None,
    }


def make_record(record_type, common, **values):
    record = blank_record()
    record.update(common)
    record.update(values)
    record["record_type"] = record_type
    return Row(**record)


def parse_zip_partition(rows):
    """1 partition分のZIPを逐次処理し、中間レコードをyieldする。"""
    for source in rows:
        path = source.path
        doc_id = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        common = {
            "doc_id": doc_id,
            "source_path": path,
            "source_modification_time": source.modificationTime,
            "source_length": source.length,
        }

        try:
            contexts = []
            units = []
            facts = []
            document_values = {}

            with zipfile.ZipFile(io.BytesIO(bytes(source.content))) as archive:
                xbrl_files = sorted(
                    name
                    for name in archive.namelist()
                    if name.lower().endswith(".xbrl")
                    and "/publicdoc/" in f"/{name.lower()}"
                    and not name.endswith("/")
                )
                if not xbrl_files:
                    raise ValueError("XBRL/PublicDoc 配下に .xbrl がありません")

                for source_file in xbrl_files:
                    with archive.open(source_file) as member:
                        root = ET.parse(member).getroot()

                    for child in root:
                        namespace, local_name = split_tag(child.tag)
                        if namespace == XBRLI and local_name == "context":
                            context = parse_context(child)
                            contexts.append(
                                make_record(
                                    "context",
                                    common,
                                    source_file=source_file,
                                    **context,
                                )
                            )
                        elif namespace == XBRLI and local_name == "unit":
                            units.append(
                                make_record(
                                    "unit",
                                    common,
                                    source_file=source_file,
                                    unit_id=child.attrib["id"],
                                    unit_expression=parse_unit(child),
                                )
                            )

                    fact_index = 0
                    for element in root.iter():
                        context_ref = element.attrib.get("contextRef")
                        if not context_ref:
                            continue

                        namespace, local_name = split_tag(element.tag)
                        is_nil = element.attrib.get(
                            f"{{{XSI}}}nil", "false"
                        ).lower() in {"true", "1"}
                        value = None if is_nil else normalized_text(element)

                        document_field = DOCUMENT_FIELDS.get(local_name)
                        if document_field and value and document_field not in document_values:
                            document_values[document_field] = value

                        facts.append(
                            make_record(
                                "fact",
                                common,
                                source_file=source_file,
                                fact_index=fact_index,
                                concept=element.tag,
                                namespace_uri=namespace,
                                local_name=local_name,
                                context_ref=context_ref,
                                unit_ref=element.attrib.get("unitRef"),
                                decimals=element.attrib.get("decimals"),
                                precision=element.attrib.get("precision"),
                                value=value,
                                is_nil=is_nil,
                            )
                        )
                        fact_index += 1

            title = document_values.get("document_title")
            # ZIP内にAPIのdocTypeCodeはないため、表紙タイトルを厳密一致させる。
            if title != "有価証券報告書":
                yield make_record(
                    "log",
                    common,
                    status="excluded",
                    document_title=title,
                    error_message="DocumentTitleCoverPage が有価証券報告書ではありません",
                )
                continue

            document_common = {
                **common,
                "doc_type_code": "120",
                **document_values,
            }
            yield make_record("document", document_common)
            yield from contexts
            yield from units
            yield from facts
            yield make_record(
                "log",
                document_common,
                status="processed",
            )

        except Exception as exc:
            # 1件の破損ZIPでジョブ全体を止めず、原因をログテーブルへ残す。
            yield make_record(
                "log",
                common,
                status="error",
                error_class=type(exc).__name__,
                error_message=str(exc)[:4000],
            )

# COMMAND ----------
# MAGIC %md
# MAGIC ## 5. Blob上のZIPを読み込み
# MAGIC
# MAGIC 疎通確認用の一覧取得はせず、このセルで処理対象のZIPを直接読み込みます。
# MAGIC `recursiveFileLookup` により日付フォルダ等の配下も再帰的に対象にします。

# COMMAND ----------
binary_zip_df = (
    spark.read.format("binaryFile")
    .option("pathGlobFilter", "*.zip")
    .option("recursiveFileLookup", "true")
    .load(source_path)
)

record_schema = T.StructType(
    [
        T.StructField("record_type", T.StringType()),
        T.StructField("status", T.StringType()),
        T.StructField("doc_id", T.StringType()),
        T.StructField("source_file", T.StringType()),
        T.StructField("source_path", T.StringType()),
        T.StructField("source_modification_time", T.TimestampType()),
        T.StructField("source_length", T.LongType()),
        T.StructField("doc_type_code", T.StringType()),
        T.StructField("document_title", T.StringType()),
        T.StructField("edinet_code", T.StringType()),
        T.StructField("sec_code", T.StringType()),
        T.StructField("filer_name", T.StringType()),
        T.StructField("period_start", T.StringType()),
        T.StructField("period_end", T.StringType()),
        T.StructField("submit_date", T.StringType()),
        T.StructField("context_id", T.StringType()),
        T.StructField("entity_identifier", T.StringType()),
        T.StructField("entity_scheme", T.StringType()),
        T.StructField("period_type", T.StringType()),
        T.StructField("instant", T.StringType()),
        T.StructField("start_date", T.StringType()),
        T.StructField("end_date", T.StringType()),
        T.StructField("dimensions_json", T.StringType()),
        T.StructField("unit_id", T.StringType()),
        T.StructField("unit_expression", T.StringType()),
        T.StructField("fact_index", T.LongType()),
        T.StructField("concept", T.StringType()),
        T.StructField("namespace_uri", T.StringType()),
        T.StructField("local_name", T.StringType()),
        T.StructField("context_ref", T.StringType()),
        T.StructField("unit_ref", T.StringType()),
        T.StructField("decimals", T.StringType()),
        T.StructField("precision", T.StringType()),
        T.StructField("value", T.StringType()),
        T.StructField("is_nil", T.BooleanType()),
        T.StructField("error_class", T.StringType()),
        T.StructField("error_message", T.StringType()),
    ]
)

records_df = (
    spark.createDataFrame(
        binary_zip_df.rdd.mapPartitions(parse_zip_partition),
        schema=record_schema,
    )
    .withColumn("ingested_at", F.current_timestamp())
    .persist()
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 6. Silverテーブルを作成してMERGE
# MAGIC
# MAGIC ZIPは通常不変ですが、再実行時にも同一キーを重複登録しないようDelta `MERGE`
# MAGIC を使います。`facts` のキーは
# MAGIC `(doc_id, source_file, fact_index)` です。

# COMMAND ----------
from delta.tables import DeltaTable

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{target_schema}`")

table_names = {
    "documents": f"{catalog}.{target_schema}.{table_prefix}_documents",
    "contexts": f"{catalog}.{target_schema}.{table_prefix}_contexts",
    "units": f"{catalog}.{target_schema}.{table_prefix}_units",
    "facts": f"{catalog}.{target_schema}.{table_prefix}_facts",
    "ingestion_log": f"{catalog}.{target_schema}.{table_prefix}_ingestion_log",
}

documents_df = records_df.filter("record_type = 'document'").select(
    "doc_id",
    "doc_type_code",
    "document_title",
    "edinet_code",
    "sec_code",
    "filer_name",
    F.to_date("period_start").alias("period_start"),
    F.to_date("period_end").alias("period_end"),
    F.to_date("submit_date").alias("submit_date"),
    "source_path",
    "source_modification_time",
    "source_length",
    "ingested_at",
)

contexts_df = records_df.filter("record_type = 'context'").select(
    "doc_id",
    "source_file",
    "context_id",
    "entity_identifier",
    "entity_scheme",
    "period_type",
    F.to_date("instant").alias("instant"),
    F.to_date("start_date").alias("start_date"),
    F.to_date("end_date").alias("end_date"),
    "dimensions_json",
    "ingested_at",
)

units_df = records_df.filter("record_type = 'unit'").select(
    "doc_id",
    "source_file",
    "unit_id",
    "unit_expression",
    "ingested_at",
)

facts_df = records_df.filter("record_type = 'fact'").select(
    "doc_id",
    "source_file",
    "fact_index",
    "concept",
    "namespace_uri",
    "local_name",
    "context_ref",
    "unit_ref",
    "decimals",
    "precision",
    "value",
    "is_nil",
    "ingested_at",
)

logs_df = records_df.filter("record_type = 'log'").select(
    "doc_id",
    "source_path",
    "source_modification_time",
    "source_length",
    "status",
    "doc_type_code",
    "document_title",
    "error_class",
    "error_message",
    "ingested_at",
)


def merge_delta(source_df, table_name, key_columns):
    """テーブルがなければ作成し、あれば指定キーで全列をupsertする。"""
    if not spark.catalog.tableExists(table_name):
        (
            source_df.limit(0)
            .write.format("delta")
            .mode("overwrite")
            .saveAsTable(table_name)
        )

    condition = " AND ".join(
        f"target.`{column}` <=> source.`{column}`" for column in key_columns
    )
    (
        DeltaTable.forName(spark, table_name)
        .alias("target")
        .merge(source_df.alias("source"), condition)
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )


merge_delta(documents_df, table_names["documents"], ["doc_id"])
merge_delta(
    contexts_df,
    table_names["contexts"],
    ["doc_id", "source_file", "context_id"],
)
merge_delta(
    units_df,
    table_names["units"],
    ["doc_id", "source_file", "unit_id"],
)
merge_delta(
    facts_df,
    table_names["facts"],
    ["doc_id", "source_file", "fact_index"],
)
merge_delta(
    logs_df,
    table_names["ingestion_log"],
    ["source_path", "source_modification_time"],
)

# COMMAND ----------
# MAGIC %md
# MAGIC ## 7. 実行結果
# MAGIC
# MAGIC 最後に今回の取込ステータスと、作成したテーブル名を表示します。
# MAGIC 認証情報は表示しません。

# COMMAND ----------
display(
    logs_df.groupBy("status")
    .count()
    .orderBy(F.col("status").asc_nulls_last())
)

for logical_name, full_name in table_names.items():
    print(f"{logical_name}: {full_name}")

# 結果表示まで完了したため、ZIP解析結果のキャッシュを解放する。
records_df.unpersist()

# COMMAND ----------
# MAGIC %md
# MAGIC ## 分析クエリ例
# MAGIC
# MAGIC 同一要素でも、contextが示す期間・連結個別・セグメントが異なります。
# MAGIC fact単体ではなくcontextと結合して利用してください。

# COMMAND ----------
facts_table = table_names["facts"]
documents_table = table_names["documents"]
contexts_table = table_names["contexts"]
units_table = table_names["units"]

analysis_example_df = spark.sql(
    f"""
    SELECT
      d.doc_id,
      d.filer_name,
      d.period_end,
      f.local_name,
      f.value,
      c.period_type,
      c.instant,
      c.start_date,
      c.end_date,
      c.dimensions_json,
      u.unit_expression
    FROM `{catalog}`.`{target_schema}`.`{table_prefix}_facts` AS f
    INNER JOIN `{catalog}`.`{target_schema}`.`{table_prefix}_documents` AS d
      ON d.doc_id = f.doc_id
    LEFT JOIN `{catalog}`.`{target_schema}`.`{table_prefix}_contexts` AS c
      ON c.doc_id = f.doc_id
     AND c.source_file = f.source_file
     AND c.context_id = f.context_ref
    LEFT JOIN `{catalog}`.`{target_schema}`.`{table_prefix}_units` AS u
      ON u.doc_id = f.doc_id
     AND u.source_file = f.source_file
     AND u.unit_id = f.unit_ref
    WHERE f.local_name IN (
      'NetSales',
      'Revenue',
      'OperatingIncomeLoss',
      'ProfitLoss'
    )
    ORDER BY d.period_end DESC, d.doc_id, f.local_name
    """
)

display(analysis_example_df)
