"""Durable request/action ledger around the existing Store business methods.

Only new tables are added. The frozen Store retrievers and original tables remain
unchanged. A transaction view defers Store.commit until the action AND ledger AND
creation audit have been written, so a crash cannot leave an untracked action.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock

from .models import new_id
from .store import Store, dump_time, now

LEASE_SECONDS = 300


class RequestError(RuntimeError):
    def __init__(self, code, message, *, status=409, request_id=None, partial=False):
        super().__init__(message)
        self.status = status
        self.detail = {"code": code, "message": message, "request_id": request_id, "partial": partial}


@dataclass(frozen=True)
class RequestOwner:
    database: str
    request_id: str
    token: str


_owner: ContextVar[RequestOwner | None] = ContextVar("message_request_owner", default=None)


@contextmanager
def request_scope(owner):
    token = _owner.set(owner)
    try:
        yield
    finally:
        _owner.reset(token)


class _DeferredCommit:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        # The outer BEGIN IMMEDIATE owns commit/rollback.
        pass


class _TransactionStore(Store):
    def __init__(self, path, connection):
        # An already migrated DB; never initialize/seed it inside this view.
        self.path = path
        self.connection = _DeferredCommit(connection)


class RequestStore:
    """Store facade: serialize its shared handle, use short DB transactions for claims/actions."""
    def __init__(self, base: Store):
        self.base = base
        self.path = base.path
        self.database = str(base.path.resolve())
        self.lock = RLock()
        with self.transaction() as unit:
            for sql in (
                """CREATE TABLE IF NOT EXISTS message_requests (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, key_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('running','failed','completed')),
                    owner_token TEXT NOT NULL, lease_until REAL NOT NULL,
                    evidence_json TEXT, response_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(conversation_id, key_hash)
                )""",
                """CREATE TABLE IF NOT EXISTS request_actions (
                    request_id TEXT NOT NULL REFERENCES message_requests(id), action_key TEXT NOT NULL,
                    result_type TEXT NOT NULL CHECK(result_type IN ('ticket','approval')),
                    result_id TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(request_id, action_key), UNIQUE(result_type, result_id)
                )""",
                """CREATE TABLE IF NOT EXISTS approval_decisions (
                    approval_id TEXT PRIMARY KEY REFERENCES approvals(id), ticket_id TEXT UNIQUE,
                    status TEXT NOT NULL, created_at TEXT NOT NULL
                )""",
            ):
                unit.connection.execute(sql)

    def __getattr__(self, name):
        attribute = getattr(self.base, name)
        if not callable(attribute):
            return attribute
        def locked(*args, **kwargs):
            with self.lock:
                return attribute(*args, **kwargs)
        return locked

    @contextmanager
    def transaction(self):
        # Never keep this lock/transaction while invoking a model or graph.
        with self.lock:
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield _TransactionStore(self.path, connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    @property
    def request_owner(self):
        owner = _owner.get()
        return owner if owner and owner.database == self.database else None

    def _owned(self, unit):
        owner = self.request_owner
        row = unit.connection.execute("SELECT * FROM message_requests WHERE id=?", (owner.request_id,)).fetchone() if owner else None
        if not row or row["status"] != "running" or row["owner_token"] != owner.token or row["lease_until"] <= time.time():
            raise RequestError("request_ownership_lost", "请求执行权已失效，请使用原标识重试。",
                               request_id=owner.request_id if owner else None)
        return row

    def claim_request(self, conversation_id, key, message):
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
            raise RequestError("invalid_idempotency_key", "Idempotency-Key 须为 1–128 位字母、数字或 . _ : -。", status=400)
        key_hash = hashlib.sha256(key.encode()).hexdigest()
        content_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        error = None
        with self.transaction() as unit:
            row = unit.connection.execute("SELECT * FROM message_requests WHERE conversation_id=? AND key_hash=?",
                                          (conversation_id, key_hash)).fetchone()
            if row and row["content_hash"] != content_hash:
                unit.audit(conversation_id, "request_conflict", {"request_id": row["id"], "code": "different_content"})
                error = RequestError("idempotency_conflict", "同一请求标识对应的消息内容不同，请核对原提交；新提交应使用新标识。", request_id=row["id"])
            elif row and row["status"] == "running" and row["lease_until"] > time.time():
                unit.audit(conversation_id, "request_in_progress", {"request_id": row["id"]})
                error = RequestError("request_in_progress", "原请求仍在处理中，请稍后使用原标识重试。", request_id=row["id"])
            elif row and row["status"] == "completed":
                claim = {"id": row["id"], "mode": "replay", "owner": None}
            else:
                request_id = row["id"] if row else new_id("request")
                owner = RequestOwner(self.database, request_id, new_id("owner"))
                stamp = dump_time(now())
                if row:
                    unit.connection.execute("UPDATE message_requests SET status='running', owner_token=?, lease_until=?, updated_at=? WHERE id=?",
                                            (owner.token, time.time() + LEASE_SECONDS, stamp, request_id))
                else:
                    unit.connection.execute("INSERT INTO message_requests VALUES (?, ?, ?, ?, 'running', ?, ?, NULL, NULL, ?, ?)",
                                            (request_id, conversation_id, key_hash, content_hash, owner.token, time.time() + LEASE_SECONDS, stamp, stamp))
                has_actions = unit.connection.execute("SELECT 1 FROM request_actions WHERE request_id=?", (request_id,)).fetchone()
                unit.audit(conversation_id, "request_retried" if row else "request_started", {"request_id": request_id})
                claim = {"id": request_id, "mode": "recover" if has_actions else "execute", "owner": owner}
        if error:
            raise error  # Audit must commit even when the HTTP request is rejected.
        return claim

    def audit(self, conversation_id, event_type, payload):
        if not self.request_owner:
            with self.lock:
                return self.base.audit(conversation_id, event_type, payload)
        with self.transaction() as unit:
            row = self._owned(unit)
            unit.audit(conversation_id, event_type, {**payload, "request_id": row["id"]})

    def add_message(self, conversation_id, role, content):
        if not self.request_owner:
            with self.lock:
                return self.base.add_message(conversation_id, role, content)
        with self.transaction() as unit:
            self._owned(unit)
            unit.add_message(conversation_id, role, content)

    def checkpoint_evidence(self, evidence):
        if self.request_owner:
            with self.transaction() as unit:
                row = self._owned(unit)
                unit.connection.execute("UPDATE message_requests SET evidence_json=?, updated_at=? WHERE id=?",
                                        (json.dumps(evidence, ensure_ascii=False), dump_time(now()), row["id"]))

    @staticmethod
    def _result(unit, result_type, result_id):
        table = "tickets" if result_type == "ticket" else "approvals"
        row = unit.connection.execute(f"SELECT * FROM {table} WHERE id=?", (result_id,)).fetchone()
        if not row:
            raise RequestError("request_data_incomplete", "已有业务记录不完整，请转人工；不会重新创建。", status=503)
        return unit._ticket_from_row(row) if result_type == "ticket" else unit._approval_from_row(row)

    def _action(self, conversation_id, action_key, result_type, create):
        with self.transaction() as unit:
            row = self._owned(unit)
            if row["conversation_id"] != conversation_id:
                raise RequestError("request_scope_mismatch", "请求会话不匹配。")
            existing = unit.connection.execute("SELECT * FROM request_actions WHERE request_id=? AND action_key=?",
                                               (row["id"], action_key)).fetchone()
            if existing:
                result = self._result(unit, result_type, existing["result_id"])
                unit.audit(conversation_id, "business_action_reused", {"request_id": row["id"], "action_key": action_key, "result_id": result.id})
                return result
            result = create(unit)
            unit.connection.execute("INSERT INTO request_actions VALUES (?, ?, ?, ?, ?)",
                                    (row["id"], action_key, result_type, result.id, dump_time(now())))
            if result_type == "ticket":
                event, payload = "ticket_created", {"ticket_id": result.id, "category": result.category, "order_id": result.order_id}
            else:
                event, payload = "approval_requested", {"approval_id": result.id, "order_id": result.payload["order_id"],
                                                        "amount": result.payload["amount"], "status": "pending"}
            unit.audit(conversation_id, event, {**payload, "request_id": row["id"]})
            return result

    def create_ticket(self, conversation_id, category, priority, summary, order_id=None):
        if not self.request_owner:
            with self.lock:
                return self.base.create_ticket(conversation_id, category, priority, summary, order_id)
        order_id = order_id.upper() if order_id else None
        return self._action(conversation_id, f"ticket:{category}:{order_id or '-'}", "ticket",
                            lambda unit: unit.create_ticket(conversation_id, category, priority, summary, order_id))

    def create_refund_approval(self, conversation_id, order, reason):
        if not self.request_owner:
            with self.lock:
                return self.base.create_refund_approval(conversation_id, order, reason)
        return self._action(conversation_id, f"refund:{order.id.upper()}", "approval",
                            lambda unit: unit.create_refund_approval(conversation_id, order, reason))

    def snapshot_request(self, request_id, *, reuse=False):
        with self.transaction() as unit:
            row = unit.connection.execute("SELECT * FROM message_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise RequestError("request_not_found", "请求记录不存在。", status=503)
            if reuse and row["status"] != "completed":
                self._owned(unit)
            tickets, approvals = [], []
            actions = unit.connection.execute("SELECT * FROM request_actions WHERE request_id=? ORDER BY created_at, action_key", (request_id,)).fetchall()
            for action in actions:
                result = self._result(unit, action["result_type"], action["result_id"])
                (tickets if action["result_type"] == "ticket" else approvals).append(result)
                if reuse:
                    unit.audit(row["conversation_id"], "business_action_reused", {"request_id": request_id,
                               "action_key": action["action_key"], "result_id": result.id})
            if reuse and row["status"] == "completed":
                unit.audit(row["conversation_id"], "request_replayed", {"request_id": request_id})
            return dict(row), tickets, approvals

    def finish_request(self, response, *, recovered=False):
        with self.transaction() as unit:
            row = self._owned(unit)
            unit.connection.execute("UPDATE message_requests SET status='completed', response_json=?, lease_until=0, updated_at=? WHERE id=?",
                                    (response.model_dump_json(), dump_time(now()), row["id"]))
            if recovered:
                unit.add_message(row["conversation_id"], "assistant", response.message)
            unit.audit(row["conversation_id"], "request_recovered" if recovered else "request_completed",
                       {"request_id": row["id"], "ticket_ids": [t.id for t in response.tickets], "approval_ids": [a.id for a in response.approvals]})

    def fail_request(self):
        owner = self.request_owner
        with self.transaction() as unit:
            row = unit.connection.execute("SELECT * FROM message_requests WHERE id=?", (owner.request_id,)).fetchone()
            partial = bool(unit.connection.execute("SELECT 1 FROM request_actions WHERE request_id=?", (owner.request_id,)).fetchone())
            if row["owner_token"] == owner.token and row["status"] == "running":
                unit.connection.execute("UPDATE message_requests SET status='failed', lease_until=0, updated_at=? WHERE id=?",
                                        (dump_time(now()), owner.request_id))
                unit.audit(row["conversation_id"], "request_failed", {"request_id": owner.request_id, "partial": partial})
            return partial

    def decide_approval(self, approval_id, approved, decided_by):
        # Existing pending/status validation + refund ticket creation now commit
        # together, even for approvals created before this migration.
        with self.transaction() as unit:
            approval, ticket = unit.decide_approval(approval_id, approved, decided_by)
            unit.connection.execute("INSERT INTO approval_decisions VALUES (?, ?, ?, ?)",
                                    (approval.id, ticket.id if ticket else None, approval.status, dump_time(now())))
            unit.audit(approval.conversation_id, "approval_decided", {"approval_id": approval.id,
                       "status": approval.status, "decided_by": decided_by, "ticket_id": ticket.id if ticket else None})
            return approval, ticket
