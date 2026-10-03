"""Frozen external-case evaluation; no changes to retrievers or historic reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.store import Store
from evals.run_blind_retrieval_eval import load_blind_cases, load_negatives as load_negative_format
from evals.run_retrieval_eval import calculate_metrics, evaluate_case

CASES = ROOT / "evals/retrieval_external_cases.jsonl"
NEGATIVES = ROOT / "evals/retrieval_external_negative_cases.jsonl"
FREEZE = ROOT / "evals/retrieval_external_freeze.json"
OUTPUT = ROOT / "evals/results/retrieval_external_comparison.json"
FREEZE_SHA256 = "6484cbb25009e88c9260f4e1a36dad6963c9cfac35a40e46b33ed2ee2c0cc7af"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_freeze(root: Path = ROOT, manifest: Path = FREEZE) -> dict:
    if digest(manifest) != FREEZE_SHA256:
        raise ValueError("Frozen manifest changed")
    freeze = json.loads(manifest.read_text(encoding="utf-8"))
    for relative, expected in freeze["sha256"].items():
        if digest(root / relative) != expected:
            raise ValueError(f"Frozen input changed: {relative}")
    return freeze


def load_cases(path: Path = CASES) -> list[dict]:
    rows = load_blind_cases(path)
    counts = Counter(row["expected_source_id_prefix"] for row in rows)
    if len(set(counts.values())) != 1:
        raise ValueError("External sources must be balanced")
    return rows


def load_negatives(path: Path = NEGATIVES) -> list[dict]:
    rows = load_negative_format(path)
    if len(rows) < 12:
        raise ValueError("At least twelve negative cases required")
    return rows


def run(output: Path = OUTPUT) -> dict:
    freeze = verify_freeze()
    # Existing JSON files other than our result may be old reports or inputs.
    if output.suffix != ".json" or (output.exists() and output.resolve() != OUTPUT.resolve()):
        raise ValueError("Use a new JSON output path or the external comparison path")
    cases, negatives = load_cases(), load_negatives()
    report = {"schema_version": 1, "frozen_at": freeze["frozen_at"], "protocol": freeze["protocol"],
              "independence_limitation": freeze["independence_limitation"],
              "sha256": dict(freeze["sha256"]), "source_counts": dict(sorted(Counter(
                  row["expected_source_id_prefix"] for row in cases).items())),
              "positive_count": len(cases), "negative_count": len(negatives),
              "metric_definition": "Stage 4 source hit rates; all positive queries as denominator; negatives separate",
              "retrievers": {}}
    for path in (FREEZE, Path(__file__), ROOT / "evals/run_retrieval_eval.py", ROOT / "evals/run_blind_retrieval_eval.py"):
        report["sha256"][path.relative_to(ROOT).as_posix()] = digest(path)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "external.db")
        try:
            for name, search in (("baseline", store.search_knowledge_baseline),
                                 ("candidate", store.search_knowledge_candidate)):
                rows = [evaluate_case(store, case, name) for case in cases]
                negative_rows = []
                for case in negatives:
                    ids = [doc.id for doc in search(case["query"], limit=3)]
                    negative_rows.append({**case, "incorrect_document_ids": ids, "rejected_correctly": not ids})
                empty = sum(row["rejected_correctly"] for row in negative_rows)
                report["retrievers"][name] = {"metrics": calculate_metrics(rows), "results": rows,
                    "negatives": {"total": len(negative_rows), "empty_count": empty,
                                  "rejection_accuracy": empty / len(negative_rows), "results": negative_rows}}
        finally:
            store.close()
    verify_freeze()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = run(args.output)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(f"External evaluation error: {error}", file=sys.stderr)
        return 1
    for name, result in report["retrievers"].items():
        m, n = result["metrics"], result["negatives"]
        print(f"{name}: R@1={m['recall_at_1']:.2%} R@3={m['recall_at_3']:.2%} "
              f"section@3={m['section_recall_at_3']:.2%} wrong={m['wrong_citation_rate']:.2%} "
              f"no_evidence={m['no_evidence_rate']:.2%} rejection={n['empty_count']}/{n['total']} ({n['rejection_accuracy']:.2%})")
        for title, group in m["by_source"].items():
            print(f"  {title}: {group['hits_at_3']}/{group['total']}")
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
