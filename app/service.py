from __future__ import annotations

import os
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable, NoReturn, Sequence

from .models import AgentResponse, Citation, KnowledgeDocument, Order, RequestTrace, RetrievalTrace, ToolEvent
from .evidence_retrieval import RAG_UNAVAILABLE, RetrievalUnavailableError, vector_evidence
from .store import Store
from .live_config import DeepSeekConfig, LIVE_UNAVAILABLE, MISSING_KEY, LiveModeConfigurationError
from .request_store import RequestError, RequestStore, request_scope


@dataclass
class OpsPilotService:
    """Business policy boundary used by both deterministic and live LangGraph paths."""

    store: Store
    live_model_factory: Callable[[], Any] | None = field(default=None, repr=False)
    agent_graph: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Import here to keep the graph's type-only reference to this service free
        # of a module-import cycle.
        from .agent_graph import build_agent_graph

        if not isinstance(self.store, RequestStore):
            self.store = RequestStore(self.store)
        self.agent_graph = build_agent_graph(self)

    async def handle_submission(self, conversation_id: str, user_message: str,
                                idempotency_key: str | None = None) -> AgentResponse:
        """HTTP submission boundary. The model never supplies the request key."""
        live = os.getenv("AGENT_MODE", "langgraph").lower() == "live"
        if idempotency_key is None:
            # Backwards-compatible callers explicitly get no cross-request guarantee.
            return (await self.handle_live_message(conversation_id, user_message) if live
                    else self.handle_message(conversation_id, user_message))
        if not self.store.conversation_exists(conversation_id):
            raise KeyError("conversation not found")
        from .agent_graph import INJECTION_MARKERS
        blocked = any(marker in user_message.lower() for marker in INJECTION_MARKERS)
        if live and not blocked and os.getenv("KNOWLEDGE_RETRIEVER", "candidate") != "candidate":
            self._retrieval_failure(conversation_id, "unsupported_live_retriever")
        claim = self.store.claim_request(conversation_id, idempotency_key, user_message)
        if claim["mode"] == "replay":
            return self._reuse_submission(claim["id"], recovered=False)
        with request_scope(claim["owner"]):
            try:
                if claim["mode"] == "recover":
                    response = self._reuse_submission(claim["id"], recovered=True)
                else:
                    response = (await self.handle_live_message(conversation_id, user_message) if live
                                else self.handle_message(conversation_id, user_message))
                    response.request = RequestTrace(id=claim["id"], outcome="completed")
                self.store.finish_request(response, recovered=claim["mode"] == "recover")
                return response
            except Exception as error:
                partial = self.store.fail_request()
                if isinstance(error, RequestError):
                    raise
                # Do not imply rollback: the action and its audit may already be durable.
                if partial:
                    message = "后续处理失败，但部分审批或工单已保存；请使用原 Idempotency-Key 重试以恢复结果，未执行退款。"
                elif isinstance(error, (LiveModeConfigurationError, RetrievalUnavailableError)):
                    message = str(error)  # These are sanitized at the model/retrieval boundary.
                else:
                    message = "本次处理失败，请使用原 Idempotency-Key 重试或转人工。"
                raise RequestError("request_failed", message, status=503, request_id=claim["id"], partial=partial) from None

    def _reuse_submission(self, request_id: str, *, recovered: bool) -> AgentResponse:
        row, tickets, approvals = self.store.snapshot_request(request_id, reuse=True)
        if row["response_json"]:
            response = AgentResponse.model_validate_json(row["response_json"])
        else:
            evidence = json.loads(row["evidence_json"] or "{}")
            response = AgentResponse(conversation_id=row["conversation_id"], message="", **evidence)
        partial = recovered or bool(response.request and response.request.partial_recovery)
        response.request = RequestTrace(id=request_id, outcome="recovered" if recovered else "replayed", partial_recovery=partial)
        response.tickets, response.approvals = tickets, approvals
        if tickets or approvals:
            parts = ["已复用此请求已保存的业务记录，没有重复创建。"]
            parts += [f"审批 {a.id} 当前状态：{a.status}；未执行资金退款。" for a in approvals]
            parts += [f"{t.category} 工单 {t.id} 当前状态：{t.status}。" for t in tickets]
            if partial:
                parts.append("上次模型处理未完成；本次仅恢复已落库结果，不自动补办其他动作，如需继续请转人工。")
            response.message = "\n".join(parts)
            response.tool_events = [ToolEvent(name="request_refund_approval", outcome="pending" if a.status == "pending" else "success",
                                             detail=f"复用审批 {a.id}，当前状态 {a.status}") for a in approvals]
            response.tool_events += [ToolEvent(name="create_ticket", outcome="success", detail=f"复用工单 {t.id}") for t in tickets]
        return response

    def handle_message(self, conversation_id: str, user_message: str) -> AgentResponse:
        """Run every local request through the compiled LangGraph state graph."""
        if not self.store.conversation_exists(conversation_id):
            raise KeyError("conversation not found")
        self.store.add_message(conversation_id, "user", user_message)
        result = self.agent_graph.invoke(
            {
                "conversation_id": conversation_id,
                "user_message": user_message,
                "graph_nodes": [],
                "tool_events": [],
            }
        )
        response = result.get("response")
        if not isinstance(response, AgentResponse):  # defensive: a graph change must not silently change the API response
            raise RuntimeError("LangGraph completed without an AgentResponse")
        return response

    async def handle_live_message(self, conversation_id: str, user_message: str) -> AgentResponse:
        """Run the optional LangGraph model-and-tools extension.

        The graph is not enabled by default and is never a substitute for service
        policy: each tool below performs the same validation and database writes as
        the deterministic graph's action nodes.
        """
        if not self.store.conversation_exists(conversation_id):
            raise KeyError("conversation not found")
        from .agent_graph import INJECTION_MARKERS
        blocked = any(marker in user_message.lower() for marker in INJECTION_MARKERS)
        if not blocked and os.getenv("KNOWLEDGE_RETRIEVER", "candidate") != "candidate":
            self._retrieval_failure(conversation_id, "unsupported_live_retriever")
        model = None
        if not blocked:
            try:
                if self.live_model_factory is None:
                    DeepSeekConfig.from_env().validate()
                else:
                    model = self.live_model_factory()
                    if getattr(model, "offline", False) is not True:
                        raise LiveModeConfigurationError(LIVE_UNAVAILABLE)
            except LiveModeConfigurationError as error:
                self.store.audit(conversation_id, "live_failed", {"provider": "deepseek", "code": "live_configuration_unavailable"})
                raise LiveModeConfigurationError(MISSING_KEY if str(error) == MISSING_KEY else LIVE_UNAVAILABLE) from None
            except Exception:
                self.store.audit(conversation_id, "live_failed", {"provider": "deepseek", "code": "live_adapter_unavailable"})
                raise LiveModeConfigurationError(LIVE_UNAVAILABLE) from None
        self.store.add_message(conversation_id, "user", user_message)
        from .live_agent import run_live_agent

        try:
            result = await run_live_agent(self, conversation_id, user_message, model=model)
        except RequestError:
            raise
        except Exception:
            self.store.audit(conversation_id, "live_failed", {"provider": "deepseek", "code": "live_execution_unavailable"})
            raise LiveModeConfigurationError(LIVE_UNAVAILABLE) from None
        response = AgentResponse(
            conversation_id=conversation_id,
            message=result.message,
            citations=[self.to_citation(document) for document in result.facts.documents],
            tickets=result.facts.tickets,
            approvals=result.facts.approvals,
            tool_events=result.tool_events,
            retrieval=result.facts.retrieval,
        )
        self.finalize_agent_response(conversation_id, response, result.graph_nodes, mode="live_langgraph")
        return response

    # Tool-policy methods -------------------------------------------------
    # These are the only methods graph nodes and live tools use for domain reads
    # and writes. Keeping them here prevents an LLM/tool adapter from bypassing
    # Store validation or creating a financially effective action.

    def search_knowledge(self, conversation_id: str, query: str) -> list[KnowledgeDocument]:
        documents, _ = self.retrieve_knowledge(conversation_id, query)
        return documents

    def _retrieval_failure(self, conversation_id: str, code: str) -> NoReturn:
        # Never serialize the exception, query, configuration values or local paths.
        message = {
            "unsupported_live_retriever": "RAG 检索不可用：本阶段不支持 live 与 vector_rag 组合，请使用确定性模式或显式切回 candidate；本次未执行业务动作。",
            "invalid_retriever": "RAG 检索不可用：KNOWLEDGE_RETRIEVER 仅支持 candidate 或 vector_rag；本次未执行业务动作。",
        }.get(code, RAG_UNAVAILABLE)
        self.store.audit(conversation_id, "retrieval_failed", {"retriever": None if code == "invalid_retriever" else "vector_rag", "code": code,
                         "message": message, "business_actions_executed": False})
        self._audit_tool(conversation_id, "search_knowledge", "blocked", {"code": code})
        raise RetrievalUnavailableError(message) from None

    def retrieve_knowledge(self, conversation_id: str, query: str) -> tuple[list[KnowledgeDocument], RetrievalTrace]:
        mode = os.getenv("KNOWLEDGE_RETRIEVER", "candidate")
        if mode == "candidate":
            start = perf_counter()
            documents = self.store.search_knowledge(query)
            trace = RetrievalTrace(retriever="candidate", document_ids=[doc.id for doc in documents],
                                   elapsed_ms=(perf_counter() - start) * 1000, empty=not documents)
        elif mode == "vector_rag":
            try:
                documents, trace = vector_evidence(self.store, query)
            except Exception:
                # Fail closed for imports, cache/index validation, loading and search.
                self._retrieval_failure(conversation_id, "rag_unavailable")
        else:
            self._retrieval_failure(conversation_id, "invalid_retriever")
        self.store.audit(conversation_id, "retrieval_completed", trace.model_dump(mode="json"))
        self._audit_tool(conversation_id, "search_knowledge", "success", {"document_ids": [doc.id for doc in documents]})
        self.store.checkpoint_evidence({"citations": [self.to_citation(doc).model_dump(mode="json") for doc in documents],
                                        "retrieval": trace.model_dump(mode="json")})
        return documents, trace

    def get_order_for_action(self, conversation_id: str, order_id: str) -> Order | None:
        """Internal action lookup; do not expose this domain object to a model."""
        order = self.store.get_order(order_id)
        self._audit_tool(conversation_id, "get_order", "success" if order else "blocked", {"order_id": order_id.upper()})
        return order

    def get_masked_order(self, conversation_id: str, order_id: str) -> dict[str, object] | None:
        """Return the order fields an agent may see; never expose a customer name or phone."""
        order = self.get_order_for_action(conversation_id, order_id)
        if order is None:
            return None
        return {
            "id": order.id,
            "status": order.status,
            "amount": order.amount,
            "product_name": order.product_name,
            "tracking_number": order.tracking_number,
            "after_sale_status": order.after_sale_status,
        }

    def create_ticket(
        self, conversation_id: str, category: str, summary: str, order_id: str | None = None
    ) -> Any:
        """Create a non-financial ticket after validating the permitted category."""
        priorities = {"delivery": "high", "invoice": "medium", "general": "low"}
        if category not in priorities:
            self._audit_tool(conversation_id, "create_ticket", "blocked", {"category": category})
            raise ValueError("create_ticket only supports delivery, invoice, or general; it cannot create refund execution")
        if order_id and not self.store.get_order(order_id):
            self._audit_tool(conversation_id, "create_ticket", "blocked", {"category": category, "order_id": order_id})
            raise KeyError("order not found")
        ticket = self.store.create_ticket(conversation_id, category, priorities[category], summary, order_id)
        if not self.store.request_owner:
            self.store.audit(conversation_id, "ticket_created", {"ticket_id": ticket.id, "category": category, "order_id": order_id})
        self._audit_tool(conversation_id, "create_ticket", "success", {"ticket_id": ticket.id, "category": category})
        return ticket

    def request_refund_approval(self, conversation_id: str, order_id: str, reason: str) -> Any:
        """Create a *pending* human approval. This method never executes a refund."""
        order = self.store.get_order(order_id)
        if order is None:
            self._audit_tool(conversation_id, "request_refund_approval", "blocked", {"order_id": order_id.upper()})
            raise KeyError("order not found")
        approval = self.store.create_refund_approval(conversation_id, order, reason)
        if not self.store.request_owner:
            self.store.audit(
                conversation_id,
                "approval_requested",
                {"approval_id": approval.id, "order_id": order.id, "amount": order.amount, "status": "pending"},
            )
        self._audit_tool(conversation_id, "request_refund_approval", "pending", {"approval_id": approval.id, "order_id": order.id})
        return approval

    # Deterministic action-node helpers ----------------------------------

    def prepare_refund_response(
        self,
        conversation_id: str,
        message: str,
        order_id: str | None,
        citations: list[Citation],
        tools: list[ToolEvent],
    ) -> AgentResponse:
        if not order_id:
            return AgentResponse(
                conversation_id=conversation_id,
                message="可以协助发起退款审批。请提供订单号（例如 ORD-10001）；涉及退款的操作必须经人工审批。",
                citations=citations,
                tool_events=tools + [ToolEvent(name="request_refund_approval", outcome="blocked", detail="缺少订单号")],
            )
        order = self.get_order_for_action(conversation_id, order_id)
        if not order:
            return AgentResponse(
                conversation_id=conversation_id,
                message=f"未找到订单 {order_id}，请核对订单号后重试。",
                citations=citations,
                tool_events=tools + [ToolEvent(name="get_order", outcome="blocked", detail="订单不存在")],
            )
        approval = self.request_refund_approval(conversation_id, order.id, message)
        return AgentResponse(
            conversation_id=conversation_id,
            message=(f"已查询到订单 {order.id}（状态：{order.status}，金额：¥{order.amount:.2f}）。"
                     f"我已创建退款审批 {approval.id}，等待人工决定；当前未执行退款。"),
            citations=citations,
            approvals=[approval],
            tool_events=tools + [
                ToolEvent(name="get_order", outcome="success", detail=f"订单 {order.id} 状态：{order.status}"),
                ToolEvent(name="request_refund_approval", outcome="pending", detail=f"已创建审批 {approval.id}"),
            ],
        )

    def prepare_delivery_response(
        self,
        conversation_id: str,
        message: str,
        order_id: str | None,
        citations: list[Citation],
        tools: list[ToolEvent],
    ) -> AgentResponse:
        if not order_id:
            return AgentResponse(
                conversation_id=conversation_id,
                message="我可以创建物流异常工单。请补充订单号，以便定位物流状态。",
                citations=citations,
                tool_events=tools + [ToolEvent(name="create_ticket", outcome="blocked", detail="缺少订单号")],
            )
        order = self.get_order_for_action(conversation_id, order_id)
        if not order:
            return AgentResponse(
                conversation_id=conversation_id,
                message=f"未找到订单 {order_id}。",
                citations=citations,
                tool_events=tools + [ToolEvent(name="get_order", outcome="blocked", detail="订单不存在")],
            )
        ticket = self.create_ticket(
            conversation_id,
            "delivery",
            f"物流异常：订单 {order.id} 当前状态为“{order.status}”；用户反馈：{message}",
            order.id,
        )
        return AgentResponse(
            conversation_id=conversation_id,
            message=f"已查询订单 {order.id}（{order.status}），并创建高优先级物流工单 {ticket.id}；客服将在一个工作日内跟进。",
            citations=citations,
            tickets=[ticket],
            tool_events=tools + [
                ToolEvent(name="get_order", outcome="success", detail=f"订单 {order.id} 状态：{order.status}"),
                ToolEvent(name="create_ticket", outcome="success", detail=f"已创建 {ticket.id}"),
            ],
        )

    def prepare_invoice_response(
        self,
        conversation_id: str,
        message: str,
        order_id: str | None,
        citations: list[Citation],
        tools: list[ToolEvent],
    ) -> AgentResponse:
        if order_id:
            order = self.get_order_for_action(conversation_id, order_id)
            if order:
                ticket = self.create_ticket(
                    conversation_id, "invoice", f"电子发票申请：订单 {order.id}；用户说明：{message}", order.id
                )
                return AgentResponse(
                    conversation_id=conversation_id,
                    message=f"已为订单 {order.id} 创建电子发票工单 {ticket.id}。若为企业抬头，请在后续补充准确税号。",
                    citations=citations,
                    tickets=[ticket],
                    tool_events=tools + [
                        ToolEvent(name="get_order", outcome="success", detail=f"订单 {order.id} 状态：{order.status}"),
                        ToolEvent(name="create_ticket", outcome="success", detail=f"已创建 {ticket.id}"),
                    ],
                )
            return AgentResponse(
                conversation_id=conversation_id,
                message=f"未找到订单 {order_id}，请核对订单号后重试。",
                citations=citations,
                tool_events=tools + [ToolEvent(name="get_order", outcome="blocked", detail="订单不存在")],
            )
        return AgentResponse(
            conversation_id=conversation_id,
            message="已完成订单可申请电子普通发票。请提供订单号；如果是企业抬头，还需确认企业名称和税号。",
            citations=citations,
            tool_events=tools,
        )

    # Existing operator actions ------------------------------------------

    def decide_approval(self, approval_id: str, approved: bool, decided_by: str) -> dict:
        approval, ticket = self.store.decide_approval(approval_id, approved, decided_by)
        # Decision, resulting refund ticket, unique decision row and audit are atomic.
        return {"approval": approval, "ticket": ticket}

    def update_ticket(self, ticket_id: str, status: str | None, assignee: str | None, resolution_note: str | None):
        ticket = self.store.update_ticket(ticket_id, status, assignee, resolution_note)
        self.store.audit(
            ticket.conversation_id,
            "ticket_updated",
            {"ticket_id": ticket.id, "status": ticket.status, "assignee": ticket.assignee},
        )
        return ticket

    # Presentation and audit helpers -------------------------------------

    @staticmethod
    def knowledge_reply(documents: Sequence[KnowledgeDocument]) -> str:
        if not documents:
            return "暂未在现有制度中找到直接依据。请补充订单号、问题类型或转人工处理。"
        return "根据制度：" + "；".join(f"《{doc.title}》指出：{doc.content}" for doc in documents[:2])

    @staticmethod
    def to_citation(document: KnowledgeDocument) -> Citation:
        return Citation(
            document_id=document.id,
            title=document.title,
            excerpt=document.content[:100],
            section=document.section,
            source_title=document.source_title,
            source_url=document.source_url,
            source_published_at=document.source_published_at,
            source_fetched_at=document.source_fetched_at,
        )

    def finalize_agent_response(self, conversation_id: str, response: AgentResponse, graph_nodes: list[str], mode: str) -> None:
        """Persist the assistant reply and a graph-level audit record in one final node."""
        self.store.add_message(conversation_id, "assistant", response.message)
        self.store.audit(conversation_id, "langgraph_completed", {"mode": mode, "nodes": graph_nodes})
        self.store.audit(
            conversation_id,
            "agent_completed",
            {
                "tools": [event.model_dump() for event in response.tool_events],
                "citation_ids": [citation.document_id for citation in response.citations],
                "ticket_ids": [ticket.id for ticket in response.tickets],
                "approval_ids": [approval.id for approval in response.approvals],
            },
        )

    def _audit_tool(self, conversation_id: str, tool: str, outcome: str, payload: dict[str, object]) -> None:
        self.store.audit(conversation_id, "tool_called", {"tool": tool, "outcome": outcome, **payload})
