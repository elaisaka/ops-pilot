"""Load the curated, source-attributed official knowledge bundle."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any


REQUIRED_CHUNK_FIELDS = {"id", "title", "tags", "content", "section", "source_id"}
REQUIRED_SOURCE_FIELDS = {"source_type", "source_title", "source_url", "source_published_at", "source_fetched_at", "scope"}
OFFICIAL_HOSTS = {"www.samr.gov.cn", "xzfg.moj.gov.cn", "www.chinatax.gov.cn"}


def knowledge_path() -> Path:
    return Path(__file__).resolve().parents[1] / "knowledge" / "official_sources.jsonl"


def source_registry_path() -> Path:
    return Path(__file__).resolve().parents[1] / "knowledge" / "source_registry.json"


def iter_official_chunks() -> Iterator[dict[str, Any]]:
    """Yield validated curated summaries, not opaque scraped HTML."""
    sources = json.loads(source_registry_path().read_text(encoding="utf-8"))
    for line_number, line in enumerate(knowledge_path().read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        document = json.loads(line)
        missing = REQUIRED_CHUNK_FIELDS - document.keys()
        if missing:
            raise ValueError(f"official_sources.jsonl:{line_number} missing {sorted(missing)}")
        source = sources.get(document["source_id"])
        if source is None:
            raise ValueError(f"official_sources.jsonl:{line_number} has an unknown source_id")
        missing_source = REQUIRED_SOURCE_FIELDS - source.keys()
        if missing_source:
            raise ValueError(f"source_registry.json:{document['source_id']} missing {sorted(missing_source)}")
        if not source["source_url"].startswith("https://"):
            raise ValueError(f"official_sources.jsonl:{line_number} requires an HTTPS source")
        host = source["source_url"].split("/", 3)[2]
        if host not in OFFICIAL_HOSTS:
            raise ValueError(f"official_sources.jsonl:{line_number} is not an approved official host")
        yield {**document, **source}
