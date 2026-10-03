import json
from collections import Counter

from app.knowledge_sources import iter_official_chunks
from app.retrieval import rank_documents
from app.store import Store
from evals.run_keyword_comparison import NEGATIVES, ROOT, run
from evals.run_retrieval_eval import load_cases


def test_dev_set_is_separate_and_balanced():
    docs = list(iter_official_chunks())
    dev = load_cases(ROOT / "evals/retrieval_dev_cases.jsonl", docs)
    fixed = load_cases(ROOT / "evals/retrieval_cases.jsonl", docs)
    assert len(dev) >= 12
    counts = Counter(case["expected_source_title"] for case in dev)
    assert len(counts) == 4 and min(counts.values()) >= 3
    assert not {case["query"] for case in dev} & {case["query"] for case in fixed}


def test_candidate_synonyms_empty_results_order_and_metadata(tmp_path):
    store = Store(tmp_path / "test.db")
    try:
        query = "平台能随便抹掉差评吗？"
        baseline = store.search_knowledge_baseline(query)
        candidate = store.search_knowledge_candidate(query)
        assert "OFFICIAL-ECOM-027" not in [doc.id for doc in baseline]
        assert candidate[0].id == "OFFICIAL-ECOM-027"
        assert store.search_knowledge(query) == candidate
        for query in NEGATIVES + ["", "？！"]:
            assert not store.search_knowledge_candidate(query)
        docs = store.list_knowledge()
        original = {doc.id: doc for doc in docs}
        for query in ("退货", "电子票存档 ＯＦＤ", "平台能随便抹掉差评吗？"):
            result = rank_documents(query, docs)
            assert result == rank_documents(query, list(reversed(docs)))
            assert result == rank_documents(query, docs)
            for doc in result:
                assert doc == original[doc.id]
                assert all(getattr(doc, name) for name in ("source_url", "source_title", "source_published_at",
                                                          "source_fetched_at", "section", "scope"))
        assert not rank_documents("退货", docs, 0)
    finally:
        store.close()


def test_comparison_reproduces_baseline_and_preserves_frozen_artifact(tmp_path):
    frozen = ROOT / "evals/results/retrieval_baseline.json"
    before = frozen.read_bytes()
    output = tmp_path / "comparison.json"
    first = run(output)
    assert first == run(output)
    assert first == json.loads(output.read_text(encoding="utf-8"))
    assert before == frozen.read_bytes()
    old = json.loads(before)
    assert first["splits"]["fixed"]["baseline"]["metrics"] == old["metrics"]
    assert first["splits"]["fixed"]["baseline"]["results"] == old["results"]
    assert first["metadata_preserved"]
    baseline = first["splits"]["fixed"]["baseline"]["metrics"]
    candidate = first["splits"]["fixed"]["candidate"]["metrics"]
    assert candidate["recall_at_3"] > baseline["recall_at_3"]
    assert candidate["wrong_citation_rate"] <= baseline["wrong_citation_rate"]
