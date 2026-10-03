"""Real local embedding -> Service -> LangGraph smoke, isolated from user data."""
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
from evals.run_rag_comparison import verify_inputs


def run():
    if os.getenv("KNOWLEDGE_RETRIEVER") != "vector_rag":
        raise ValueError("Explicit KNOWLEDGE_RETRIEVER=vector_rag is required")
    if os.getenv("AGENT_MODE", "langgraph") == "live":
        raise ValueError("This smoke only validates deterministic LangGraph; unset AGENT_MODE=live")
    verify_inputs()
    cases = [
        ("knowledge", "电子商务平台应当保存商品和交易信息至少多少年？"),
        ("delivery", "订单 ORD-10002 物流一直没更新，快递公司应如何处理延误？"),
        ("refund", "订单 ORD-10001 我想退款，商品质量有问题"),
        ("unrelated", "明天上海会下雨吗？"),
    ]
    rows = []
    with tempfile.TemporaryDirectory() as directory:
        store = Store(Path(directory) / "smoke.db")
        try:
            service = OpsPilotService(store)
            for name, query in cases:
                conversation_id = store.create_conversation()
                before = (len(store.list_tickets()), len(store.list_pending_approvals()))
                response = service.handle_message(conversation_id, query)
                trace = response.retrieval
                assert trace is not None and trace.retriever == "vector_rag"
                assert trace.model == "BAAI/bge-small-zh-v1.5" and trace.revision and trace.index_manifest_sha256
                assert trace.threshold is not None and len(trace.scores) == len(trace.document_ids)
                assert all(score >= trace.threshold for score in trace.scores)
                assert trace.document_ids == [c.document_id for c in response.citations]
                audit = store.list_audit(conversation_id)
                assert next(e["payload"] for e in audit if e["event_type"] == "retrieval_completed") == trace.model_dump(mode="json")
                nodes = next(e["payload"]["nodes"] for e in audit if e["event_type"] == "langgraph_completed")
                assert nodes[:2] == ["guardrail", "retrieve_evidence"]
                if name == "unrelated":
                    assert trace.empty and not response.citations and "补充信息或转人工" in response.message
                    assert nodes == ["guardrail", "retrieve_evidence", "finalize_audit"]
                else:
                    assert not trace.empty and response.citations
                    for citation in response.citations:
                        assert all(getattr(citation, key) for key in ("document_id", "section", "source_url", "source_published_at", "source_fetched_at"))
                    if name == "knowledge":
                        assert "OFFICIAL-ECOM-021" in trace.document_ids
                if name == "refund":
                    assert len(response.approvals) == 1 and response.approvals[0].status == "pending"
                    assert not response.tickets and "当前未执行退款" in response.message
                    assert len(store.list_tickets()) == before[0]
                    assert len(store.list_pending_approvals()) == before[1] + 1
                elif name == "delivery":
                    assert len(response.tickets) == 1 and response.tickets[0].category == "delivery"
                    assert not response.approvals and len(store.list_pending_approvals()) == before[1]
                else:
                    assert not response.approvals and not response.tickets
                    assert before == (len(store.list_tickets()), len(store.list_pending_approvals()))
                assert json.dumps(str(ROOT))[1:-1] not in response.model_dump_json() + json.dumps(audit, ensure_ascii=False)
                rows.append({"case": name, "passed": True, "retrieval": trace.model_dump(mode="json"),
                             "nodes": nodes, "citation_ids": trace.document_ids,
                             "tickets": len(response.tickets), "pending_approvals": len(response.approvals)})
        finally:
            store.close()
    verify_inputs()
    report = {"total": len(rows), "passed": len(rows), "path": "OpsPilotService.handle_message -> LangGraph", "results": rows}
    output = ROOT / "evals/results/agent_rag_smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
