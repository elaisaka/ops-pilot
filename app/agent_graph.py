"""Deterministic LangGraph orchestration for the default OpsPilot agent.

The graph owns routing and state transitions.  It deliberately delegates every
database read or write to :class:`OpsPilotService`, which remains the policy
boundary for business operations.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .models import AgentResponse, Citation, KnowledgeDocument, RetrievalTrace, ToolEvent
from .evidence_retrieval import RAG_NO_EVIDENCE

if TYPE_CHECKING:
    from .service import OpsPilotService


ORDER_ID = re.compile(r"ORD-\d{5}", re.IGNORECASE)
INJECTION_MARKERS = ("忽略以上", "ignore previous", "系统提示词", "system prompt")

Intent = Literal["refund", "delivery", "invoice", "knowledge"]


class AgentGraphState(TypedDict, total=False):
    """State passed between one small, auditable graph node at a time."""

    conversation_id: str
    user_message: str
    normalized_message: str
    order_id: str | None
    intent: Intent
    blocked: bool
    documents: list[KnowledgeDocument]
    citations: list[Citation]
    tool_events: list[ToolEvent]
    response: AgentResponse
    graph_nodes: list[str]
    retrieval: RetrievalTrace


def build_agent_graph(service: OpsPilotService):
    """Compile the local, deterministic single-agent graph.

    This function intentionally has no model or API-key dependency.  The same
    transition boundaries can later be reused by a model-backed classifier.
    """

    graph = StateGraph(AgentGraphState)

    def trace(state: AgentGraphState, node: str, **updates: object) -> dict[str, object]:
        return {"graph_nodes": [*state.get("graph_nodes", []), node], **updates}

    def guardrail(state: AgentGraphState) -> dict[str, object]:
        normalized = state["user_message"].lower()
        if any(marker in normalized for marker in INJECTION_MARKERS):
            response = AgentResponse(
                conversation_id=state["conversation_id"],
                message="我只能处理售后知识查询、订单查询和工单流程，无法执行或披露系统指令。",
                tool_events=[ToolEvent(name="input_guardrail", outcome="blocked", detail="检测到越权指令")],
            )
            return trace(state, "guardrail", normalized_message=normalized, blocked=True, response=response)
        return trace(state, "guardrail", normalized_message=normalized, blocked=False)

    def route_after_guardrail(state: AgentGraphState) -> Literal["blocked", "continue"]:
        return "blocked" if state["blocked"] else "continue"

    def retrieve_evidence(state: AgentGraphState) -> dict[str, object]:
        documents, retrieval = service.retrieve_knowledge(state["conversation_id"], state["user_message"])
        citations = [service.to_citation(document) for document in documents]
        tools = [ToolEvent(name="search_knowledge", outcome="success", detail=f"命中 {len(documents)} 篇制度文档")]
        updates = {"documents": documents, "citations": citations, "tool_events": tools, "retrieval": retrieval}
        if retrieval.retriever == "vector_rag" and retrieval.empty:
            updates["response"] = AgentResponse(conversation_id=state["conversation_id"],
                                                message=RAG_NO_EVIDENCE, tool_events=tools)
        return trace(state, "retrieve_evidence", **updates)

    def route_after_retrieval(state: AgentGraphState) -> Literal["empty", "continue"]:
        return "empty" if state["retrieval"].retriever == "vector_rag" and state["retrieval"].empty else "continue"

    def classify_or_decide(state: AgentGraphState) -> dict[str, object]:
        normalized = state["normalized_message"]
        if any(word in normalized for word in ("退款", "退货")):
            intent: Intent = "refund"
        elif any(word in normalized for word in ("物流", "没收到", "未收到", "延迟")):
            intent = "delivery"
        elif "发票" in normalized:
            intent = "invoice"
        else:
            intent = "knowledge"
        order_match = ORDER_ID.search(state["user_message"])
        return trace(state, "classify_or_decide", intent=intent, order_id=order_match.group().upper() if order_match else None)

    def route_after_decision(state: AgentGraphState) -> Intent:
        return state["intent"]

    def refund_action(state: AgentGraphState) -> dict[str, object]:
        response = service.prepare_refund_response(
            state["conversation_id"],
            state["user_message"],
            state.get("order_id"),
            state["citations"],
            state["tool_events"],
        )
        return trace(state, "request_refund_approval", response=response)

    def delivery_action(state: AgentGraphState) -> dict[str, object]:
        response = service.prepare_delivery_response(
            state["conversation_id"],
            state["user_message"],
            state.get("order_id"),
            state["citations"],
            state["tool_events"],
        )
        return trace(state, "create_delivery_ticket", response=response)

    def invoice_action(state: AgentGraphState) -> dict[str, object]:
        response = service.prepare_invoice_response(
            state["conversation_id"],
            state["user_message"],
            state.get("order_id"),
            state["citations"],
            state["tool_events"],
        )
        return trace(state, "create_invoice_ticket", response=response)

    def knowledge_action(state: AgentGraphState) -> dict[str, object]:
        response = AgentResponse(
            conversation_id=state["conversation_id"],
            message=service.knowledge_reply(state["documents"]),
            citations=state["citations"],
            tool_events=state["tool_events"],
        )
        return trace(state, "knowledge_reply", response=response)

    def finalize_audit(state: AgentGraphState) -> dict[str, object]:
        # The explicit node name is appended before persisting, so the audit trail
        # proves that the reply passed through this LangGraph transition.
        nodes = [*state.get("graph_nodes", []), "finalize_audit"]
        state["response"].retrieval = state.get("retrieval")
        service.finalize_agent_response(state["conversation_id"], state["response"], nodes, mode="langgraph")
        return {"graph_nodes": nodes}

    graph.add_node("guardrail", guardrail)
    graph.add_node("retrieve_evidence", retrieve_evidence)
    graph.add_node("classify_or_decide", classify_or_decide)
    graph.add_node("request_refund_approval", refund_action)
    graph.add_node("create_delivery_ticket", delivery_action)
    graph.add_node("create_invoice_ticket", invoice_action)
    graph.add_node("knowledge_reply", knowledge_action)
    graph.add_node("finalize_audit", finalize_audit)
    graph.add_edge(START, "guardrail")
    graph.add_conditional_edges("guardrail", route_after_guardrail, {"blocked": "finalize_audit", "continue": "retrieve_evidence"})
    graph.add_conditional_edges("retrieve_evidence", route_after_retrieval,
                                {"empty": "finalize_audit", "continue": "classify_or_decide"})
    graph.add_conditional_edges(
        "classify_or_decide",
        route_after_decision,
        {
            "refund": "request_refund_approval",
            "delivery": "create_delivery_ticket",
            "invoice": "create_invoice_ticket",
            "knowledge": "knowledge_reply",
        },
    )
    for action_node in ("request_refund_approval", "create_delivery_ticket", "create_invoice_ticket", "knowledge_reply"):
        graph.add_edge(action_node, "finalize_audit")
    graph.add_edge("finalize_audit", END)
    return graph.compile()
