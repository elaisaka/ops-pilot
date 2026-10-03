"""Read-only three-way retrieval experiment. Calibration lives exclusively in the builder."""
from __future__ import annotations

import json
import math
import platform
import sys
import tempfile
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag_retrieval import RagConfig, VectorRetriever, object_hash, sha256
from app.retrieval import FIELD_WEIGHTS, SYNONYMS, normalize, terms
from app.store import Store
from evals import run_blind_retrieval_eval as blind
from evals import run_external_retrieval_eval as external
from evals.run_retrieval_eval import calculate_metrics, source_matches

# Stage 5.0 is a historical artifact; replay timing/index hashes must not overwrite it.
OUTPUT = ROOT / "evals/results/rag_comparison_replay.json"


def verify_inputs():
    blind.verify_freeze()
    external.verify_freeze()


def positive_result(case, hits):
    top3 = []
    for doc, score in hits:
        item = doc.model_dump(mode="json")  # Full provenance, not reconstructed citations.
        item.update(score=float(score), source_hit=source_matches(case, item))
        item["section_hit"] = item["source_hit"] and any(k in doc.section for k in case["expected_section_keywords"])
        top3.append(item)
    hit3 = any(item["source_hit"] for item in top3)
    return {**case, "top3": top3, "hit_at_1": bool(top3 and top3[0]["source_hit"]),
            "hit_at_3": hit3, "section_hit_at_3": any(item["section_hit"] for item in top3),
            "error_reason": "no_results" if not top3 else "unexpected_source" if not hit3 else None}


class KeywordScores:
    """Audit-only score reconstruction; Store still selects and orders every result."""
    def __init__(self, documents):
        self.fields = {doc.id: {field: terms(" ".join(doc.tags) if field == "tags" else getattr(doc, field))
                                for field in FIELD_WEIGHTS} for doc in documents}
        self.df = Counter(term for fields in self.fields.values() for term in set().union(*fields.values()))
        self.count = len(documents)

    def score(self, name, query, doc):
        if name == "baseline":
            query = query.lower()
            return (sum(6 for tag in doc.tags if tag.lower() in query)
                    + sum(1 for c in query if c.strip() and c in doc.title) + (2 if doc.source_url else 0))
        query = normalize(query)
        tokens = terms(query + " " + " ".join(v for k, v in SYNONYMS.items() if k in query))
        return sum(weight * sum(math.log(1 + self.count / self.df[t]) for t in tokens & self.fields[doc.id][field])
                   for field, weight in FIELD_WEIGHTS.items())


def latency_summary(values):
    ordered = sorted(values)
    return {"samples": len(values), "mean_ms": sum(values) / len(values),
            "p50_ms": ordered[math.ceil(.50 * len(values)) - 1],
            "p95_ms": ordered[math.ceil(.95 * len(values)) - 1]}


def adoption_gate(splits):
    # Predeclared minimum section benefit: at least one extra correct query in EACH split.
    return all(s["vector_rag"]["metrics"]["recall_at_3"] >= s["candidate"]["metrics"]["recall_at_3"]
               and s["vector_rag"]["metrics"]["section_recall_at_3"] > s["candidate"]["metrics"]["section_recall_at_3"]
               and s["vector_rag"]["negatives"]["rejection_accuracy"] >= s["candidate"]["negatives"]["rejection_accuracy"]
               for s in splits.values())


def run(output=OUTPUT):
    if output.resolve() != OUTPUT.resolve():
        raise ValueError("Only the dedicated rag_comparison_replay.json output is permitted")
    verify_inputs()
    config = RagConfig.from_env()
    tracked = [blind.FREEZE, external.FREEZE, blind.CASES, blind.NEGATIVES, external.CASES, external.NEGATIVES,
               ROOT / "knowledge/official_sources.jsonl", ROOT / "knowledge/source_registry.json",
               ROOT / "app/retrieval.py", ROOT / "app/store.py", ROOT / "app/rag_retrieval.py",
               ROOT / "scripts/build_vector_index.py", Path(__file__), ROOT / "evals/run_retrieval_eval.py",
               config.index_path / "manifest.json", config.index_path / "documents.faiss",
               ROOT / "evals/retrieval_dev_cases.jsonl", ROOT / "evals/rag_dev_negative_cases.jsonl"]
    before = {str(path): sha256(path) for path in tracked}
    import psutil
    process = psutil.Process()
    report = {"schema_version": 1, "model_config": config.identity(), "model_config_sha256": object_hash(config.identity()),
              "sha256": before, "metric_definition": "Stage 4 source/section hits; positive denominators only; negatives separate",
              "timing_protocol": "3 warm sequential searches per query, all positive+negative samples; nearest-rank percentiles; score audit excluded; CPU threads=2",
              "environment": {"python": sys.version, "platform": platform.platform(), "processor": platform.processor(),
                              "packages": {name: version(name) for name in ("torch", "transformers", "faiss-cpu", "numpy", "huggingface-hub")}},
              "splits": {}}
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "rag_eval.db")
        try:
            docs = store.list_knowledge()
            start = perf_counter()
            vector = VectorRetriever(docs, config)
            report["cold_load_seconds"] = perf_counter() - start
            report["threshold"] = vector.manifest["threshold"]
            report["calibration"] = vector.manifest["calibration"]
            for path, expected in vector.manifest["calibration"]["sha256"].items():
                if sha256(ROOT / path) != expected:
                    raise ValueError("Calibration development input changed; rebuild explicitly")
            scorer = KeywordScores(docs)
            searches = {"baseline": store.search_knowledge_baseline, "candidate": store.search_knowledge_candidate,
                        "vector_rag": vector.search}
            warmup = vector.manifest["calibration"]["positive_scores"][0]["query"]
            for search in searches.values():
                search(warmup, limit=3)
            for split, cases, negatives in (("blind", blind.load_blind_cases(), blind.load_negatives()),
                                             ("external", external.load_cases(), external.load_negatives())):
                report["splits"][split] = {}
                for name, search in searches.items():
                    positive_rows, negative_rows, timings = [], [], []
                    for is_positive, rows in ((True, cases), (False, negatives)):
                        for case in rows:
                            samples = []
                            for _ in range(3):
                                start = perf_counter()
                                found = search(case["query"], limit=3)
                                samples.append((perf_counter() - start) * 1000)
                            timings.extend(samples)
                            hits = found if name == "vector_rag" else [(doc, scorer.score(name, case["query"], doc)) for doc in found]
                            if is_positive:
                                positive_rows.append({**positive_result(case, hits), "latency_ms": samples})
                            else:
                                negative_rows.append({**case, "rejected_correctly": not hits,
                                    "incorrect_document_ids": [doc.id for doc, _ in hits],
                                    "top3": [{**doc.model_dump(mode="json"), "score": float(score)} for doc, score in hits],
                                    "latency_ms": samples})
                    empty = sum(row["rejected_correctly"] for row in negative_rows)
                    report["splits"][split][name] = {"metrics": calculate_metrics(positive_rows), "results": positive_rows,
                        "latency": latency_summary(timings), "negatives": {"total": len(negative_rows), "empty_count": empty,
                        "rejection_accuracy": empty / len(negative_rows), "results": negative_rows}}
        finally:
            store.close()
    report["resources"] = {"rss_bytes": process.memory_info().rss,
                           "peak_working_set_bytes": getattr(process.memory_info(), "peak_wset", None),
                           "index_bytes": (config.index_path / "documents.faiss").stat().st_size,
                           "model_cache_bytes": sum(p.stat().st_size for p in config.cache.rglob("*") if p.is_file())}
    report["recommend_agent_integration"] = adoption_gate(report["splits"])
    verify_inputs()
    if before != {str(path): sha256(path) for path in tracked}:
        raise ValueError("Frozen experiment inputs changed during evaluation; report not written")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = run()
    for split, methods in result["splits"].items():
        for name, row in methods.items():
            print(split, name, json.dumps({"metrics": row["metrics"], "latency": row["latency"],
                  "rejection_accuracy": row["negatives"]["rejection_accuracy"]}, ensure_ascii=False))
    print("Recommend Agent integration:", result["recommend_agent_integration"])
