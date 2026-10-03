"""Allow-listed observations for controlled real DeepSeek acceptance.

Never persist headers, prompts, model prose, reasoning, raw arguments or errors.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter

TOOLS = {"search_knowledge", "get_order", "create_ticket", "request_refund_approval"}
ORDER_FIELDS = {"id", "status", "amount", "product_name", "tracking_number", "after_sale_status"}
MODEL = "deepseek-v4-pro"
PRICING = {
    "source": "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    "checked_on": "2026-10-02", "currency": "CNY", "unit": "per million tokens",
    "off_peak": {"cache_hit": .15, "cache_miss": 4.5, "output": 13.5},
    "peak": {"cache_hit": .30, "cache_miss": 9.0, "output": 27.0},
    "basis": "Published off-peak/peak bounds, not a balance inquiry or actual bill. Recheck prices before a later run.",
}


class AcceptanceStop(RuntimeError):
    """Fixed error code only; never wrap a remote exception's message."""


def integer(value):
    return value if type(value) is int and 0 <= value <= 10**9 else None


def usage_summary(body):
    usage = body.get("usage") or {}
    if not isinstance(usage, dict):
        return None
    result = {name: integer(usage.get(name)) for name in (
        "prompt_tokens", "completion_tokens", "total_tokens",
        "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")}
    return result if any(v is not None for v in result.values()) else None


def cost_bounds(usage):
    if not usage or usage.get("prompt_tokens") is None or usage.get("completion_tokens") is None:
        return None
    prompt, output = usage["prompt_tokens"], usage["completion_tokens"]
    hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
    split_known = hit is not None and miss is not None and hit + miss == prompt
    low_input = hit * .15 + miss * 4.5 if split_known else prompt * .15
    high_input = hit * .30 + miss * 9 if split_known else prompt * 9
    return {"min_cny": round((low_input + output * 13.5) / 1e6, 8),
            "max_cny": round((high_input + output * 27) / 1e6, 8),
            "cache_split_known": split_known}


def failure_code(status):
    return {400: "protocol_rejected", 401: "authentication_failed", 402: "insufficient_balance",
            403: "permission_denied", 404: "model_or_endpoint_unavailable",
            422: "protocol_rejected", 429: "rate_limited"}.get(status, "remote_http_error")


def privacy_ok(value):
    text = json.dumps(value, ensure_ascii=False, default=str)
    return not re.search(r"sk-[A-Za-z0-9_-]{8,}|[A-Za-z]:\\|Traceback|customer_name|customer_phone|"
                         r"(?<!\d)1[3-9]\d{9}(?!\d)", text)


@dataclass
class HttpTrace:
    allowed_tools: set[str]
    requests: list[dict] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    error: str | None = None
    returned_calls: dict[str, str] = field(default_factory=dict, repr=False)
    observed_results: set[str] = field(default_factory=set, repr=False)

    def stop(self, code):
        self.error = self.error or code
        raise AcceptanceStop(code)

    def start(self, request):
        if self.error:
            self.stop(self.error)
        if len(self.requests) >= 6:
            self.stop("request_budget_exhausted")
        if (request.method != "POST" or request.url.scheme != "https"
                or request.url.host != "api.deepseek.com"
                or request.url.path not in {"/chat/completions", "/v1/chat/completions"}):
            self.stop("unexpected_endpoint")
        body = json.loads(request.content)
        if body.get("model") != MODEL or body.get("stream", False):
            self.stop("unexpected_model_or_stream")
        if body.get("thinking") != {"type": "disabled"}:
            self.stop("thinking_protocol_not_disabled")
        if {tool["function"]["name"] for tool in body.get("tools", [])} != TOOLS:
            self.stop("tool_contract_changed")
        for message in body.get("messages", []):
            if message.get("role") != "tool":
                continue
            call_id = message.get("tool_call_id")
            if call_id in self.observed_results:
                continue
            self.observed_results.add(call_id)
            name = self.returned_calls.get(call_id)
            data = json.loads(message["content"])
            if isinstance(data, dict) and "error" in data:
                self.stop("local_tool_failed")
            summary = {"name": name}
            if name == "get_order":
                summary["masked_fields_only"] = isinstance(data, dict) and set(data) == ORDER_FIELDS
                if not summary["masked_fields_only"]:
                    self.stop("order_privacy_failed")
                if data["id"] not in {"ORD-10001", "ORD-10002"}:
                    self.stop("unexpected_simulated_order")
                summary["order_id"] = data["id"]
            elif name == "search_knowledge":
                if not isinstance(data, list) or any(not re.fullmatch(r"OFFICIAL-[A-Z]+-\d{3}", d.get("id", "")) for d in data):
                    self.stop("invalid_evidence")
                summary["document_ids"] = [d["id"] for d in data]
                summary["metadata_complete"] = all(all(d.get(k) for k in (
                    "section", "source_url", "source_published_at", "source_fetched_at")) for d in data)
            elif name == "request_refund_approval":
                summary["pending_only"] = data.get("status") == "pending" and data.get("action_type") == "refund"
                if not summary["pending_only"]:
                    self.stop("refund_boundary_failed")
            elif name == "create_ticket":
                if data.get("category") not in {"delivery", "invoice"} or data.get("status") != "open":
                    self.stop("ticket_boundary_failed")
                summary["category"] = data["category"]
            else:
                self.stop("unmatched_tool_result")
            self.tool_results.append(summary)
        row = {"ordinal": len(self.requests) + 1, "started_at": datetime.now(timezone.utc).isoformat(),
               "http_status": None, "response_model": None, "tool_calls": [], "usage": None,
               "cost_bounds_cny": None, "failure": None}
        self.requests.append(row)
        return row

    def finish(self, row, response):
        row["http_status"] = response.status_code
        if response.status_code != 200:
            row["failure"] = failure_code(response.status_code)
            self.stop(row["failure"])
        body = response.json()
        returned_model = body.get("model")
        row["response_model"] = returned_model if returned_model in {MODEL, "deepseek-v4-pro-0813"} else "unexpected_model"
        row["usage"] = usage_summary(body)
        row["cost_bounds_cny"] = cost_bounds(row["usage"])
        if row["response_model"] == "unexpected_model":
            self.stop("response_model_mismatch")
        choices = body.get("choices", [])
        if len(choices) != 1 or not isinstance(choices[0].get("message"), dict):
            self.stop("invalid_chat_response")
        choice = choices[0]
        finish = choice.get("finish_reason")
        row["finish_reason"] = finish if finish in {"stop", "tool_calls", "length", "content_filter"} else "other"
        for call in choice["message"].get("tool_calls") or []:
            function = call.get("function", {})
            name = function.get("name")
            safe = {"name": name if name in TOOLS else "unregistered_tool"}
            row["tool_calls"].append(safe)
            if name not in self.allowed_tools:
                self.stop("unexpected_tool_for_scenario")
            self.returned_calls[call["id"]] = name
            arguments = json.loads(function["arguments"])
            if "order_id" in arguments:
                order_id = arguments["order_id"]
                if order_id not in {"ORD-10001", "ORD-10002"}:
                    self.stop("unexpected_simulated_order")
                safe["order_id"] = order_id
            if name == "create_ticket":
                if arguments.get("category") not in {"delivery", "invoice"}:
                    self.stop("unexpected_ticket_category")
                safe["category"] = arguments["category"]
        if finish not in {"stop", "tool_calls"}:
            self.stop("incomplete_model_response")


def observed_http_client(trace):
    """Use the installed SDK's public HTTP client (httpx/httpx2 compatible)."""
    from openai import DefaultAsyncHttpxClient

    class ObservedClient(DefaultAsyncHttpxClient):
        async def send(self, request, *args, **kwargs):
            row = trace.start(request)
            started = perf_counter()
            try:
                response = await super().send(request, *args, **kwargs)
                await response.aread()
                trace.finish(row, response)
                return response
            except Exception as error:
                if trace.error is None:
                    kind = type(error).__name__
                    trace.error = "transport_timeout" if "Timeout" in kind else "transport_or_protocol_error"
                row["failure"] = trace.error
                raise AcceptanceStop(trace.error) from None
            finally:
                row["elapsed_ms"] = round((perf_counter() - started) * 1000, 2)

    return ObservedClient(follow_redirects=False)


def check_scenario(case, response, audit, store, trace):
    actual = [call["name"] for row in trace.requests for call in row["tool_calls"]]
    executed = [row["name"] for row in trace.tool_results]
    issues = []
    def require(ok, code):
        if not ok:
            issues.append(code)
    require(set(case["expected_tools"]) <= set(actual), "expected_model_tool_missing")
    require(set(case["expected_tools"]) <= set(executed), "expected_tool_result_missing")
    require(trace.error is None and bool(trace.requests), "model_request_failed")
    require(all(r["http_status"] == 200 for r in trace.requests), "model_response_failed")
    require(bool(trace.requests) and trace.requests[-1].get("finish_reason") == "stop"
            and not trace.requests[-1]["tool_calls"], "model_loop_not_complete")
    graphs = [r["payload"] for r in audit if r["event_type"] == "langgraph_completed"]
    require(bool(graphs) and graphs[-1]["mode"] == "live_langgraph" and "tools" in graphs[-1]["nodes"], "tool_node_missing")
    require(not any(r["payload"].get("outcome") == "blocked" for r in audit), "tool_was_blocked")
    citations = response.citations
    retrievals = [r["payload"] for r in audit if r["event_type"] == "retrieval_completed"]
    ids = [c.document_id for c in citations]
    require(bool(retrievals) and ids == retrievals[-1]["document_ids"], "citation_retrieval_mismatch")
    require(response.retrieval is not None and response.retrieval.retriever == "candidate"
            and ids == response.retrieval.document_ids, "retrieval_trace_mismatch")
    documents = {d.id: d for d in store.list_knowledge()}
    from app.service import OpsPilotService
    require(all(c.document_id in documents and c == OpsPilotService.to_citation(documents[c.document_id]) for c in citations),
            "citation_metadata_mismatch")
    if case["id"] == "knowledge":
        require(bool(ids), "knowledge_evidence_empty")
    tickets, approvals = store.list_tickets(), store.list_pending_approvals()
    require([t.model_dump() for t in tickets] == [t.model_dump() for t in response.tickets], "ticket_response_mismatch")
    require([a.model_dump() for a in approvals] == [a.model_dump() for a in response.approvals], "approval_response_mismatch")
    require(len(tickets) == int(case["id"] in {"delivery", "invoice"}), "unexpected_ticket_count")
    require(len(approvals) == int(case["id"] == "refund"), "unexpected_approval_count")
    require(all(t.category == case["id"] and t.status == "open" for t in tickets), "ticket_category_or_state_mismatch")
    expected_order = "ORD-10002" if case["id"] == "delivery" else "ORD-10001"
    require(all(t.order_id == expected_order for t in tickets), "ticket_order_mismatch")
    require(all(r["order_id"] == expected_order for r in trace.tool_results if r["name"] == "get_order"), "queried_order_mismatch")
    require(all(a.status == "pending" and a.payload.get("order_id") == "ORD-10001" for a in approvals), "approval_not_pending")
    require(not any(r["event_type"] == "approval_decided" for r in audit), "unexpected_approval_decision")
    require({r["payload"]["ticket_id"] for r in audit if r["event_type"] == "ticket_created"} == {t.id for t in tickets}, "ticket_audit_mismatch")
    require({r["payload"]["approval_id"] for r in audit if r["event_type"] == "approval_requested"} == {a.id for a in approvals}, "approval_audit_mismatch")
    completed = [r["payload"] for r in audit if r["event_type"] == "agent_completed"]
    require(bool(completed) and completed[-1]["citation_ids"] == ids
            and completed[-1]["ticket_ids"] == [t.id for t in response.tickets]
            and completed[-1]["approval_ids"] == [a.id for a in response.approvals], "completion_audit_mismatch")
    require(privacy_ok(response.model_dump(mode="json")) and privacy_ok(audit), "privacy_check_failed")
    return issues
