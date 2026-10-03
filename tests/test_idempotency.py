"""Offline acceptance of durable actions, including real spawned processes."""
import asyncio
import json
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.fake_deepseek import FakeDeepSeekModel
from app.main import create_app
from app.models import AgentResponse, RequestTrace
from app.request_store import RequestError, request_scope
from app.service import OpsPilotService
from app.store import Store

MESSAGES = {"refund": "订单 ORD-10001 申请退款", "delivery": "订单 ORD-10002 物流异常",
            "invoice": "订单 ORD-10001 申请电子发票"}


@pytest.fixture(autouse=True)
def candidate(monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "langgraph")
    monkeypatch.setenv("KNOWLEDGE_RETRIEVER", "candidate")


def send(client, cid, message, key="submission-1"):
    return client.post(f"/api/conversations/{cid}/messages", json={"message": message},
                       headers={"Idempotency-Key": key} if key is not None else {})


def counts(path):
    with sqlite3.connect(path) as db:
        return {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in ("tickets", "approvals", "request_actions", "message_requests", "approval_decisions")}


def audits(client, cid):
    return client.get(f"/api/conversations/{cid}/audit").json()


@pytest.mark.parametrize("kind", MESSAGES)
def test_sequential_http_retry_replays_database_result(tmp_path, kind):
    path = tmp_path / "sequential.db"
    with TestClient(create_app(path)) as client:
        cid = client.post("/api/conversations").json()["conversation_id"]
        first, second = [send(client, cid, MESSAGES[kind]) for _ in range(2)]
        assert first.status_code == second.status_code == 200
        assert first.headers["Idempotency-Status"] == "completed"
        assert second.headers["Idempotency-Status"] == "replayed"
        field = "approvals" if kind == "refund" else "tickets"
        assert first.json()[field][0]["id"] == second.json()[field][0]["id"]
        assert counts(path)[field] == counts(path)["request_actions"] == 1
        events = audits(client, cid)
        created = "approval_requested" if kind == "refund" else "ticket_created"
        assert sum(r["event_type"] == created for r in events) == 1
        assert sum(r["event_type"] == "business_action_reused" for r in events) == 1
        assert sum(r["event_type"] == "langgraph_completed" for r in events) == 1
        assert any(r["event_type"] == "request_replayed" for r in events)


@pytest.mark.parametrize("kind", MESSAGES)
def test_action_commits_then_model_fails_retry_recovers_without_model(tmp_path, monkeypatch, kind):
    monkeypatch.setenv("AGENT_MODE", "live")
    path = tmp_path / "partial.db"
    factories = []
    def factory():
        fake = FakeDeepSeekModel(kind + "_then_error")
        factories.append(fake)
        return fake
    with TestClient(create_app(path, live_model_factory=factory)) as client:
        cid = client.post("/api/conversations").json()["conversation_id"]
        failed = send(client, cid, MESSAGES[kind])
        assert failed.status_code == 503 and failed.json()["detail"]["partial"] is True
        assert "已保存" in failed.json()["detail"]["message"]
        assert "PRIVATE_MODEL_EXCEPTION" not in failed.text
        field = "approvals" if kind == "refund" else "tickets"
        assert counts(path)[field] == 1
        response = send(client, cid, MESSAGES[kind])
        assert response.status_code == 200
        assert response.json()["request"]["outcome"] == "recovered"
        assert response.json()["request"]["partial_recovery"] is True
        assert len(factories) == 1 and factories[0].calls == 2
        assert counts(path)[field] == counts(path)["request_actions"] == 1
        assert response.json()["citations"] and response.json()["retrieval"]["retriever"] == "candidate"
        if kind == "refund":
            assert response.json()["approvals"][0]["status"] == "pending"
            assert counts(path)["tickets"] == 0 and "未执行资金退款" in response.json()["message"]
        events = audits(client, cid)
        assert any(r["event_type"] == "request_failed" and r["payload"]["partial"] for r in events)
        assert any(r["event_type"] == "request_recovered" for r in events)
        assert any(r["event_type"] == "business_action_reused" for r in events)
        assert sum(r["event_type"] == ("approval_requested" if kind == "refund" else "ticket_created") for r in events) == 1


def test_conflicting_body_rejected_but_distinct_keys_and_conversations_allowed(tmp_path):
    path = tmp_path / "scope.db"
    with TestClient(create_app(path)) as client:
        cid = client.post("/api/conversations").json()["conversation_id"]
        first = send(client, cid, MESSAGES["refund"])
        conflict = send(client, cid, MESSAGES["invoice"])
        assert first.status_code == 200 and conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "idempotency_conflict"
        assert counts(path)["approvals"] == 1 and counts(path)["tickets"] == 0
        assert any(r["event_type"] == "request_conflict" for r in audits(client, cid))
        other = send(client, cid, MESSAGES["refund"], "submission-2")
        other_cid = client.post("/api/conversations").json()["conversation_id"]
        other_conversation = send(client, other_cid, MESSAGES["refund"])
        assert other.status_code == other_conversation.status_code == 200
        assert counts(path)["approvals"] == counts(path)["request_actions"] == 3
        assert len({r.json()["approvals"][0]["id"] for r in (first, other, other_conversation)}) == 3
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT key_hash FROM message_requests LIMIT 1").fetchone()[0] != "submission-1"


def test_missing_key_is_compatible_and_explicitly_unprotected(tmp_path):
    path = tmp_path / "legacy.db"
    with TestClient(create_app(path)) as client:
        cid = client.post("/api/conversations").json()["conversation_id"]
        for _ in range(2):
            response = send(client, cid, MESSAGES["refund"], None)
            assert response.status_code == 200 and response.headers["Idempotency-Status"] == "unprotected"
            assert response.json()["request"] is None
        assert counts(path)["approvals"] == 2 and counts(path)["message_requests"] == 0


def test_parallel_http_same_key_and_conflict_are_rejected_while_running(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "live")
    path = tmp_path / "parallel.db"
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        class WaitingFake(FakeDeepSeekModel):
            async def ainvoke(self, messages):
                if self.calls == 0:
                    entered.set()
                    await release.wait()
                return await super().ainvoke(messages)
        fake = WaitingFake("refund")
        app = create_app(path, live_model_factory=lambda: fake)
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                cid = (await client.post("/api/conversations")).json()["conversation_id"]
                url = f"/api/conversations/{cid}/messages"
                first = asyncio.create_task(client.post(url, json={"message": MESSAGES["refund"]}, headers={"Idempotency-Key": "one-key"}))
                await asyncio.wait_for(entered.wait(), 5)
                in_progress = await client.post(url, json={"message": MESSAGES["refund"]}, headers={"Idempotency-Key": "one-key"})
                conflict = await client.post(url, json={"message": MESSAGES["invoice"]}, headers={"Idempotency-Key": "one-key"})
                assert in_progress.status_code == conflict.status_code == 409
                assert in_progress.json()["detail"]["code"] == "request_in_progress"
                assert conflict.json()["detail"]["code"] == "idempotency_conflict"
                release.set()
                assert (await first).status_code == 200
                replay = await client.post(url, json={"message": MESSAGES["refund"]}, headers={"Idempotency-Key": "one-key"})
                assert replay.status_code == 200 and fake.calls == 2
                events = app.state.service.store.list_audit(cid)
                assert sum(r["event_type"] == "approval_requested" for r in events) == 1
                assert any(r["event_type"] == "request_in_progress" for r in events)
        finally:
            app.state.service.store.close()
    asyncio.run(run())
    assert counts(path)["approvals"] == counts(path)["request_actions"] == 1


def process_submission(path, cid, kind, queue, barrier=None, crash=False):
    """Spawned process: explicitly Fake only, without inherited credentials."""
    os.environ.update(AGENT_MODE="live", KNOWLEDGE_RETRIEVER="candidate", DEEPSEEK_API_KEY="",
                      LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    class CrashFake(FakeDeepSeekModel):
        async def ainvoke(self, messages):
            if self.calls:
                os._exit(17)  # Simulate abrupt termination AFTER the durable tool transaction.
            return await super().ainvoke(messages)
    fake = CrashFake("refund") if crash else FakeDeepSeekModel(kind)
    service = OpsPilotService(Store(path), live_model_factory=lambda: fake)
    try:
        if barrier:
            barrier.wait(timeout=15)
        result = asyncio.run(service.handle_submission(cid, MESSAGES["refund"], "process-key"))
        queue.put({"status": 200, "id": result.approvals[0].id, "calls": fake.calls, "pid": os.getpid()})
    except RequestError as error:
        queue.put({"status": error.status, "code": error.detail["code"], "calls": fake.calls, "pid": os.getpid()})
    finally:
        service.store.close()


def start_process(context, path, cid, kind, queue, barrier=None, crash=False):
    process = context.Process(target=process_submission, args=(str(path), cid, kind, queue, barrier, crash))
    process.start()
    return process


def join_process(process, expected=0):
    process.join(timeout=25)
    if process.is_alive():
        process.terminate()
        process.join()
        pytest.fail("offline worker timed out")
    assert process.exitcode == expected


def test_two_processes_same_key_have_only_one_database_action(tmp_path):
    path = tmp_path / "process-parallel.db"
    service = OpsPilotService(Store(path))
    cid = service.store.create_conversation()
    service.store.close()
    context = multiprocessing.get_context("spawn")
    queue, barrier = context.Queue(), context.Barrier(2)
    workers = [start_process(context, path, cid, "refund", queue, barrier) for _ in range(2)]
    for process in workers:
        join_process(process)
    rows = [queue.get(timeout=5) for _ in workers]
    queue.close()
    assert any(row["status"] == 200 for row in rows)
    assert all(row["status"] in {200, 409} for row in rows)
    assert counts(path)["approvals"] == counts(path)["request_actions"] == 1
    store = Store(path)
    try:
        assert sum(r["event_type"] == "approval_requested" for r in store.list_audit(cid)) == 1
    finally:
        store.close()


def test_restart_after_partial_failure_recovers_in_fresh_process(tmp_path):
    path = tmp_path / "restart.db"
    service = OpsPilotService(Store(path))
    cid = service.store.create_conversation()
    service.store.close()
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    first = start_process(context, path, cid, "refund_then_error", queue)
    join_process(first)
    first_result = queue.get(timeout=5)
    second = start_process(context, path, cid, "refund_then_error", queue)
    join_process(second)
    second_result = queue.get(timeout=5)
    queue.close()
    assert first_result["status"] == 503 and second_result["status"] == 200
    assert first_result["pid"] != second_result["pid"] and second_result["calls"] == 0
    assert counts(path)["approvals"] == 1 and counts(path)["tickets"] == 0
    store = Store(path)
    try:
        events = store.list_audit(cid)
        assert any(r["event_type"] == "request_failed" and r["payload"]["partial"] for r in events)
        assert any(r["event_type"] == "request_recovered" for r in events)
    finally:
        store.close()


def test_hard_crash_recovers_committed_action_after_lease_expiry(tmp_path):
    path = tmp_path / "crash.db"
    service = OpsPilotService(Store(path))
    cid = service.store.create_conversation()
    service.store.close()
    context, queue = multiprocessing.get_context("spawn"), multiprocessing.get_context("spawn").Queue()
    worker = start_process(context, path, cid, "refund", queue, crash=True)
    join_process(worker, expected=17)
    queue.close()
    assert counts(path)["approvals"] == counts(path)["request_actions"] == 1
    with TestClient(create_app(path)) as client:
        pending = send(client, cid, MESSAGES["refund"], "process-key")
        assert pending.status_code == 409 and pending.json()["detail"]["code"] == "request_in_progress"
        with sqlite3.connect(path) as db:
            db.execute("UPDATE message_requests SET lease_until=0 WHERE conversation_id=?", (cid,))
        recovered = send(client, cid, MESSAGES["refund"], "process-key")
        assert recovered.status_code == 200 and recovered.json()["request"]["outcome"] == "recovered"
        assert counts(path)["approvals"] == 1 and counts(path)["tickets"] == 0
        assert sum(r["event_type"] == "approval_requested" for r in audits(client, cid)) == 1


def test_expired_worker_is_fenced_and_ledger_unique_constraint_is_real(tmp_path):
    path = tmp_path / "fenced.db"
    service = OpsPilotService(Store(path))
    try:
        cid = service.store.create_conversation()
        first = service.store.claim_request(cid, "fenced-key", MESSAGES["refund"])
        with sqlite3.connect(path) as db:
            db.execute("UPDATE message_requests SET lease_until=0 WHERE id=?", (first["id"],))
        second = service.store.claim_request(cid, "fenced-key", MESSAGES["refund"])
        with request_scope(first["owner"]), pytest.raises(RequestError, match="执行权"):
            service.request_refund_approval(cid, "ORD-10001", "退款")
        with request_scope(second["owner"]):
            approval = service.request_refund_approval(cid, "ORD-10001", "退款")
            reused = service.request_refund_approval(cid, "ORD-10001", "不同生成原因")
            assert approval.id == reused.id
        assert counts(path)["approvals"] == 1
        with sqlite3.connect(path) as db, pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO request_actions SELECT request_id, action_key, result_type, 'another-id', created_at FROM request_actions")
        assert sum(r["event_type"] == "approval_requested" for r in service.store.list_audit(cid)) == 1
        assert any(r["event_type"] == "business_action_reused" for r in service.store.list_audit(cid))
    finally:
        service.store.close()


def test_failed_ledger_insert_rolls_back_business_and_creation_audit(tmp_path):
    path = tmp_path / "atomic.db"
    service = OpsPilotService(Store(path))
    try:
        cid = service.store.create_conversation()
        claim = service.store.claim_request(cid, "atomic-key", MESSAGES["refund"])
        with sqlite3.connect(path) as db:
            db.execute("CREATE TRIGGER fail_ledger BEFORE INSERT ON request_actions BEGIN SELECT RAISE(ABORT, 'offline fault'); END")
        with request_scope(claim["owner"]), pytest.raises(sqlite3.IntegrityError):
            service.request_refund_approval(cid, "ORD-10001", "退款")
        assert counts(path)["approvals"] == counts(path)["request_actions"] == 0
        assert not any(r["event_type"] == "approval_requested" for r in service.store.list_audit(cid))
    finally:
        service.store.close()


def test_migration_preserves_legacy_business_and_operator_approval_is_atomic(tmp_path):
    path = tmp_path / "legacy-migration.db"
    original = Store(path)
    cid = original.create_conversation()
    old_ticket = original.create_ticket(cid, "delivery", "high", "保留已有工单", "ORD-10002")
    old_approval = original.create_refund_approval(cid, original.get_order("ORD-10001"), "保留已有审批")
    original.audit(cid, "legacy_event", {"preserved": True})
    original.close()
    services = [OpsPilotService(Store(path)) for _ in range(2)]
    try:
        assert services[0].store.list_tickets()[0].id == old_ticket.id
        assert services[0].store.list_pending_approvals()[0].id == old_approval.id
        assert any(r["event_type"] == "legacy_event" for r in services[0].store.list_audit(cid))
        def approve(service):
            try:
                return service.decide_approval(old_approval.id, True, "operator")
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, services))
        assert sum(row is not None for row in results) == 1
        tickets = services[0].store.list_tickets()
        assert sum(t.category == "refund" for t in tickets) == 1
        assert counts(path)["approval_decisions"] == 1
        assert sum(r["event_type"] == "approval_decided" for r in services[0].store.list_audit(cid)) == 1
        with pytest.raises(ValueError):
            services[0].decide_approval(old_approval.id, False, "operator")
    finally:
        for service in services:
            service.store.close()


def test_replay_refreshes_human_decided_approval_and_cannot_bypass_live_vector(tmp_path, monkeypatch):
    path = tmp_path / "decided.db"
    with TestClient(create_app(path)) as client:
        cid = client.post("/api/conversations").json()["conversation_id"]
        first = send(client, cid, MESSAGES["refund"])
        approval_id = first.json()["approvals"][0]["id"]
        url = f"/api/approvals/{approval_id}/decision"
        assert client.post(url, json={"approved": True}).status_code == 200
        assert client.post(url, json={"approved": True}).status_code == 409
        replay = send(client, cid, MESSAGES["refund"])
        assert replay.json()["approvals"][0]["status"] == "approved"
        assert "当前状态：approved" in replay.json()["message"]
        assert "未执行资金退款" in replay.json()["message"]
        monkeypatch.setenv("AGENT_MODE", "live")
        monkeypatch.setenv("KNOWLEDGE_RETRIEVER", "vector_rag")
        blocked = send(client, cid, MESSAGES["refund"])
        assert blocked.status_code == 503 and "RAG 检索不可用" in blocked.json()["detail"]
        assert counts(path)["approvals"] == counts(path)["tickets"] == 1
