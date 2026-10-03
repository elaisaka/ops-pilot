"""Explicit model download, genuine embedding, persistent FAISS build and dev-only calibration."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag_retrieval import (LocalEncoder, RagConfig, corpus_identity, document_text, sha256)
from app.store import Store


def calibrate(positive_rows: list[dict], negative_scores: list[float]) -> dict:
    # Fixed before blind/external evaluation: grid 0.00..1.00, equal weight
    # source recall, section recall and negative rejection; ties prefer rejection then higher threshold.
    trials = []
    for step in range(101):
        threshold = step / 100
        source = sum(any(hit["source_hit"] and hit["score"] >= threshold for hit in row["hits"])
                     for row in positive_rows) / len(positive_rows)
        section = sum(any(hit["section_hit"] and hit["score"] >= threshold for hit in row["hits"])
                      for row in positive_rows) / len(positive_rows)
        rejection = sum(score < threshold for score in negative_scores) / len(negative_scores)
        trials.append({"threshold": threshold, "source_recall_at_3": source, "section_recall_at_3": section,
                       "rejection_accuracy": rejection, "objective": (source + section + rejection) / 3})
    selected = max(trials, key=lambda row: (row["objective"], row["rejection_accuracy"], row["threshold"]))
    return {"method": "grid 0..1 step .01; mean(source R@3, section R@3, negative rejection); ties rejection then higher threshold",
            "selected": selected, "trials": trials, "positive_scores": positive_rows, "negative_top1_scores": negative_scores}


def main() -> int:
    import faiss
    from app.knowledge_sources import iter_official_chunks
    from evals.run_retrieval_eval import load_cases, source_matches

    start = perf_counter()
    config = RagConfig.from_env()
    encoder = LocalEncoder(config, allow_download=True)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "build.db")
        try:
            documents = store.list_knowledge()
        finally:
            store.close()
    vectors = encoder.encode([document_text(doc) for doc in documents])
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(config.dimension)
    index.add(vectors)
    dev_path = ROOT / "evals/retrieval_dev_cases.jsonl"
    neg_path = ROOT / "evals/rag_dev_negative_cases.jsonl"
    cases = load_cases(dev_path, list(iter_official_chunks()))
    negatives = [json.loads(line) for line in neg_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(negatives) < 8 or not all(row.get("expected_empty") is True for row in negatives):
        raise ValueError("At least 8 development negative queries required")
    scores, positions = index.search(encoder.encode([case["query"] for case in cases], queries=True), 3)
    rows = []
    for case, values, ids in zip(cases, scores, positions):
        hits = []
        for score, i in zip(values, ids):
            doc = documents[int(i)]
            source = source_matches(case, {"id": doc.id, "source_title": doc.source_title})
            hits.append({"id": doc.id, "score": float(score), "source_hit": source,
                         "section_hit": source and any(word in doc.section for word in case["expected_section_keywords"])})
        rows.append({"id": case["id"], "query": case["query"], "hits": hits})
    negative_scores, _ = index.search(encoder.encode([row["query"] for row in negatives], queries=True), 1)
    calibration = calibrate(rows, [float(row[0]) for row in negative_scores])
    calibration["sha256"] = {str(path.relative_to(ROOT)): sha256(path) for path in (dev_path, neg_path)}
    calibration["negative_queries"] = negatives
    config.index_path.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(config.index_path / "documents.faiss"))
    manifest = {"schema_version": 1, "model_config": config.identity(), "dimension": config.dimension,
                **corpus_identity(documents), "built_at": datetime.now(timezone.utc).isoformat(),
                "threshold": calibration["selected"]["threshold"], "calibration": calibration,
                "index_sha256": sha256(config.index_path / "documents.faiss"),
                "build_seconds": perf_counter() - start,
                "model_snapshot": str(encoder.snapshot),
                "model_weights_sha256": sha256(encoder.snapshot / "model.safetensors")}
    (config.index_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"index_path": str(config.index_path), "documents": len(documents),
                      "dimension": config.dimension, "threshold": manifest["threshold"],
                      "build_seconds": manifest["build_seconds"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
