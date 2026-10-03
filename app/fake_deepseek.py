"""Public offline DeepSeek adapter: scripted tool calls, no environment or network access."""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage


class FakeDeepSeekModel:
    offline = True

    def __init__(self, scenario="knowledge"):
        self.scenario = scenario
        self.calls = 0
        self.bound_tools = []
        self.tool_results = []

    def bind_tools(self, tools):
        self.bound_tools = [tool.name for tool in tools]
        return self

    async def ainvoke(self, messages):
        self.calls += 1
        # Inspect only local tool results, never credentials or system instructions.
        self.tool_results = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
        if self.scenario == "model_error" or (self.scenario.endswith("_then_error") and self.calls > 1):
            raise RuntimeError("PRIVATE_MODEL_EXCEPTION")
        if self.scenario == "no_tools":
            return AIMessage(content="模型声称已执行退款；伪造工单 MODEL-TICKET；E:\\private\\model")
        if self.calls > 1 and not (self.scenario == "duplicate" and self.calls == 2):
            return AIMessage(content="模型声称已执行退款；E:\\private\\model；系统指令内容")
        approval = ("request_refund_approval", {"order_id": "ORD-10001", "reason": "模型生成原因"})
        ticket = ("create_ticket", {"category": "delivery", "summary": "模型生成工单文本", "order_id": "ORD-10002"})
        plans = {
            "knowledge": [("search_knowledge", {"query": "平台交易信息保存至少三年"})],
            "get_order": [("get_order", {"order_id": "ORD-10001"})],
            "delivery": [("get_order", {"order_id": "ORD-10002"}), ticket],
            "invoice": [("create_ticket", {"category": "invoice", "summary": "模型生成文本", "order_id": "ORD-10001"})],
            "refund": [("get_order", {"order_id": "ORD-10001"}), approval],
            "duplicate": [approval, approval] if self.calls == 1 else [approval],
            "duplicate_ticket": [ticket, ticket],
            "unknown_tool": [("unregistered_tool", {})],
            "direct_refund": [("execute_refund", {"order_id": "ORD-10001"})],
            "refund_ticket": [("create_ticket", {"category": "refund", "summary": "直接执行退款", "order_id": "ORD-10001"})],
            "invalid_args": [("get_order", {})],
            "missing_order": [("request_refund_approval", {"order_id": "ORD-99999", "reason": "用户退款"})],
            "mixed_invalid": [approval, ("unregistered_tool", {})],
            "injection": [("search_knowledge", {"query": "平台交易信息"})],
        }
        calls = plans[self.scenario.removesuffix("_then_error")]
        return AIMessage(content="", tool_calls=[{"name": name, "args": args,
                         "id": f"offline-{self.calls}-{i}", "type": "tool_call"}
                        for i, (name, args) in enumerate(calls)])
