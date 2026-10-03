import json
from collections import Counter

import pytest

from evals import run_external_retrieval_eval as external


def write_rows(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")


def test_formats_and_balance():
    positives, negatives = external.load_cases(), external.load_negatives()
    counts = Counter(row["expected_source_id_prefix"] for row in positives)
    assert len(positives) >= 24 and len(counts) == 4
    assert len(set(counts.values())) == 1 and min(counts.values()) >= 6
    assert all(row["expected_section_keywords"] and row["expression"] for row in positives)
    assert len(negatives) >= 12 and all(row["expected_empty"] is True for row in negatives)
    assert {"编程", "医疗", "学习", "系统设置", "金融建议", "创作", "天气", "泛化不明"} <= {
        row["expression"] for row in negatives}


@pytest.mark.parametrize("change", [{"expression": ""}, {"expected_section_keywords": []},
                                    {"expected_source_id_prefix": "unknown"}])
def test_invalid_positive_rejected(tmp_path, change):
    rows = external.load_cases()
    rows[0].update(change)
    path = tmp_path / "invalid.jsonl"
    write_rows(path, rows)
    with pytest.raises(ValueError):
        external.load_cases(path)


@pytest.mark.parametrize("change", [{"expected_empty": "true"}, {"reason": ""}, {"query": 1}])
def test_invalid_negative_rejected(tmp_path, change):
    rows = external.load_negatives()
    rows[0].update(change)
    path = tmp_path / "invalid.jsonl"
    write_rows(path, rows)
    with pytest.raises(ValueError):
        external.load_negatives(path)


def test_freeze_detects_input_and_manifest_changes(tmp_path):
    manifest = external.verify_freeze()
    for relative in manifest["sha256"]:
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((external.ROOT / relative).read_bytes())
    external.verify_freeze(root=tmp_path)
    target = tmp_path / "evals/retrieval_external_cases.jsonl"
    target.write_bytes(target.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Frozen input"):
        external.verify_freeze(root=tmp_path)
    changed_manifest = tmp_path / "manifest.json"
    changed_manifest.write_bytes(external.FREEZE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Frozen manifest"):
        external.verify_freeze(manifest=changed_manifest)


def test_reproducible_report_without_input_mutations(tmp_path):
    freeze = external.verify_freeze()
    protected = [external.FREEZE, *(external.ROOT / path for path in freeze["sha256"])]
    before = {path: path.read_bytes() for path in protected}
    first = external.run(tmp_path / "first.json")
    second = external.run(tmp_path / "second.json")
    assert first == second
    assert (tmp_path / "first.json").read_bytes() == (tmp_path / "second.json").read_bytes()
    assert all(path.read_bytes() == content for path, content in before.items())
    for result in first["retrievers"].values():
        assert result["metrics"]["total"] == first["positive_count"]
        n = result["negatives"]
        assert n["empty_count"] == sum(not row["incorrect_document_ids"] for row in n["results"])
        assert n["rejection_accuracy"] == n["empty_count"] / n["total"]
    # Scores and the winning retriever are intentionally not asserted.


def test_post_run_freeze_failure_prevents_report(tmp_path, monkeypatch):
    verify = external.verify_freeze
    calls = 0

    def detect_change():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("Frozen input changed during evaluation")
        return verify()

    monkeypatch.setattr(external, "verify_freeze", detect_change)
    output = tmp_path / "not_written.json"
    with pytest.raises(ValueError, match="during evaluation"):
        external.run(output)
    assert calls == 2 and not output.exists()


def test_output_cannot_overwrite_inputs_or_old_reports():
    for path in (external.CASES, external.NEGATIVES, external.FREEZE,
                 external.ROOT / "evals/results/retrieval_baseline.json"):
        with pytest.raises(ValueError, match="output path"):
            external.run(path)
