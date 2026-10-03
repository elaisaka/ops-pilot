import json
from collections import Counter

import pytest

from evals.run_blind_retrieval_eval import (
    CASES, FREEZE, NEGATIVES, PREFIXES, ROOT, load_blind_cases, load_negatives, run, verify_freeze,
)


def test_blind_formats_and_source_distribution():
    cases = load_blind_cases()
    counts = Counter(case["expected_source_id_prefix"] for case in cases)
    assert len(cases) >= 24 and set(counts) == PREFIXES
    assert min(counts.values()) >= 6
    assert all(case["expected_section_keywords"] and case["expression"] for case in cases)
    negatives = load_negatives()
    assert len(negatives) >= 8 and all(case["expected_empty"] is True for case in negatives)
    assert not {row["id"] for row in cases} & {row["id"] for row in negatives}


@pytest.mark.parametrize("change", [{"expected_empty": False}, {"query": ""}, {"expression": 2}])
def test_invalid_negative_rejected(tmp_path, change):
    rows = load_negatives()
    rows[0].update(change)
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_negatives(path)


def test_blind_missing_section_rejected(tmp_path):
    rows = [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines()]
    rows[0]["expected_section_keywords"] = []
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="section"):
        load_blind_cases(path)


def test_reproducible_report_preserves_frozen_files(tmp_path):
    freeze = verify_freeze()
    before = {path: (ROOT / path).read_bytes() for path in freeze["sha256"]}
    manifest_before = FREEZE.read_bytes()
    output = tmp_path / "report.json"
    first = run(output)
    assert first == run(output)
    assert first == json.loads(output.read_text(encoding="utf-8"))
    assert FREEZE.read_bytes() == manifest_before
    assert all((ROOT / path).read_bytes() == content for path, content in before.items())
    for result in first["retrievers"].values():
        assert result["metrics"]["total"] == len(load_blind_cases())
        negative = result["negatives"]
        assert negative["empty_count"] == sum(not row["returned_document_ids"] for row in negative["results"])
        assert negative["rejection_accuracy"] == negative["empty_count"] / negative["total"]
    # No assertions about which retriever should win or achieve a target score.


def test_modified_freeze_digest_is_detected(tmp_path):
    freeze = verify_freeze()
    freeze["sha256"]["evals/retrieval_blind_cases.jsonl"] = "0" * 64
    manifest = tmp_path / "freeze.json"
    manifest.write_text(json.dumps(freeze), encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen file changed"):
        verify_freeze(manifest=manifest)


def test_output_cannot_overwrite_frozen_input():
    for path in (CASES, NEGATIVES, FREEZE, ROOT / "knowledge/source_registry.json"):
        with pytest.raises(ValueError, match="separate JSON"):
            run(path)
