from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from edinet_collector.api import EdinetClient


class EdinetClientTest(unittest.TestCase):
    def test_list_documents_retains_exact_response(self) -> None:
        payload = {
            "metadata": {"status": "200"},
            "results": [{"docID": "S100TEST", "docTypeCode": "120"}],
        }
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "date=2026-08-15" / "documents.json"
            client = EdinetClient(api_key="test-key")
            with patch.object(EdinetClient, "_get", return_value=raw):
                results = client.list_documents(
                    "2026-08-15", raw_destination=destination
                )

            self.assertEqual(results, payload["results"])
            self.assertEqual(destination.read_bytes(), raw)
            self.assertFalse(destination.with_suffix(".json.part").exists())


if __name__ == "__main__":
    unittest.main()
