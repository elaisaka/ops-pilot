"""Run deterministic, reproducible behavior checks against the actual service path."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.service import OpsPilotService
from app.store import Store


def evaluate(case: dict) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        service = OpsPilotService(Store(Path(directory) / "eval.db"))
        conversation_id = service.store.create_conversation()
        response = service.handle_message(conversation_id, case["message"])
        service.store.close()
    expected = case["expected"]
    checks = []
    if "blocked" in expected:
        checks.append(any(event.outcome == "blocked" for event in response.tool_events) == expected["blocked"])
    if "approval" in expected:
        actual = response.approvals[0].status if response.approvals else None
        checks.append(actual == expected["approval"])
    if "citation" in expected:
        checks.append(expected["citation"] in [citation.document_id for citation in response.citations])
    if "citation_prefix" in expected:
        checks.append(any(citation.document_id.startswith(expected["citation_prefix"]) for citation in response.citations))
    if "ticket_category" in expected:
        actual = response.tickets[0].category if response.tickets else None
        checks.append(actual == expected["ticket_category"])
    return {"name": case["name"], "passed": all(checks), "checks": checks}


def main() -> int:
    cases_path = ROOT / "evals" / "cases.jsonl"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line]
    results = [evaluate(case) for case in cases]
    report = {"total": len(results), "passed": sum(result["passed"] for result in results), "results": results}
    output_dir = Path(os.getenv("OPS_EVAL_OUTPUT_DIR", str(ROOT / "evals" / "results")))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["total"] == report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
