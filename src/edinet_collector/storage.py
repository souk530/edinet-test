from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from .xbrl import ParsedXbrl, context_dimensions_json


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    submit_date TEXT NOT NULL,
    edinet_code TEXT,
    sec_code TEXT,
    filer_name TEXT,
    doc_type_code TEXT,
    doc_description TEXT,
    period_start TEXT,
    period_end TEXT,
    submit_datetime TEXT,
    metadata_json TEXT NOT NULL,
    zip_path TEXT,
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS contexts (
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    source_file TEXT NOT NULL,
    context_id TEXT NOT NULL,
    entity_identifier TEXT,
    entity_scheme TEXT,
    period_type TEXT,
    instant TEXT,
    start_date TEXT,
    end_date TEXT,
    dimensions_json TEXT NOT NULL,
    PRIMARY KEY (doc_id, source_file, context_id)
);
CREATE TABLE IF NOT EXISTS units (
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    source_file TEXT NOT NULL,
    unit_id TEXT NOT NULL,
    expression TEXT NOT NULL,
    PRIMARY KEY (doc_id, source_file, unit_id)
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    source_file TEXT NOT NULL,
    concept TEXT NOT NULL,
    namespace_uri TEXT NOT NULL,
    local_name TEXT NOT NULL,
    context_ref TEXT NOT NULL,
    unit_ref TEXT,
    decimals TEXT,
    precision TEXT,
    value TEXT,
    is_nil INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_doc_concept_idx ON facts(doc_id, local_name);
CREATE INDEX IF NOT EXISTS documents_submit_date_idx ON documents(submit_date);
CREATE INDEX IF NOT EXISTS documents_edinet_code_idx ON documents(edinet_code);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    return connection


def save_document(
    connection: sqlite3.Connection,
    metadata: dict[str, Any],
    submit_date: str,
    zip_path: Path | None = None,
) -> None:
    doc_id = metadata["docID"]
    connection.execute(
        """
        INSERT INTO documents (
            doc_id, submit_date, edinet_code, sec_code, filer_name,
            doc_type_code, doc_description, period_start, period_end,
            submit_datetime, metadata_json, zip_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(doc_id) DO UPDATE SET
            submit_date=excluded.submit_date,
            edinet_code=excluded.edinet_code,
            sec_code=excluded.sec_code,
            filer_name=excluded.filer_name,
            doc_type_code=excluded.doc_type_code,
            doc_description=excluded.doc_description,
            period_start=excluded.period_start,
            period_end=excluded.period_end,
            submit_datetime=excluded.submit_datetime,
            metadata_json=excluded.metadata_json,
            zip_path=COALESCE(excluded.zip_path, documents.zip_path)
        """,
        (
            doc_id,
            submit_date,
            metadata.get("edinetCode"),
            metadata.get("secCode"),
            metadata.get("filerName"),
            metadata.get("docTypeCode"),
            metadata.get("docDescription"),
            metadata.get("periodStart"),
            metadata.get("periodEnd"),
            metadata.get("submitDateTime"),
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            str(zip_path) if zip_path else None,
        ),
    )


def save_xbrl(
    connection: sqlite3.Connection, doc_id: str, parsed: ParsedXbrl
) -> None:
    with connection:
        connection.execute("DELETE FROM facts WHERE doc_id = ?", (doc_id,))
        connection.execute("DELETE FROM contexts WHERE doc_id = ?", (doc_id,))
        connection.execute("DELETE FROM units WHERE doc_id = ?", (doc_id,))
        connection.executemany(
            """
            INSERT INTO contexts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    doc_id,
                    context.source_file,
                    context.context_id,
                    context.entity_identifier,
                    context.entity_scheme,
                    context.period_type,
                    context.instant,
                    context.start_date,
                    context.end_date,
                    context_dimensions_json(context),
                )
                for context in parsed.contexts.values()
            ],
        )
        connection.executemany(
            "INSERT INTO units VALUES (?, ?, ?, ?)",
            [
                (doc_id, source_file, unit_id, value)
                for (source_file, unit_id), value in parsed.units.items()
            ],
        )
        connection.executemany(
            """
            INSERT INTO facts (
                doc_id, source_file, concept, namespace_uri, local_name,
                context_ref, unit_ref, decimals, precision, value, is_nil
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    doc_id,
                    fact.source_file,
                    fact.concept,
                    fact.namespace_uri,
                    fact.local_name,
                    fact.context_ref,
                    fact.unit_ref,
                    fact.decimals,
                    fact.precision,
                    fact.value,
                    int(fact.is_nil),
                )
                for fact in parsed.facts
            ],
        )


def export_facts_csv(connection: sqlite3.Connection, output: Path) -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    cursor = connection.execute(
        """
        SELECT d.doc_id, d.submit_date, d.edinet_code, d.sec_code, d.filer_name,
               d.doc_type_code, f.source_file, f.namespace_uri, f.local_name,
               f.context_ref, c.period_type, c.instant, c.start_date, c.end_date,
               c.dimensions_json, f.unit_ref, u.expression AS unit_expression,
               f.decimals, f.value, f.is_nil
        FROM facts f
        JOIN documents d ON d.doc_id = f.doc_id
        LEFT JOIN contexts c
          ON c.doc_id = f.doc_id
         AND c.source_file = f.source_file
         AND c.context_id = f.context_ref
        LEFT JOIN units u
          ON u.doc_id = f.doc_id
         AND u.source_file = f.source_file
         AND u.unit_id = f.unit_ref
        ORDER BY d.submit_date, d.doc_id, f.id
        """
    )
    headers = [column[0] for column in cursor.description]
    count = 0
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        for row in cursor:
            writer.writerow(row)
            count += 1
    return count
