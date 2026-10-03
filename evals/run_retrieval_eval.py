"""Offline source-level retrieval baseline against the real Store, with no model calls."""

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

from app.knowledge_sources import iter_official_chunks, knowledge_path, source_registry_path
from app.store import Store

DEFAULT_CASES = ROOT / "evals/retrieval_cases.jsonl"
DEFAULT_OUTPUT = ROOT / "evals/results/retrieval_baseline_replay.json"


def source_matches(case: dict, document: dict) -> bool:
    return (
        ("expected_source_title" not in case or document["source_title"] == case["expected_source_title"])
        and ("expected_source_id_prefix" not in case or document["id"].startswith(case["expected_source_id_prefix"]))
    )


def load_cases(path: Path, documents: list[dict]) -> list[dict]:
    cases = []
    ids = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case, dict):
            raise ValueError(f"line {line_number}: expected an object")
        for field in ("id", "query", "reason"):
            if not isinstance(case.get(field), str) or not case[field].strip():
                raise ValueError(f"line {line_number}: nonempty {field} required")
        labels = [key for key in ("expected_source_title", "expected_source_id_prefix") if key in case]
        if not labels or any(not isinstance(case[key], str) or not case[key].strip() for key in labels):
            raise ValueError(f"line {line_number}: expected source required")
        keywords = case.get("expected_section_keywords", [])
        if not isinstance(keywords, list) or any(not isinstance(word, str) or not word.strip() for word in keywords):
            raise ValueError(f"line {line_number}: invalid section keywords")
        if case["id"] in ids:
            raise ValueError(f"line {line_number}: duplicate id {case['id']}")
        matching = [doc for doc in documents if source_matches(case, doc)]
        if len({doc["source_title"] for doc in matching}) != 1:
            raise ValueError(f"line {line_number}: source must resolve to exactly one corpus source")
        if keywords and not any(any(word in doc["section"] for word in keywords) for doc in matching):
            raise ValueError(f"line {line_number}: section does not exist in expected source")
        ids.add(case["id"])
        cases.append({**case, "expected_source_title": matching[0]["source_title"]})
    if not cases:
        raise ValueError("empty evaluation set")
    return cases


def evaluate_case(store: Store, case: dict, retriever: str = "baseline") -> dict:
    search = {"baseline": store.search_knowledge_baseline, "candidate": store.search_knowledge_candidate}[retriever]
    documents = search(case["query"], limit=3)
    top3 = []
    for document in documents:
        item = {"id": document.id, "title": document.title, "section": document.section,
                "source_title": document.source_title, "source_url": document.source_url}
        item["source_hit"] = source_matches(case, item)
        keywords = case.get("expected_section_keywords", [])
        item["section_hit"] = item["source_hit"] and any(word in document.section for word in keywords)
        top3.append(item)
    hit1 = bool(top3 and top3[0]["source_hit"])
    hit3 = any(doc["source_hit"] for doc in top3)
    section_hit3 = any(doc["section_hit"] for doc in top3) if case.get("expected_section_keywords") else None
    error = "no_results" if not top3 else "unexpected_source" if not hit3 else None
    return {**case, "top3": top3, "hit_at_1": hit1, "hit_at_3": hit3,
            "section_hit_at_3": section_hit3, "error_reason": error,
            "section_error_reason": "expected_section_not_in_top3" if section_hit3 is False else None}


def calculate_metrics(results: list[dict]) -> dict:
    total = len(results)
    if not total:
        raise ValueError("cannot calculate metrics for empty results")
    wrong = sum(bool(row["top3"]) and not row["hit_at_3"] for row in results)
    no_evidence = sum(not row["hit_at_3"] for row in results)
    annotated = [row for row in results if row["section_hit_at_3"] is not None]
    by_source = {}
    for title in sorted({row["expected_source_title"] for row in results}):
        rows = [row for row in results if row["expected_source_title"] == title]
        by_source[title] = {"total": len(rows), "hits_at_3": sum(row["hit_at_3"] for row in rows),
                            "recall_at_3": sum(row["hit_at_3"] for row in rows) / len(rows)}
    return {"total": total, "recall_at_1": sum(row["hit_at_1"] for row in results) / total,
            "recall_at_3": sum(row["hit_at_3"] for row in results) / total,
            "wrong_citation_count": wrong, "wrong_citation_rate": wrong / total,
            "no_evidence_count": no_evidence, "no_evidence_rate": no_evidence / total,
            "no_results_count": sum(not row["top3"] for row in results),
            "section_annotated_count": len(annotated),
            "section_recall_at_3": sum(row["section_hit_at_3"] for row in annotated) / len(annotated) if annotated else None,
            "by_source": by_source}


def quality_summary(documents: list[dict], results: list[dict]) -> dict:
    def duplicates(field: str) -> list[list[str]]:
        groups: dict[str, list[str]] = {}
        for doc in documents:
            groups.setdefault("".join(doc[field].split()), []).append(doc["id"])
        return [ids for ids in groups.values() if len(ids) > 1]

    lengths = sorted(len(doc["content"]) for doc in documents)
    returned_ids = {doc["id"] for row in results for doc in row["top3"]}
    return {"documents": len(documents), "chunks_by_source": dict(Counter(doc["source_title"] for doc in documents)),
            "duplicate_content_groups": duplicates("content"), "duplicate_title_groups": duplicates("title"),
            "content_length_min": min(lengths), "content_length_max": max(lengths),
            "content_length_median": (lengths[(len(lengths)-1)//2] + lengths[len(lengths)//2]) / 2,
            "shorter_than_20_chars": [doc["id"] for doc in documents if len(doc["content"]) < 20],
            "longer_than_500_chars": [doc["id"] for doc in documents if len(doc["content"]) > 500],
            "top3_unique_chunk_count": len(returned_ids),
            "not_returned_chunk_ids": sorted(doc["id"] for doc in documents if doc["id"] not in returned_ids)}


def run(cases_path: Path = DEFAULT_CASES, output_path: Path = DEFAULT_OUTPUT) -> dict:
    documents = list(iter_official_chunks())
    if not documents or len({doc["id"] for doc in documents}) != len(documents):
        raise ValueError("empty corpus or duplicate document IDs")
    cases = load_cases(cases_path, documents)
    # A new database prevents pre-existing user data from influencing the baseline.
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "retrieval.db")
        try:
            results = [evaluate_case(store, case) for case in cases]
        finally:
            store.close()
    report = {"schema_version": 1, "retriever": "Store.search_knowledge_baseline", "top_k": 3,
              "metric_denominator": "all queries; Recall is expected-source hit rate, not chunk recall",
              "sha256": {"cases": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
                         "corpus": hashlib.sha256(knowledge_path().read_bytes()).hexdigest(),
                         "sources": hashlib.sha256(source_registry_path().read_bytes()).hexdigest(),
                         "retriever": hashlib.sha256(inspect.getsource(Store.search_knowledge_baseline).encode()).hexdigest()},
              "metrics": calculate_metrics(results), "quality": quality_summary(documents, results), "results": results}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        report = run(args.cases, args.output)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
        print(f"Retrieval evaluation input/data/output error: {error}", file=sys.stderr)
        return 1
    metrics = report["metrics"]
    print(f"Queries: {metrics['total']}; Recall@1: {metrics['recall_at_1']:.2%}; Recall@3: {metrics['recall_at_3']:.2%}")
    print(f"Wrong citation: {metrics['wrong_citation_rate']:.2%}; No evidence: {metrics['no_evidence_rate']:.2%}")
    for title, values in metrics["by_source"].items():
        print(f"  {title}: {values['hits_at_3']}/{values['total']} ({values['recall_at_3']:.2%})")
    print(f"Report: {args.output}")
    return 0  # Retrieval misses are measurements, not evaluation execution failures.


if __name__ == "__main__":
    raise SystemExit(main())
