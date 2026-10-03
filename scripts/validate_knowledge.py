"""Validate the committed official knowledge bundle before it is released."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.knowledge_sources import iter_official_chunks


def main() -> int:
    documents = list(iter_official_chunks())
    report = {
        "documents": len(documents),
        "chunks_by_source": dict(sorted(Counter(document["source_title"] for document in documents).items())),
        "source_titles": sorted({document["source_title"] for document in documents}),
        "source_hosts": sorted({document["source_url"].split("/", 3)[2] for document in documents}),
        "latest_fetch_date": max(document["source_fetched_at"] for document in documents),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
