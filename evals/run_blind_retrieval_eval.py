"""Read-only retrieval experiment against frozen cases, corpus and retrievers."""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sqlite3
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.knowledge_sources import iter_official_chunks
from app.store import Store
from evals.run_retrieval_eval import calculate_metrics, evaluate_case, load_cases

CASES = ROOT / "evals/retrieval_blind_cases.jsonl"
NEGATIVES = ROOT / "evals/retrieval_blind_negative_cases.jsonl"
FREEZE = ROOT / "evals/retrieval_blind_freeze.json"
OUTPUT = ROOT / "evals/results/retrieval_blind_comparison.json"
PREFIXES = {"OFFICIAL-RETURN-", "OFFICIAL-ECOM-", "OFFICIAL-COURIER-", "OFFICIAL-INVOICE-"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_freeze(root: Path = ROOT, manifest: Path = FREEZE) -> dict:
    freeze = json.loads(manifest.read_text(encoding="utf-8"))
    for relative, expected in freeze["sha256"].items():
        if digest(root / relative) != expected:
            raise ValueError(f"Frozen file changed: {relative}")
    return freeze


def load_blind_cases(path: Path = CASES) -> list[dict]:
    cases = load_cases(path, list(iter_official_chunks()))
    counts = Counter(case.get("expected_source_id_prefix") for case in cases)
    if set(counts) != PREFIXES or any(count < 6 for count in counts.values()):
        raise ValueError("Blind cases require four sources with at least six cases each")
    for case in cases:
        if not isinstance(case.get("expression"), str) or not case["expression"].strip():
            raise ValueError("expression required")
        if not case.get("expected_section_keywords"):
            raise ValueError("nonempty section keywords required")
    if len({case["query"] for case in cases}) != len(cases):
        raise ValueError("duplicate blind query")
    return cases


def load_negatives(path: Path = NEGATIVES) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) < 8:
        raise ValueError("at least eight negative cases required")
    for row in rows:
        if not isinstance(row, dict) or row.get("expected_empty") is not True:
            raise ValueError("negative expected_empty must be true")
        for field in ("id", "query", "reason", "expression"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"negative {field} required")
    if any(len({row[field] for row in rows}) != len(rows) for field in ("id", "query")):
        raise ValueError("duplicate negative id/query")
    return rows


def run(output: Path = OUTPUT) -> dict:
    freeze = verify_freeze()
    # Disallow overwriting a frozen input or another source file via --output.
    if output.suffix != ".json" or output.resolve() == FREEZE.resolve() or output.resolve() in {
        (ROOT / relative).resolve() for relative in freeze["sha256"]
    }:
        raise ValueError("output must be a separate JSON report")
    cases, negatives = load_blind_cases(), load_negatives()
    report = {"schema_version": 1, "frozen_at": freeze["frozen_at"], "protocol": freeze["protocol"],
              "sha256": dict(freeze["sha256"]), "positive_count": len(cases), "negative_count": len(negatives),
              "source_counts": dict(sorted(Counter(case["expected_source_id_prefix"] for case in cases).items())),
              "metric_definition": "same source/section hit rates as stage 4; negatives excluded; rejection = empty results / negative count",
              "retrievers": {}}
    report["sha256"]["baseline_method"] = hashlib.sha256(inspect.getsource(Store.search_knowledge_baseline).encode()).hexdigest()
    report["sha256"]["candidate_method"] = hashlib.sha256(inspect.getsource(Store.search_knowledge_candidate).encode()).hexdigest()
    for path in (FREEZE, Path(__file__), ROOT / "evals/run_retrieval_eval.py"):
        report["sha256"][path.relative_to(ROOT).as_posix()] = digest(path)
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "blind.db")
        try:
            for name in ("baseline", "candidate"):
                rows = [evaluate_case(store, case, name) for case in cases]
                search = getattr(store, f"search_knowledge_{name}")
                negative_rows = []
                for case in negatives:
                    ids = [doc.id for doc in search(case["query"], limit=3)]
                    negative_rows.append({**case, "returned_document_ids": ids,
                                          "incorrect_document_ids": ids, "rejected_correctly": not ids})
                empty = sum(row["rejected_correctly"] for row in negative_rows)
                report["retrievers"][name] = {"metrics": calculate_metrics(rows), "results": rows,
                    "negatives": {"total": len(negatives), "empty_count": empty,
                                  "rejection_accuracy": empty / len(negatives), "results": negative_rows}}
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
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error) as error:
        print(f"Blind evaluation error: {error}", file=sys.stderr)
        return 1
    for name, result in report["retrievers"].items():
        m, n = result["metrics"], result["negatives"]
        print(f"{name}: R@1={m['recall_at_1']:.2%}; R@3={m['recall_at_3']:.2%}; section@3={m['section_recall_at_3']:.2%}; "
              f"wrong={m['wrong_citation_rate']:.2%}; no_evidence={m['no_evidence_rate']:.2%}; "
              f"negative rejection={n['empty_count']}/{n['total']} ({n['rejection_accuracy']:.2%})")
        for title, values in m["by_source"].items():
            print(f"  {title}: {values['hits_at_3']}/{values['total']}")
    print(f"Report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
