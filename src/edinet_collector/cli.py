from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from .api import EdinetApiError, EdinetClient
from .storage import connect, export_facts_csv, save_document, save_xbrl
from .xbrl import parse_zip


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日付は YYYY-MM-DD 形式で指定してください") from exc


def _dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _matches(document: dict, args: argparse.Namespace) -> bool:
    if str(document.get("xbrlFlag")) != "1":
        return False
    if args.doc_type_code and document.get("docTypeCode") not in args.doc_type_code:
        return False
    if args.edinet_code and document.get("edinetCode") not in args.edinet_code:
        return False
    if args.sec_code and document.get("secCode") not in args.sec_code:
        return False
    return True


def collect(args: argparse.Namespace) -> int:
    api_key = args.api_key or os.environ.get("EDINET_API_KEY")
    if not api_key:
        print(
            "エラー: --api-key または環境変数 EDINET_API_KEY を設定してください",
            file=sys.stderr,
        )
        return 2
    if args.to_date < args.from_date:
        print("エラー: --to は --from 以降の日付にしてください", file=sys.stderr)
        return 2

    client = EdinetClient(api_key=api_key)
    connection = connect(args.database)
    total_documents = 0
    total_facts = 0
    try:
        stop = False
        for target_date in _dates(args.from_date, args.to_date):
            day = target_date.isoformat()
            raw_list_path = args.list_dir / f"date={day}" / "documents.json"
            documents = client.list_documents(day, raw_destination=raw_list_path)
            selected = [item for item in documents if _matches(item, args)]
            print(f"{day}: {len(documents)}件中 {len(selected)}件が対象")
            for document in selected:
                if args.limit is not None and total_documents >= args.limit:
                    stop = True
                    break
                doc_id = document["docID"]
                zip_path = (
                    args.download_dir
                    / f"submit_date={day}"
                    / f"doc_id={doc_id}"
                    / "document.zip"
                )
                if not zip_path.exists() or args.overwrite:
                    client.download_xbrl(doc_id, zip_path)
                    if args.interval:
                        time.sleep(args.interval)
                save_document(connection, document, day, zip_path)
                connection.commit()
                parsed = parse_zip(zip_path)
                save_xbrl(connection, doc_id, parsed)
                total_documents += 1
                total_facts += len(parsed.facts)
                print(
                    f"  {doc_id} {document.get('filerName', '')}: "
                    f"{len(parsed.facts)} facts"
                )
            if stop:
                break
    finally:
        connection.close()
    print(f"完了: {total_documents}書類、{total_facts}ファクト")
    return 0


def parse_local(args: argparse.Namespace) -> int:
    parsed = parse_zip(args.zip_file)
    doc_id = args.doc_id or args.zip_file.stem
    metadata = {
        "docID": doc_id,
        "filerName": args.filer_name,
        "docDescription": "local ZIP",
    }
    connection = connect(args.database)
    try:
        save_document(connection, metadata, args.submit_date, args.zip_file)
        connection.commit()
        save_xbrl(connection, doc_id, parsed)
    finally:
        connection.close()
    print(
        f"{doc_id}: {len(parsed.source_files)}ファイル、"
        f"{len(parsed.contexts)}コンテキスト、{len(parsed.facts)}ファクトを保存"
    )
    return 0


def export_csv(args: argparse.Namespace) -> int:
    connection = connect(args.database)
    try:
        count = export_facts_csv(connection, args.output)
    finally:
        connection.close()
    print(f"{args.output}: {count}行を書き出しました")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="edinet-collector",
        description="EDINETからXBRL構造化データを収集します",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect_parser = subparsers.add_parser("collect", help="APIから書類を収集")
    collect_parser.add_argument("--from", dest="from_date", type=_iso_date, required=True)
    collect_parser.add_argument("--to", dest="to_date", type=_iso_date)
    collect_parser.add_argument("--api-key", help="省略時は EDINET_API_KEY を使用")
    collect_parser.add_argument(
        "--doc-type-code", action="append", help="書類種別コード（複数指定可）"
    )
    collect_parser.add_argument(
        "--edinet-code", action="append", help="EDINETコード（複数指定可）"
    )
    collect_parser.add_argument(
        "--sec-code", action="append", help="証券コード（複数指定可）"
    )
    collect_parser.add_argument("--limit", type=int, help="取得する最大書類数")
    collect_parser.add_argument(
        "--database", type=Path, default=Path("data/edinet.sqlite3")
    )
    collect_parser.add_argument(
        "--list-dir",
        type=Path,
        default=Path("data/bronze/document_lists"),
        help="書類一覧APIの生JSON保存先",
    )
    collect_parser.add_argument(
        "--download-dir", type=Path, default=Path("data/bronze/filing_zips")
    )
    collect_parser.add_argument(
        "--interval", type=float, default=0.2, help="ダウンロード間隔（秒）"
    )
    collect_parser.add_argument("--overwrite", action="store_true")
    collect_parser.set_defaults(func=collect)

    parse_parser = subparsers.add_parser("parse", help="ローカルのEDINET ZIPを解析")
    parse_parser.add_argument("zip_file", type=Path)
    parse_parser.add_argument("--doc-id")
    parse_parser.add_argument("--filer-name")
    parse_parser.add_argument(
        "--submit-date", default=date.today().isoformat(), type=str
    )
    parse_parser.add_argument(
        "--database", type=Path, default=Path("data/edinet.sqlite3")
    )
    parse_parser.set_defaults(func=parse_local)

    export_parser = subparsers.add_parser("export", help="全ファクトをCSVへ出力")
    export_parser.add_argument(
        "--database", type=Path, default=Path("data/edinet.sqlite3")
    )
    export_parser.add_argument(
        "--output", type=Path, default=Path("data/facts.csv")
    )
    export_parser.set_defaults(func=export_csv)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "to_date", None) is None and hasattr(args, "from_date"):
        args.to_date = args.from_date
    try:
        return args.func(args)
    except (EdinetApiError, ValueError, OSError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
