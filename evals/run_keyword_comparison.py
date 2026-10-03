"""Compare frozen lexical retrievers without overwriting the stage-4 baseline."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.knowledge_sources import iter_official_chunks
from app.store import Store
from evals.run_retrieval_eval import calculate_metrics, evaluate_case, load_cases

OUTPUT = ROOT / "evals/results/keyword_comparison.json"
NEGATIVES = ["zzzzzz", "明天北京天气怎么样", "如何煮意大利面", "帮我写一首诗"]


def run(output: Path = OUTPUT, splits: tuple[str, ...] = ("dev", "fixed")) -> dict:
    paths = {"dev": ROOT / "evals/retrieval_dev_cases.jsonl", "fixed": ROOT / "evals/retrieval_cases.jsonl"}
    documents = list(iter_official_chunks())
    report = {"schema_version": 1, "splits": {}, "negative_probes": [], "sha256": {}}
    for path in [*paths.values(), ROOT / "knowledge/official_sources.jsonl", ROOT / "knowledge/source_registry.json",
                 ROOT / "app/retrieval.py", ROOT / "app/store.py", ROOT / "evals/results/retrieval_baseline.json"]:
        report["sha256"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "comparison.db")
        try:
            for split in splits:
                cases = load_cases(paths[split], documents)
                report["splits"][split] = {}
                for retriever in ("baseline", "candidate"):
                    rows = [evaluate_case(store, case, retriever) for case in cases]
                    report["splits"][split][retriever] = {"metrics": calculate_metrics(rows), "results": rows}
            for query in NEGATIVES:
                report["negative_probes"].append({"query": query,
                    "baseline": [doc.id for doc in store.search_knowledge_baseline(query)],
                    "candidate": [doc.id for doc in store.search_knowledge_candidate(query)]})
            # Candidate returns the original domain objects with all citation fields intact.
            original = {doc.id: doc for doc in store.list_knowledge()}
            report["metadata_preserved"] = all(
                doc == original[doc.id] and all(getattr(doc, field) for field in
                    ("source_url", "source_title", "source_published_at", "source_fetched_at", "section", "scope"))
                for split in splits for case in load_cases(paths[split], documents)
                for doc in store.search_knowledge_candidate(case["query"]))
        finally:
            store.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "all"), default="all")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    report = run(args.output, ("dev",) if args.split == "dev" else ("dev", "fixed"))
    for split, runs in report["splits"].items():
        for name, result in runs.items():
            m = result["metrics"]
            print(f"{split}/{name}: R@1={m['recall_at_1']:.2%} R@3={m['recall_at_3']:.2%} "
                  f"section@3={m['section_recall_at_3']:.2%} wrong={m['wrong_citation_rate']:.2%} no_evidence={m['no_evidence_rate']:.2%}")
    print(f"Metadata preserved: {report['metadata_preserved']}; report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
