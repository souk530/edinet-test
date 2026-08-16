from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from edinet_collector.storage import connect, save_document, save_xbrl
from edinet_collector.xbrl import parse_xbrl, parse_zip


SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
 xmlns:jp="http://example.jp/taxonomy">
  <xbrli:context id="CurrentYearInstant">
    <xbrli:entity>
      <xbrli:identifier scheme="http://disclosure.edinet-fsa.go.jp">E00001</xbrli:identifier>
      <xbrli:segment>
        <xbrldi:explicitMember dimension="jp:ConsolidatedAxis">jp:ConsolidatedMember</xbrldi:explicitMember>
      </xbrli:segment>
    </xbrli:entity>
    <xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>
  </xbrli:context>
  <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
  <jp:Assets contextRef="CurrentYearInstant" unitRef="JPY" decimals="-6">123000000</jp:Assets>
</xbrli:xbrl>
"""


class XbrlTest(unittest.TestCase):
    def test_parse_instance(self) -> None:
        parsed = parse_xbrl(io.BytesIO(SAMPLE), "sample.xbrl")
        self.assertEqual(1, len(parsed.facts))
        self.assertEqual("Assets", parsed.facts[0].local_name)
        self.assertEqual("123000000", parsed.facts[0].value)
        context = parsed.contexts[("sample.xbrl", "CurrentYearInstant")]
        self.assertEqual("2026-03-31", context.instant)
        self.assertEqual(
            "jp:ConsolidatedMember", context.dimensions["jp:ConsolidatedAxis"]
        )
        self.assertEqual("iso4217:JPY", parsed.units[("sample.xbrl", "JPY")])

    def test_parse_zip_and_save(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive_path = root / "document.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("XBRL/PublicDoc/report.xbrl", SAMPLE)
            parsed = parse_zip(archive_path)
            database = root / "test.sqlite3"
            connection = connect(database)
            save_document(
                connection,
                {"docID": "S100TEST", "filerName": "テスト株式会社"},
                "2026-06-30",
                archive_path,
            )
            connection.commit()
            save_xbrl(connection, "S100TEST", parsed)
            count = connection.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
            self.assertEqual(1, count)
            connection.close()


if __name__ == "__main__":
    unittest.main()
