from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Callable, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .service import LiveModeConfigurationError, OpsPilotService
from .evidence_retrieval import RetrievalUnavailableError
from .store import Store
from .live_config import live_ready
from .request_store import RequestError
from .auth import require_roles, ROLES


class MessageRequest(BaseModel):
    message: Annotated[str, Field(min_length=1, max_length=2000)]


class ApprovalDecisionRequest(BaseModel):
    approved: bool
    decided_by: Annotated[str, Field(min_length=2, max_length=80)] = "operator"


class TicketUpdateRequest(BaseModel):
    status: Literal["in_progress", "resolved"] | None = None
    assignee: Annotated[str | None, Field(min_length=2, max_length=80)] = None
    resolution_note: Annotated[str | None, Field(min_length=3, max_length=500)] = None


def create_app(database_path: str | Path | None = None, *, live_model_factory: Callable[[], Any] | None = None) -> FastAPI:
    database_path = database_path or os.getenv("DATABASE_PATH", "data/ops_pilot.db")
    service = OpsPilotService(Store(database_path), live_model_factory=live_model_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        service.store.close()

    app = FastAPI(
        title="OpsPilot", version="0.1.0", description="Auditable knowledge-base and ticket agent", lifespan=lifespan
    )
    app.state.service = service

    @app.get("/api/auth/me")
    def identity(user=Depends(require_roles(*ROLES))):
        return user

    @app.get("/health")
    def health() -> dict:
        mode = os.getenv("AGENT_MODE", "langgraph")
        retriever = os.getenv("KNOWLEDGE_RETRIEVER", "candidate")
        return {"status": "ok", "agent_mode": mode if mode in {"live", "langgraph", "deterministic"} else "invalid",
                "live_ready": live_ready(), "llm_provider": "deepseek",
                "knowledge_retriever": retriever if retriever in {"candidate", "vector_rag"} else "invalid"}

    @app.post("/api/conversations", status_code=201)
    def create_conversation() -> dict:
        conversation_id = service.store.create_conversation()
        return {"conversation_id": conversation_id}

    @app.post("/api/conversations/{conversation_id}/messages")
    async def send_message(conversation_id: str, request: MessageRequest, response: Response,
                           idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None):
        try:
            result = await service.handle_submission(conversation_id, request.message, idempotency_key)
            response.headers["Idempotency-Status"] = result.request.outcome if result.request else "unprotected"
            return result
        except RequestError as error:
            headers = {"Idempotency-Status": error.detail["code"]}
            if error.detail["code"] in {"request_in_progress", "request_ownership_lost"}:
                headers["Retry-After"] = "5"
            raise HTTPException(status_code=error.status, detail=error.detail, headers=headers) from None
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except (LiveModeConfigurationError, RetrievalUnavailableError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/conversations/{conversation_id}/audit", dependencies=[Depends(require_roles("supervisor", "auditor"))])
    def get_audit(conversation_id: str) -> list[dict]:
        if not service.store.conversation_exists(conversation_id):
            raise HTTPException(status_code=404, detail="conversation not found")
        return service.store.list_audit(conversation_id)

    @app.get("/api/approvals", dependencies=[Depends(require_roles(*ROLES))])
    def list_approvals():
        return service.store.list_pending_approvals()

    @app.post("/api/approvals/{approval_id}/decision")
    def decide_approval(approval_id: str, request: ApprovalDecisionRequest, user=Depends(require_roles("supervisor"))):
        try:
            return service.decide_approval(approval_id, request.approved, user["name"])
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/tickets", dependencies=[Depends(require_roles(*ROLES))])
    def list_tickets():
        return service.store.list_tickets()

    @app.patch("/api/tickets/{ticket_id}")
    def update_ticket(ticket_id: str, request: TicketUpdateRequest, user=Depends(require_roles("operator", "supervisor"))):
        try:
            return service.update_ticket(ticket_id, request.status, user["name"], request.resolution_note)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/api/orders", dependencies=[Depends(require_roles(*ROLES))])
    def list_orders(status: str | None = None, limit: int = 100):
        return service.store.list_orders(status=status, limit=min(max(limit, 1), 100))

    @app.get("/api/dashboard", dependencies=[Depends(require_roles(*ROLES))])
    def dashboard():
        return service.store.dashboard_metrics()

    @app.get("/api/knowledge")
    def list_knowledge():
        return service.store.list_knowledge()

    @app.get("/")
    def index():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    return app


app = create_app()
