"""Offline tests for real-run evidence collection; these never call a model API."""
from __future__ import annotations

import asyncio
from contextvars import copy_context
from dataclasses import asdict
import json
import os
import sys

import httpx
import pytest

from app.fake_deepseek import FakeDeepSeekModel
from app.live_agent import observe_live_http
from app.live_config import DeepSeekConfig
from app.service import OpsPilotService
from app.store import Store
from evals.live_acceptance import (
    AcceptanceStop,
    HttpTrace,
    MODEL,
    ORDER_FIELDS,
    TOOLS,
    check_scenario,
    cost_bounds,
    observed_http_client,
    usage_summary,
)
from evals import run_live_agent_real_smoke


def protocol_request(*, messages=None, url="https://api.deepseek.com/chat/completions", method="POST"):
    return httpx.Request(method, url, headers={"X-Test-Observation": "HEADER_MUST_NOT_PERSIST"}, json={
        "model": MODEL,
        "stream": False,
        "thinking": {"type": "disabled"},
        "tools": [{"type": "function", "function": {"name": name}} for name in sorted(TOOLS)],
        "messages": messages or [{"role": "user", "content": "PROMPT_MUST_NOT_PERSIST"}],
    })


def protocol_response(*, calls=None, usage=None):
    return httpx.Response(200, json={
        "model": MODEL,
        "usage": usage,
        "choices": [{"finish_reason": "tool_calls" if calls else "stop", "message": {
            "role": "assistant", "content": "MODEL_PROSE_MUST_NOT_PERSIST",
            "reasoning_content": "REASONING_MUST_NOT_PERSIST", "tool_calls": calls or [],
        }}],
    })


def tool_call(name, arguments=None):
    return {"id": f"call_{name}", "type": "function", "function": {
        "name": name, "arguments": json.dumps(arguments or {}, ensure_ascii=False),
    }}


def tool_result(name, content):
    return {"role": "tool", "tool_call_id": f"call_{name}", "content": json.dumps(content, ensure_ascii=False)}


def test_http_200_and_local_evidence_do_not_prove_actual_model_tool_call(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "live")
    monkeypatch.setenv("KNOWLEDGE_RETRIEVER", "candidate")
    store = Store(tmp_path / "acceptance.db")
    try:
        service = OpsPilotService(store, live_model_factory=lambda: FakeDeepSeekModel("knowledge"))
        conversation_id = store.create_conversation()
        response = asyncio.run(service.handle_live_message(conversation_id, "平台交易信息应保存多久"))
        audit = store.list_audit(conversation_id)
        assert response.citations
        assert any(row["event_type"] == "retrieval_completed" for row in audit)
        trace = HttpTrace(allowed_tools={"search_knowledge"})
        trace.finish(trace.start(protocol_request()), protocol_response())
        issues = check_scenario({"id": "knowledge", "expected_tools": ["search_knowledge"]},
                                response, audit, store, trace)
        assert "expected_model_tool_missing" in issues
        assert "expected_tool_result_missing" in issues
        assert "model_response_failed" not in issues
        assert "tool_node_missing" not in issues
    finally:
        store.close()


@pytest.mark.parametrize("status,expected", [
    (401, "authentication_failed"), (402, "insufficient_balance"), (400, "protocol_rejected"),
])
def test_remote_failure_records_fixed_classification_without_response_text(status, expected):
    trace = HttpTrace(allowed_tools=TOOLS)
    row = trace.start(protocol_request())
    response = httpx.Response(status, json={"error": {
        "message": "PRIVATE_REMOTE_ERROR E:\\private\\model Traceback", "detail": "RAW_BODY_MUST_NOT_PERSIST",
    }})
    with pytest.raises(AcceptanceStop, match=f"^{expected}$"):
        trace.finish(row, response)
    assert trace.error == row["failure"] == expected
    assert row["http_status"] == status
    serialized = json.dumps(asdict(trace), default=list)
    assert "PRIVATE_REMOTE_ERROR" not in serialized
    assert "RAW_BODY_MUST_NOT_PERSIST" not in serialized
    assert "Traceback" not in serialized and "private" not in serialized
    with pytest.raises(AcceptanceStop, match=f"^{expected}$"):
        trace.start(protocol_request())
    assert len(trace.requests) == 1


@pytest.mark.parametrize("url,method", [
    ("https://example.invalid/chat/completions", "POST"),
    ("http://api.deepseek.com/chat/completions", "POST"),
    ("https://api.deepseek.com/models", "POST"),
    ("https://api.deepseek.com/chat/completions", "GET"),
])
def test_endpoint_boundary_rejects_before_counting_a_request(url, method):
    trace = HttpTrace(allowed_tools=TOOLS)
    with pytest.raises(AcceptanceStop, match="^unexpected_endpoint$"):
        trace.start(protocol_request(url=url, method=method))
    assert trace.requests == []


def test_six_model_request_budget_rejects_seventh_before_dispatch():
    trace = HttpTrace(allowed_tools=TOOLS)
    for _ in range(6):
        trace.finish(trace.start(protocol_request()), protocol_response())
    with pytest.raises(AcceptanceStop, match="^request_budget_exhausted$"):
        trace.start(protocol_request())
    assert len(trace.requests) == 6


def test_observed_client_blocks_unapproved_endpoint_without_network():
    trace = HttpTrace(allowed_tools=TOOLS)
    async def run():
        async with observed_http_client(trace) as client:
            with pytest.raises(AcceptanceStop, match="^unexpected_endpoint$"):
                await client.post("https://example.invalid/chat/completions", json={})
    asyncio.run(run())
    assert trace.requests == [] and trace.error == "unexpected_endpoint"


def test_usage_is_nullable_and_cost_uses_explicit_cache_price_bounds():
    assert usage_summary({}) is None
    assert usage_summary({"usage": {"prompt_tokens": True, "completion_tokens": -1}}) is None
    assert cost_bounds(None) is None
    assert cost_bounds({"prompt_tokens": 100, "completion_tokens": None}) is None
    usage = usage_summary({"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                                    "total_tokens": 120, "prompt_cache_hit_tokens": 40,
                                    "prompt_cache_miss_tokens": 60, "raw_field": "NOT_PERSISTED"}})
    assert "raw_field" not in usage
    assert cost_bounds(usage) == {"min_cny": .000546, "max_cny": .001092, "cache_split_known": True}
    assert cost_bounds({"prompt_tokens": 100, "completion_tokens": 20}) == {
        "min_cny": .000285, "max_cny": .00144, "cache_split_known": False,
    }
    trace = HttpTrace(allowed_tools=TOOLS)
    row = trace.start(protocol_request())
    trace.finish(row, protocol_response())
    assert row["usage"] is None and row["cost_bounds_cny"] is None


def test_trace_records_actual_tool_calls_and_only_allowlisted_result_summaries():
    trace = HttpTrace(allowed_tools=TOOLS)
    calls = [tool_call("search_knowledge", {"query": "RAW_QUERY_MUST_NOT_PERSIST"}),
             tool_call("get_order", {"order_id": "ORD-10001"}),
             tool_call("create_ticket", {"category": "invoice", "order_id": "ORD-10001", "summary": "RAW_SUMMARY_MUST_NOT_PERSIST"}),
             tool_call("request_refund_approval", {"order_id": "ORD-10001", "reason": "RAW_REASON_MUST_NOT_PERSIST"})]
    first = trace.start(protocol_request())
    trace.finish(first, protocol_response(calls=calls))
    order = {name: "SIMULATED_VALUE_MUST_NOT_PERSIST" for name in ORDER_FIELDS}
    order["id"] = "ORD-10001"
    document = {"id": "OFFICIAL-ECOMMERCE-001", "section": "第三条", "source_url": "https://example.invalid/source",
                "source_published_at": "2024-01-01", "source_fetched_at": "2026-01-01", "content": "RAW_DOCUMENT_MUST_NOT_PERSIST"}
    messages = [tool_result("get_order", order), tool_result("search_knowledge", [document]),
                tool_result("create_ticket", {"category": "invoice", "status": "open", "summary": "RAW_TICKET_MUST_NOT_PERSIST"}),
                tool_result("request_refund_approval", {"status": "pending", "action_type": "refund", "reason": "RAW_APPROVAL_MUST_NOT_PERSIST"})]
    second = trace.start(protocol_request(messages=messages))
    trace.finish(second, protocol_response())
    assert {call["name"] for call in first["tool_calls"]} == TOOLS
    assert trace.tool_results == [
        {"name": "get_order", "masked_fields_only": True, "order_id": "ORD-10001"},
        {"name": "search_knowledge", "document_ids": [document["id"]], "metadata_complete": True},
        {"name": "create_ticket", "category": "invoice"},
        {"name": "request_refund_approval", "pending_only": True},
    ]
    serialized = json.dumps(asdict(trace), default=list)
    assert "MUST_NOT_PERSIST" not in serialized
    assert "headers" not in serialized and "reasoning_content" not in serialized


def test_unmasked_order_tool_result_stops_before_next_model_request():
    trace = HttpTrace(allowed_tools={"get_order"})
    trace.finish(trace.start(protocol_request()), protocol_response(calls=[tool_call("get_order", {"order_id": "ORD-10001"})]))
    order = {name: None for name in ORDER_FIELDS}
    order["customer_name"] = "UNMASKED_FIELD_MUST_NOT_PERSIST"
    with pytest.raises(AcceptanceStop, match="^order_privacy_failed$"):
        trace.start(protocol_request(messages=[tool_result("get_order", order)]))
    assert len(trace.requests) == 1 and trace.tool_results == []
    assert "UNMASKED_FIELD" not in json.dumps(asdict(trace), default=list)


def test_nonthinking_configuration_needs_no_credential_or_client():
    config = DeepSeekConfig(api_key="", model=MODEL)
    parameters = config.client_parameters()
    assert parameters["extra_body"] == {"thinking": {"type": "disabled"}}
    assert parameters["temperature"] == 0 and parameters["max_retries"] == 0
    assert "api_key" not in repr(config)


def test_observation_context_restores_nested_and_exception_state_without_a_client():
    before = dict(copy_context().items())
    outer, inner = object(), object()
    def observed_value():
        return next((value for variable, value in copy_context().items()
                     if variable.name == "live_http_observation_client"), None)
    with observe_live_http(outer):
        assert observed_value() is outer
        with pytest.raises(RuntimeError, match="local context exit"):
            with observe_live_http(inner):
                assert observed_value() is inner
                raise RuntimeError("local context exit")
        assert observed_value() is outer
    assert dict(copy_context().items()) == before


@pytest.mark.parametrize("failed_index", [0, 1, 2])
def test_real_smoke_stops_after_first_failure_and_never_attempts_later_cases(tmp_path, failed_index):
    attempted = []
    async def execute(case, directory):
        assert directory == tmp_path
        attempted.append(case["id"])
        return {"case": case["id"], "attempted": True,
                "passed": len(attempted) - 1 != failed_index,
                "expected_tools": case["expected_tools"], "actual_model_tools": [],
                "issues": ["offline_simulated_failure"] if len(attempted) - 1 == failed_index else [],
                "http_requests": []}
    rows = asyncio.run(run_live_agent_real_smoke.run_cases(tmp_path, execute=execute))
    expected_ids = [case["id"] for case in run_live_agent_real_smoke.CASES]
    assert expected_ids == ["knowledge", "order", "delivery", "invoice", "refund"]
    assert attempted == expected_ids[:failed_index + 1]
    assert len(rows) == 5 and sum(row["attempted"] for row in rows) == failed_index + 1
    assert rows[failed_index]["passed"] is False
    for row in rows[failed_index + 1:]:
        assert row["attempted"] is False and row["passed"] is False
        assert row["issues"] == ["not_run_after_failure"]
        assert row["actual_model_tools"] == []


def test_real_smoke_success_attempts_exactly_five_distinct_cases_once(tmp_path):
    attempted = []
    async def execute(case, directory):
        attempted.append(case["id"])
        return {"case": case["id"], "attempted": True, "passed": True,
                "expected_tools": case["expected_tools"], "actual_model_tools": [],
                "issues": [], "http_requests": []}
    rows = asyncio.run(run_live_agent_real_smoke.run_cases(tmp_path, execute=execute))
    assert attempted == ["knowledge", "order", "delivery", "invoice", "refund"]
    assert len(attempted) == len(set(attempted)) == len(rows) == 5
    assert all(row["attempted"] and row["passed"] for row in rows)


def test_real_smoke_requires_explicit_enable_without_reading_credentials(monkeypatch, capsys):
    original_getenv = os.getenv
    def guarded_getenv(name, *args):
        assert name != "DEEPSEEK_API_KEY", "disabled command accessed credentials"
        return original_getenv(name, *args)
    def forbidden(*args, **kwargs):
        pytest.fail("disabled command entered live preflight or read model configuration")
    monkeypatch.setattr(os, "getenv", guarded_getenv)
    monkeypatch.setattr(DeepSeekConfig, "from_env", forbidden)
    monkeypatch.setattr(run_live_agent_real_smoke, "run", forbidden)
    monkeypatch.setattr(sys, "argv", ["run_live_agent_real_smoke.py"])
    assert run_live_agent_real_smoke.main() == 2
    output = capsys.readouterr()
    assert "--allow-real-api is required" in output.out
    assert "no API requests made" in output.out
    assert output.err == ""
