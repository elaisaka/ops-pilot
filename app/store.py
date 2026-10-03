from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .knowledge_sources import iter_official_chunks
from .models import Approval, KnowledgeDocument, Order, Ticket, new_id


def now() -> datetime:
    return datetime.now(UTC)


def dump_time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def load_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class Store:
    """A small SQLite repository; replace this class with real business systems in production."""

    def __init__(self, database_path: str | Path) -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        self.seed_demo_data()

    def close(self) -> None:
        """Release the SQLite handle explicitly (important for temporary DBs on Windows)."""
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, tags TEXT NOT NULL,
                content TEXT NOT NULL, updated_at TEXT NOT NULL,
                section TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT '',
                source_title TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                source_published_at TEXT NOT NULL DEFAULT '', source_fetched_at TEXT NOT NULL DEFAULT '',
                scope TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY, customer_name TEXT NOT NULL,
                customer_phone_masked TEXT NOT NULL, status TEXT NOT NULL,
                amount REAL NOT NULL, product_name TEXT NOT NULL DEFAULT '', tracking_number TEXT,
                after_sale_status TEXT NOT NULL DEFAULT 'none', created_at TEXT, delivered_at TEXT
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tickets (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                category TEXT NOT NULL, priority TEXT NOT NULL, summary TEXT NOT NULL,
                status TEXT NOT NULL, order_id TEXT, created_at TEXT NOT NULL, updated_at TEXT,
                assignee TEXT, resolution_note TEXT
            );
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                action_type TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
                reason TEXT NOT NULL, requested_at TEXT NOT NULL, decided_at TEXT,
                decided_by TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, event_type TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            """
        )
        # Migrate the original demo database in place without touching tickets or conversations.
        known_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(knowledge_documents)")}
        for name, definition in {
            "section": "TEXT NOT NULL DEFAULT ''",
            "source_type": "TEXT NOT NULL DEFAULT ''",
            "source_title": "TEXT NOT NULL DEFAULT ''",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "source_published_at": "TEXT NOT NULL DEFAULT ''",
            "source_fetched_at": "TEXT NOT NULL DEFAULT ''",
            "scope": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in known_columns:
                self.connection.execute(f"ALTER TABLE knowledge_documents ADD COLUMN {name} {definition}")
        order_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(orders)")}
        for name, definition in {
            "product_name": "TEXT NOT NULL DEFAULT ''",
            "tracking_number": "TEXT",
            "after_sale_status": "TEXT NOT NULL DEFAULT 'none'",
            "created_at": "TEXT",
        }.items():
            if name not in order_columns:
                self.connection.execute(f"ALTER TABLE orders ADD COLUMN {name} {definition}")
        ticket_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(tickets)")}
        for name, definition in {
            "updated_at": "TEXT",
            "assignee": "TEXT",
            "resolution_note": "TEXT",
        }.items():
            if name not in ticket_columns:
                self.connection.execute(f"ALTER TABLE tickets ADD COLUMN {name} {definition}")
        self.connection.commit()

    def seed_demo_data(self) -> None:
        # Delete only packaged seed documents; user-imported documents are preserved.
        self.connection.execute("DELETE FROM knowledge_documents WHERE id LIKE 'KB-%'")
        self.connection.execute("DELETE FROM knowledge_documents WHERE id LIKE 'OFFICIAL-%'")
        documents = list(iter_official_chunks())
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO knowledge_documents (
                id, title, tags, content, updated_at, section, source_type, source_title,
                source_url, source_published_at, source_fetched_at, scope
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    document["id"], document["title"], json.dumps(document["tags"], ensure_ascii=False), document["content"],
                    f"{document['source_fetched_at']}T00:00:00+00:00", document["section"], document["source_type"],
                    document["source_title"], document["source_url"], document["source_published_at"],
                    document["source_fetched_at"], document["scope"],
                )
                for document in documents
            ],
        )
        orders = [
            ("ORD-10001", "王小明", "138****2345", "已签收", 299.0, "降噪蓝牙耳机", "SFDEMO10001", "refund_requested", "2026-09-10T09:30:00+00:00", "2026-09-15T09:30:00+00:00"),
            ("ORD-10002", "李然", "139****8821", "运输中", 88.0, "智能保温杯", "YTDEMO10002", "none", "2026-09-16T11:00:00+00:00", None),
            ("ORD-10003", "陈默", "136****1010", "已发货", 520.0, "人体工学键盘", "JDDEMO10003", "none", "2026-09-18T08:45:00+00:00", None),
        ]
        products = ["便携榨汁杯", "折叠键盘", "智能台灯", "旅行收纳包", "无线充电器", "护颈枕", "运动水壶", "桌面支架"]
        names = ["赵晨", "孙悦", "周宁", "吴桐", "郑安", "冯雪", "褚阳", "卫岚"]
        statuses = ["待发货", "已发货", "运输中", "已签收", "已签收", "已取消"]
        after_sale_statuses = ["none", "none", "none", "none", "invoice_requested", "none", "delivery_issue"]
        carriers = ["SF", "YT", "JD", "ZTO"]
        for index, number in enumerate(range(10004, 10064), start=1):
            status = statuses[index % len(statuses)]
            delivered_at = f"2026-09-{(index % 18) + 1:02d}T14:00:00+00:00" if status == "已签收" else None
            orders.append((
                f"ORD-{number}", names[index % len(names)], f"13{index % 10}****{2000 + index:04d}", status,
                round(39 + (index * 37.5) % 760, 2), products[index % len(products)],
                f"{carriers[index % len(carriers)]}DEMO{number}", after_sale_statuses[index % len(after_sale_statuses)],
                f"2026-08-{(index % 28) + 1:02d}T10:00:00+00:00", delivered_at,
            ))
        self.connection.executemany(
            """
            INSERT OR IGNORE INTO orders (
                id, customer_name, customer_phone_masked, status, amount, product_name,
                tracking_number, after_sale_status, created_at, delivered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            orders,
        )
        # Backfill the three original demo rows if this database was created before order metadata existed.
        self.connection.executemany(
            """
            UPDATE orders
            SET product_name = ?, tracking_number = ?, after_sale_status = ?, created_at = ?
            WHERE id = ? AND product_name = ''
            """,
            [(order[5], order[6], order[7], order[8], order[0]) for order in orders],
        )
        self.connection.commit()

    def create_conversation(self) -> str:
        conversation_id = new_id("conv")
        self.connection.execute("INSERT INTO conversations VALUES (?, ?)", (conversation_id, dump_time(now())))
        self.connection.commit()
        return conversation_id

    def conversation_exists(self, conversation_id: str) -> bool:
        return self.connection.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone() is not None

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        self.connection.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
            (new_id("msg"), conversation_id, role, content, dump_time(now())),
        )
        self.connection.commit()

    def search_knowledge(self, query: str, limit: int = 3) -> list[KnowledgeDocument]:
        return self.search_knowledge_candidate(query, limit)

    def search_knowledge_candidate(self, query: str, limit: int = 3) -> list[KnowledgeDocument]:
        from .retrieval import rank_documents

        return rank_documents(query, self.list_knowledge(), limit)

    def search_knowledge_baseline(self, query: str, limit: int = 3) -> list[KnowledgeDocument]:
        rows = self.connection.execute("SELECT * FROM knowledge_documents").fetchall()
        query_lower = query.lower()
        ranked: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            tags = json.loads(row["tags"])
            score = sum(6 for tag in tags if tag.lower() in query_lower)
            score += sum(1 for character in query_lower if len(character.strip()) and character in row["title"])
            score += 2 if row["source_url"] else 0
            if score:
                ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [self._document_from_row(row) for _, row in ranked[:limit]]

    def list_knowledge(self) -> list[KnowledgeDocument]:
        rows = self.connection.execute("SELECT * FROM knowledge_documents ORDER BY id").fetchall()
        return [self._document_from_row(row) for row in rows]

    def get_order(self, order_id: str) -> Order | None:
        row = self.connection.execute("SELECT * FROM orders WHERE id = ?", (order_id.upper(),)).fetchone()
        return Order(
            id=row["id"], customer_name=row["customer_name"], customer_phone_masked=row["customer_phone_masked"],
            status=row["status"], amount=row["amount"], product_name=row["product_name"],
            tracking_number=row["tracking_number"], after_sale_status=row["after_sale_status"],
            created_at=load_time(row["created_at"]), delivered_at=load_time(row["delivered_at"]),
        ) if row else None

    def list_orders(self, status: str | None = None, limit: int = 100) -> list[Order]:
        sql = "SELECT * FROM orders"
        parameters: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            parameters = (status,)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        rows = self.connection.execute(sql, (*parameters, limit)).fetchall()
        return [
            Order(
                id=row["id"], customer_name=row["customer_name"], customer_phone_masked=row["customer_phone_masked"],
                status=row["status"], amount=row["amount"], product_name=row["product_name"],
                tracking_number=row["tracking_number"], after_sale_status=row["after_sale_status"],
                created_at=load_time(row["created_at"]), delivered_at=load_time(row["delivered_at"]),
            )
            for row in rows
        ]

    def create_ticket(
        self, conversation_id: str, category: str, priority: str, summary: str, order_id: str | None = None
    ) -> Ticket:
        created_at = now()
        ticket = Ticket(
            id=new_id("ticket"), conversation_id=conversation_id, category=category, priority=priority,
            summary=summary, order_id=order_id, created_at=created_at, updated_at=created_at,
        )
        self.connection.execute(
            """
            INSERT INTO tickets (
                id, conversation_id, category, priority, summary, status, order_id, created_at,
                updated_at, assignee, resolution_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticket.id, ticket.conversation_id, ticket.category, ticket.priority, ticket.summary, ticket.status,
             ticket.order_id, dump_time(ticket.created_at), dump_time(ticket.updated_at), ticket.assignee, ticket.resolution_note),
        )
        self.connection.commit()
        return ticket

    def update_ticket(
        self, ticket_id: str, status: str | None = None, assignee: str | None = None, resolution_note: str | None = None
    ) -> Ticket:
        row = self.connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise KeyError("ticket not found")
        current = self._ticket_from_row(row)
        next_status = status or current.status
        allowed_transitions = {
            "open": {"open", "in_progress"},
            "in_progress": {"in_progress", "resolved"},
            "resolved": {"resolved"},
        }
        if next_status not in allowed_transitions[current.status]:
            raise ValueError(f"invalid ticket transition: {current.status} -> {next_status}")
        next_note = resolution_note if resolution_note is not None else current.resolution_note
        if next_status == "resolved" and not next_note:
            raise ValueError("a resolution note is required before resolving a ticket")
        updated_at = now()
        updated = current.model_copy(update={
            "status": next_status, "assignee": assignee if assignee is not None else current.assignee,
            "resolution_note": next_note, "updated_at": updated_at,
        })
        self.connection.execute(
            "UPDATE tickets SET status = ?, assignee = ?, resolution_note = ?, updated_at = ? WHERE id = ?",
            (updated.status, updated.assignee, updated.resolution_note, dump_time(updated.updated_at), ticket_id),
        )
        self.connection.commit()
        return updated

    def create_refund_approval(self, conversation_id: str, order: Order, reason: str) -> Approval:
        approval = Approval(
            id=new_id("approval"), conversation_id=conversation_id, action_type="refund", status="pending",
            payload={"order_id": order.id, "amount": order.amount}, reason=reason, requested_at=now(),
        )
        self.connection.execute(
            "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (approval.id, approval.conversation_id, approval.action_type, approval.status,
             json.dumps(approval.payload, ensure_ascii=False), approval.reason, dump_time(approval.requested_at), None, None),
        )
        self.connection.commit()
        return approval

    def list_pending_approvals(self) -> list[Approval]:
        rows = self.connection.execute("SELECT * FROM approvals WHERE status = 'pending' ORDER BY requested_at DESC").fetchall()
        return [self._approval_from_row(row) for row in rows]

    def decide_approval(self, approval_id: str, approved: bool, decided_by: str) -> tuple[Approval, Ticket | None]:
        row = self.connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise KeyError("approval not found")
        approval = self._approval_from_row(row)
        if approval.status != "pending":
            raise ValueError("approval has already been decided")
        status = "approved" if approved else "rejected"
        decided_at = now()
        self.connection.execute(
            "UPDATE approvals SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
            (status, dump_time(decided_at), decided_by, approval_id),
        )
        decided = approval.model_copy(update={"status": status, "decided_at": decided_at, "decided_by": decided_by})
        ticket = None
        if approved:
            ticket = self.create_ticket(
                approval.conversation_id, "refund", "high",
                f"已批准退款：{approval.payload['order_id']}，金额 ¥{approval.payload['amount']:.2f}；原因：{approval.reason}",
                approval.payload["order_id"],
            )
        self.connection.commit()
        return decided, ticket

    def audit(self, conversation_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
            (new_id("audit"), conversation_id, event_type, json.dumps(payload, ensure_ascii=False), dump_time(now())),
        )
        self.connection.commit()

    def list_audit(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM audit_events WHERE conversation_id = ? ORDER BY created_at", (conversation_id,)
        ).fetchall()
        return [
            {"id": row["id"], "event_type": row["event_type"], "payload": json.loads(row["payload"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def list_tickets(self) -> list[Ticket]:
        rows = self.connection.execute("SELECT * FROM tickets ORDER BY created_at DESC").fetchall()
        return [self._ticket_from_row(row) for row in rows]

    def list_tickets_for_conversation(self, conversation_id: str) -> list[Ticket]:
        return [ticket for ticket in self.list_tickets() if ticket.conversation_id == conversation_id]

    def dashboard_metrics(self) -> dict[str, Any]:
        def counts(table: str, field: str) -> dict[str, int]:
            rows = self.connection.execute(f"SELECT {field}, COUNT(*) AS count FROM {table} GROUP BY {field}").fetchall()
            return {row[field]: row["count"] for row in rows}

        return {
            "orders": {"total": self.connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0], "by_status": counts("orders", "status")},
            "tickets": {"total": self.connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0], "by_status": counts("tickets", "status")},
            "pending_approvals": self.connection.execute("SELECT COUNT(*) FROM approvals WHERE status = 'pending'").fetchone()[0],
            "knowledge_documents": self.connection.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0],
        }

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        return Approval(
            id=row["id"], conversation_id=row["conversation_id"], action_type=row["action_type"], status=row["status"],
            payload=json.loads(row["payload"]), reason=row["reason"], requested_at=load_time(row["requested_at"]),
            decided_at=load_time(row["decided_at"]), decided_by=row["decided_by"],
        )

    @staticmethod
    def _ticket_from_row(row: sqlite3.Row) -> Ticket:
        return Ticket(
            id=row["id"], conversation_id=row["conversation_id"], category=row["category"], priority=row["priority"],
            summary=row["summary"], status=row["status"], order_id=row["order_id"], created_at=load_time(row["created_at"]) or now(),
            updated_at=load_time(row["updated_at"]), assignee=row["assignee"], resolution_note=row["resolution_note"],
        )

    @staticmethod
    def _document_from_row(row: sqlite3.Row) -> KnowledgeDocument:
        return KnowledgeDocument(
            id=row["id"], title=row["title"], tags=json.loads(row["tags"]), content=row["content"],
            updated_at=load_time(row["updated_at"]) or now(), section=row["section"], source_type=row["source_type"],
            source_title=row["source_title"], source_url=row["source_url"],
            source_published_at=row["source_published_at"], source_fetched_at=row["source_fetched_at"], scope=row["scope"],
        )
