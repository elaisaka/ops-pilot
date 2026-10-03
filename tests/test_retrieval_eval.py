import json
from collections import Counter

import pytest

from app.knowledge_sources import iter_official_chunks
from evals.run_retrieval_eval import DEFAULT_CASES, calculate_metrics, load_cases, main, run


def test_committed_cases_have_balanced_sources_and_diverse_expressions():
    cases = load_cases(DEFAULT_CASES, list(iter_official_chunks()))
    assert 20 <= len(cases) <= 30
    assert sorted(Counter(case["expected_source_title"] for case in cases).values()) == [6, 6, 6, 6]
    assert {"同义", "口语", "缺少关键词", "边界"} <= {case["expression"] for case in cases}


@pytest.mark.parametrize("change", [
    {"query": " "}, {"reason": ""}, {"id": 1},
    {"expected_source_id_prefix": "UNKNOWN-"},
    {"expected_section_keywords": "第十条"},
    {"expected_section_keywords": ["不存在的章节"]},
])
def test_invalid_case_is_rejected(tmp_path, change):
    case = json.loads(DEFAULT_CASES.read_text(encoding="utf-8").splitlines()[0])
    path = tmp_path / "cases.jsonl"
    path.write_text(json.dumps({**case, **change}, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path, list(iter_official_chunks()))


@pytest.mark.parametrize("content", ["", "{", "[]", "{}"])
def test_malformed_input_returns_nonzero(tmp_path, content):
    path = tmp_path / "bad.jsonl"
    output = tmp_path / "report.json"
    path.write_text(content, encoding="utf-8")
    assert main(["--cases", str(path), "--output", str(output)]) == 1
    assert not output.exists()


def test_duplicate_case_ids_are_rejected(tmp_path):
    line = DEFAULT_CASES.read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "cases.jsonl"
    path.write_text(line + "\n" + line, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate id"):
        load_cases(path, list(iter_official_chunks()))


def test_metric_denominators_distinguish_wrong_source_and_empty_results():
    results = [
        {"expected_source_title": "A", "top3": [{}], "hit_at_1": True, "hit_at_3": True, "section_hit_at_3": True},
        {"expected_source_title": "A", "top3": [{}, {}], "hit_at_1": False, "hit_at_3": True, "section_hit_at_3": False},
        {"expected_source_title": "B", "top3": [{}], "hit_at_1": False, "hit_at_3": False, "section_hit_at_3": False},
        {"expected_source_title": "B", "top3": [], "hit_at_1": False, "hit_at_3": False, "section_hit_at_3": None},
    ]
    metrics = calculate_metrics(results)
    assert metrics["recall_at_1"] == .25
    assert metrics["recall_at_3"] == .5
    assert metrics["wrong_citation_rate"] == .25
    assert metrics["no_evidence_rate"] == .5
    assert metrics["no_results_count"] == 1
    assert metrics["section_recall_at_3"] == pytest.approx(1 / 3)
    assert metrics["by_source"]["A"]["recall_at_3"] == 1
    assert metrics["by_source"]["B"]["recall_at_3"] == 0


def test_real_store_generates_reproducible_report_and_misses_do_not_fail(tmp_path):
    output = tmp_path / "results/report.json"
    report = run(output_path=output)
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert report["quality"]["documents"] == 84
    assert report["metrics"]["total"] == 24
    assert all(len(row["top3"]) <= 3 for row in report["results"])
    assert all(doc["source_url"].startswith("https://") for row in report["results"] for doc in row["top3"])
    assert report == run(output_path=output)
    # Force a valid but irrelevant query through Store, not a mocked retriever.
    case = json.loads(DEFAULT_CASES.read_text(encoding="utf-8").splitlines()[0])
    case.update(query="zzzzzz", expected_source_id_prefix="OFFICIAL-INVOICE-", expected_section_keywords=[])
    cases_path = tmp_path / "miss.jsonl"
    cases_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    assert main(["--cases", str(cases_path), "--output", str(output)]) == 0
    miss = json.loads(output.read_text(encoding="utf-8"))
    assert miss["metrics"]["recall_at_3"] == 0
    assert miss["results"][0]["error_reason"] == "unexpected_source"
