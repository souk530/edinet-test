from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"


class EdinetApiError(RuntimeError):
    """EDINET API request failed."""


@dataclass(slots=True)
class EdinetClient:
    api_key: str
    base_url: str = BASE_URL
    timeout: float = 60.0
    retries: int = 3
    user_agent: str = "edinet-collector/0.1"

    def _get(self, path: str, params: dict[str, str]) -> bytes:
        query = urllib.parse.urlencode({**params, "Subscription-Key": self.api_key})
        request = urllib.request.Request(
            f"{self.base_url}{path}?{query}",
            headers={"User-Agent": self.user_agent, "Accept": "*/*"},
        )
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if retryable and attempt < self.retries:
                    time.sleep(2**attempt)
                    continue
                raise EdinetApiError(
                    f"EDINET API returned HTTP {exc.code}: {body[:500]}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < self.retries:
                    time.sleep(2**attempt)
                    continue
                raise EdinetApiError(f"EDINET API request failed: {exc.reason}") from exc
        raise AssertionError("unreachable")

    def list_documents(
        self, date: str, raw_destination: Path | None = None
    ) -> list[dict[str, Any]]:
        """Return metadata for a date and optionally retain the exact API response."""
        raw = self._get("/documents.json", {"date": date, "type": "2"})
        if raw_destination is not None:
            raw_destination.parent.mkdir(parents=True, exist_ok=True)
            temp = raw_destination.with_suffix(raw_destination.suffix + ".part")
            temp.write_bytes(raw)
            temp.replace(raw_destination)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EdinetApiError("書類一覧APIからJSON以外の応答を受信しました") from exc
        metadata = payload.get("metadata", {})
        status = str(metadata.get("status", "200"))
        if status != "200":
            raise EdinetApiError(
                f"書類一覧APIエラー status={status}: {metadata.get('message', '')}"
            )
        return payload.get("results") or []

    def download_xbrl(self, doc_id: str, destination: Path) -> Path:
        """Download a document ZIP containing XBRL (document API type=1)."""
        raw = self._get(f"/documents/{urllib.parse.quote(doc_id)}", {"type": "1"})
        if raw[:2] != b"PK":
            detail = raw.decode("utf-8", errors="replace")[:500]
            raise EdinetApiError(f"{doc_id}: ZIP以外の応答を受信しました: {detail}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_suffix(destination.suffix + ".part")
        temp.write_bytes(raw)
        temp.replace(destination)
        return destination
