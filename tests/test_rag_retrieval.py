"""Offline contract tests use explicit scores, never pretend to run an embedding model."""
import copy
import json
import os
from pathlib import Path

import pytest

from app.rag_retrieval import (RagConfig, VectorRetriever, corpus_identity, document_text,
                               select_hits, validate_manifest)
from app.store import Store
from evals.run_rag_comparison import (KeywordScores, adoption_gate, latency_summary,
                                     positive_result, verify_inputs)
from evals.run_retrieval_eval import calculate_metrics
from scripts.build_vector_index import calibrate


@pytest.fixture
def documents(tmp_path):
    store = Store(tmp_path / "test.db")
    try:
        yield store.list_knowledge()
    finally:
        store.close()


def manifest_for(documents):
    config = RagConfig()
    return {"model_config": config.identity(), **corpus_identity(documents), "dimension": 512, "threshold": .49}


@pytest.mark.parametrize("field", ["model_config", "corpus_sha256", "sources_sha256", "documents_sha256", "document_ids", "dimension"])
def test_manifest_rejects_stale_inputs(documents, field):
    manifest = manifest_for(documents)
    validate_manifest(manifest, RagConfig(), documents, 512)
    manifest[field] = "changed"
    with pytest.raises(ValueError, match="rebuild"):
        validate_manifest(manifest, RagConfig(), documents, 512)


def test_dimension_and_threshold_validation(documents):
    manifest = manifest_for(documents)
    with pytest.raises(ValueError, match="rebuild"):
        validate_manifest(manifest, RagConfig(), documents, 384)
    for threshold in (None, float("nan"), -2, 2):
        manifest["threshold"] = threshold
        with pytest.raises(ValueError, match="threshold"):
            validate_manifest(manifest, RagConfig(), documents, 512)


def test_threshold_stable_ties_and_original_metadata(documents):
    hits = select_hits(documents, [.8, .8, .2], [1, 0, 2], .49, 3)
    assert [doc.id for doc, _ in hits] == sorted([documents[0].id, documents[1].id])
    assert hits[0][0] is documents[0]
    assert hits[0][0].source_url and hits[0][0].source_published_at and hits[0][0].source_fetched_at
    assert select_hits(documents, [.1, float("nan")], [0, 1], .49, 3) == []
    assert select_hits(documents, [.8], [-1], .49, 3) == []
    text = document_text(documents[0])
    assert all(value in text for value in (documents[0].title, documents[0].section, documents[0].content, *documents[0].tags))


def test_metric_calculation_and_latency(documents):
    doc = documents[0]
    case = {"id": "unit", "query": "unit", "expected_source_title": doc.source_title,
            "expected_section_keywords": [doc.section]}
    yes, empty = positive_result(case, [(doc, .8)]), positive_result(case, [])
    wrong = positive_result({**case, "expected_source_title": "not this source"}, [(doc, .8)])
    m = calculate_metrics([yes, empty, wrong])
    assert m["recall_at_1"] == m["recall_at_3"] == m["section_recall_at_3"] == 1/3
    assert m["wrong_citation_rate"] == 1/3 and m["no_evidence_rate"] == 2/3
    assert yes["top3"][0]["source_url"] == doc.source_url and yes["top3"][0]["score"] == .8
    assert latency_summary([4, 1, 3, 2]) == {"samples": 4, "mean_ms": 2.5, "p50_ms": 2, "p95_ms": 4}


def test_dev_calibration_is_deterministic():
    rows = [{"hits": [{"score": .8, "source_hit": True, "section_hit": True}]}]
    first = calibrate(rows, [.2, .3])
    assert first == calibrate(rows, [.2, .3])
    assert first["selected"]["threshold"] == .8
    assert first["selected"]["objective"] == 1


def test_keyword_audit_scores_match_order(tmp_path):
    store = Store(tmp_path / "scores.db")
    try:
        scorer = KeywordScores(store.list_knowledge())
        for name in ("baseline", "candidate"):
            query = "包裹破损怎么索赔"
            docs = getattr(store, "search_knowledge_" + name)(query)
            scores = [scorer.score(name, query, doc) for doc in docs]
            assert scores == sorted(scores, reverse=True)
    finally:
        store.close()


def test_gate_requires_all_conditions_in_both_splits():
    row = {"metrics": {"recall_at_3": .8, "section_recall_at_3": .6}, "negatives": {"rejection_accuracy": .5}}
    split = {"candidate": copy.deepcopy(row), "vector_rag": copy.deepcopy(row)}
    splits = {"blind": copy.deepcopy(split), "external": copy.deepcopy(split)}
    assert not adoption_gate(splits)
    for s in splits.values():
        s["vector_rag"]["metrics"]["section_recall_at_3"] = .7
    assert adoption_gate(splits)
    splits["external"]["vector_rag"]["negatives"]["rejection_accuracy"] = .4
    assert not adoption_gate(splits)


def test_frozen_inputs_and_dev_negatives():
    verify_inputs()
    from app.rag_retrieval import ROOT
    rows = [json.loads(line) for line in (ROOT / "evals/rag_dev_negative_cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) >= 8 and len({r["id"] for r in rows}) == len(rows)
    assert all(r["expected_empty"] is True and r["query"] and r["reason"] for r in rows)


@pytest.mark.skipif(os.getenv("RUN_RAG_INTEGRATION") != "1", reason="explicit opt-in; cached real model and index required")
def test_real_cached_index_integration(documents):
    retriever = VectorRetriever(documents)
    first = retriever.search("退货的邮费由谁承担")
    second = retriever.search("退货的邮费由谁承担")
    assert [(d.id, s) for d, s in first] == [(d.id, s) for d, s in second]
    assert all(d.source_url and d.section for d, _ in first)


@pytest.mark.skipif(os.getenv("RUN_RAG_INTEGRATION") != "1", reason="explicit opt-in real benchmark")
def test_real_comparison_reproducible_and_inputs_unchanged(tmp_path, monkeypatch):
    from evals import run_rag_comparison as experiment
    from app.rag_retrieval import sha256
    monkeypatch.setattr(experiment, "OUTPUT", tmp_path / "comparison.json")
    first = experiment.run(experiment.OUTPUT)
    second = experiment.run(experiment.OUTPUT)
    assert first["sha256"] == second["sha256"]
    assert all(sha256(Path(path)) == value for path, value in first["sha256"].items())
    for split in first["splits"]:
        for method in first["splits"][split]:
            a, b = first["splits"][split][method], second["splits"][split][method]
            assert a["metrics"] == b["metrics"]
            assert a["negatives"]["rejection_accuracy"] == b["negatives"]["rejection_accuracy"]
            for left, right in ((a["results"], b["results"]), (a["negatives"]["results"], b["negatives"]["results"])):
                assert [[(d["id"], d["score"]) for d in r["top3"]] for r in left] == [
                    [(d["id"], d["score"]) for d in r["top3"]] for r in right]
