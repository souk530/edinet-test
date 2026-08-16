CREATE OR REPLACE TABLE workspace.edinet_silver.poc_local_facts
COMMENT 'PoC: local EDINET facts CSV ingested through a Unity Catalog managed Volume'
TBLPROPERTIES (
  'quality' = 'silver',
  'source.system' = 'local',
  'pipeline.mode' = 'poc'
)
AS
SELECT
  CAST(doc_id AS STRING) AS doc_id,
  TRY_CAST(submit_date AS DATE) AS submit_date,
  CAST(edinet_code AS STRING) AS edinet_code,
  CAST(sec_code AS STRING) AS sec_code,
  CAST(filer_name AS STRING) AS filer_name,
  CAST(doc_type_code AS STRING) AS doc_type_code,
  CAST(source_file AS STRING) AS xbrl_source_file,
  CAST(namespace_uri AS STRING) AS namespace_uri,
  CAST(local_name AS STRING) AS local_name,
  CAST(context_ref AS STRING) AS context_ref,
  CAST(period_type AS STRING) AS period_type,
  TRY_CAST(instant AS DATE) AS instant,
  TRY_CAST(start_date AS DATE) AS start_date,
  TRY_CAST(end_date AS DATE) AS end_date,
  CAST(dimensions_json AS STRING) AS dimensions_json,
  CAST(unit_ref AS STRING) AS unit_ref,
  CAST(unit_expression AS STRING) AS unit_expression,
  CAST(decimals AS STRING) AS decimals,
  CAST(value AS STRING) AS raw_value,
  TRY_CAST(is_nil AS BOOLEAN) AS is_nil,
  'local' AS source_system,
  _metadata.file_path AS source_uri,
  _metadata.file_name AS source_file_name,
  _metadata.file_modification_time AS source_modified_at,
  _metadata.file_size AS source_size,
  current_timestamp() AS ingested_at,
  'poc-local-v1' AS schema_version,
  'active' AS record_status
FROM read_files(
  '/Volumes/workspace/edinet_bronze/raw/local/facts.csv',
  format => 'csv',
  header => true,
  inferSchema => true
);
