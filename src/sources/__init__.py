"""Source connectors. Each returns canonical-schema records (see README)
plus writes its resolutions into the shared EntityResolver log."""
from __future__ import annotations

from datetime import datetime, timezone

from ..dates import iso_z

SCHEMA_VERSION = "1.0"


def record(record_type: str, source_name: str, source_url: str, content: dict) -> dict:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "recordType": record_type,
        "source": {"name": source_name, "url": source_url},
        "content": content,
        "collectedAt": iso_z(datetime.now(timezone.utc)),
    }
