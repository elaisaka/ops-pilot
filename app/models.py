from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Citation(BaseModel):
    document_id: str
    title: str
    excerpt: str
    section: str = ""
    source_title: str = ""
    source_url: str = ""
    source_published_at: str = ""
    source_fetched_at: str = ""


class KnowledgeDocument(BaseModel):
    id: str
    title: str
    tags: list[str]
    content: str
    updated_at: datetime
    section: str = ""
    source_type: str = "internal_demo"
    source_title: str = ""
    source_url: str = ""
    source_published_at: str = ""
    source_fetched_at: str = ""
    scope: str = ""


class Order(BaseModel):
    id: str
    customer_name: str
    customer_phone_masked: str
    status: str
    amount: float
    product_name: str = ""
    tracking_number: str | None = None
    after_sale_status: str = "none"
    created_at: datetime | None = None
    delivered_at: datetime | None = None


class Ticket(BaseModel):
    id: str
    conversation_id: str
    category: Literal["refund", "delivery", "invoice", "general"]
    priority: Literal["low", "medium", "high"]
    summary: str
    status: Literal["open", "in_progress", "resolved"] = "open"
    order_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    assignee: str | None = None
    resolution_note: str | None = None


class Approval(BaseModel):
    id: str
    conversation_id: str
    action_type: Literal["refund"]
    status: Literal["pending", "approved", "rejected"]
    payload: dict[str, Any]
    reason: str
    requested_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None


class ToolEvent(BaseModel):
    name: str
    outcome: Literal["success", "blocked", "pending"]
    detail: str


class RetrievalTrace(BaseModel):
    """Public, allow-listed retrieval metadata: never paths, queries or raw errors."""

    retriever: Literal["candidate", "vector_rag"]
    document_ids: list[str]
    scores: list[float] = Field(default_factory=list)
    threshold: float | None = None
    model: str | None = None
    revision: str | None = None
    index_manifest_sha256: str | None = None
    elapsed_ms: float
    empty: bool


class RequestTrace(BaseModel):
    id: str
    outcome: Literal["completed", "replayed", "recovered"]
    partial_recovery: bool = False


class AgentResponse(BaseModel):
    conversation_id: str
    message: str
    citations: list[Citation] = Field(default_factory=list)
    tickets: list[Ticket] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    tool_events: list[ToolEvent] = Field(default_factory=list)
    retrieval: RetrievalTrace | None = None
    request: RequestTrace | None = None
