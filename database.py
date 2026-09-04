"""
database.py — SQLite + ORM leve (sqlite3 puro).
Armazena contatos, conversas, mensagens, tags, templates e tarefas para o painel CRM.
O agente Gemini continua usando memory.py (in-memory) para seu histórico.
"""

import os
import sqlite3
import threading
from datetime import datetime
from typing import Any

_DB_PATH = os.getenv("DB_PATH", "whatsapp.db")
_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Cria todas as tabelas se não existirem (e aplica migrações idempotentes)."""
    conn = _get_conn()
    conn.executescript(SCHEMA)

    # Migração idempotente: adiciona colunas de gerenciamento de conversa
    # (arquivada, silenciada, fixada, favorita) se não existirem ainda.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()}
    for col, ddl in (
        ("archived", "ALTER TABLE conversations ADD COLUMN archived INTEGER DEFAULT 0"),
        ("muted", "ALTER TABLE conversations ADD COLUMN muted INTEGER DEFAULT 0"),
        ("pinned", "ALTER TABLE conversations ADD COLUMN pinned INTEGER DEFAULT 0"),
        ("favorite", "ALTER TABLE conversations ADD COLUMN favorite INTEGER DEFAULT 0"),
    ):
        if col not in cols:
            conn.execute(ddl)
    conn.commit()
    conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT UNIQUE NOT NULL,
    name TEXT DEFAULT '',
    email TEXT DEFAULT '',
    company TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    assigned_to TEXT DEFAULT '',
    auto_mode INTEGER DEFAULT 1,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id),
    status TEXT DEFAULT 'new',
    assigned_to TEXT DEFAULT '',
    priority TEXT DEFAULT 'normal',
    unread_count INTEGER DEFAULT 0,
    last_message_at TEXT,
    archived INTEGER DEFAULT 0,
    muted INTEGER DEFAULT 0,
    pinned INTEGER DEFAULT 0,
    favorite INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    phone TEXT NOT NULL,
    uazapi_id TEXT UNIQUE,
    direction TEXT NOT NULL,
    sender TEXT DEFAULT '',
    body TEXT DEFAULT '',
    msg_type TEXT DEFAULT 'text',
    media_url TEXT DEFAULT '',
    status TEXT DEFAULT 'sent',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    color TEXT DEFAULT '#6366f1'
);

CREATE TABLE IF NOT EXISTS contact_tags (
    contact_id INTEGER REFERENCES contacts(id),
    tag_id INTEGER REFERENCES tags(id),
    PRIMARY KEY (contact_id, tag_id)
);

CREATE TABLE IF NOT EXISTS conversation_tags (
    conversation_id INTEGER REFERENCES conversations(id),
    tag_id INTEGER REFERENCES tags(id),
    PRIMARY KEY (conversation_id, tag_id)
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER REFERENCES contacts(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    due_date TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_uazapi ON messages(uazapi_id);
CREATE INDEX IF NOT EXISTS idx_conversations_contact ON conversations(contact_id);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);
CREATE INDEX IF NOT EXISTS idx_tasks_contact ON tasks(contact_id);
"""


# ── Contacts ──────────────────────────────────────────────────────────────────

def upsert_contact(phone: str, name: str = "", **extra: Any) -> dict:
    """Insere ou atualiza contato pelo phone. Retorna o contato."""
    with _lock:
        conn = _get_conn()
        existing = conn.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        now = datetime.utcnow().isoformat()
        if existing:
            fields = []
            vals = []
            for k, v in extra.items():
                if k in ("name", "email", "company", "notes", "avatar_url", "assigned_to", "auto_mode"):
                    fields.append(f"{k}=?")
                    vals.append(v)
            if name:
                fields.append("name=?")
                vals.append(name)
            fields.append("updated_at=?")
            vals.append(now)
            vals.append(phone)
            conn.execute(f"UPDATE contacts SET {','.join(fields)} WHERE phone=?", vals)
        else:
            conn.execute(
                "INSERT INTO contacts (phone, name, email, company, notes, avatar_url, assigned_to, auto_mode, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (phone, name, extra.get("email", ""), extra.get("company", ""),
                 extra.get("notes", ""), extra.get("avatar_url", ""),
                 extra.get("assigned_to", ""), extra.get("auto_mode", 1), now, now),
            )
        conn.commit()
        row = conn.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        conn.close()
        return dict(row)


def get_contact(phone: str) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM contacts WHERE phone=?", (phone,)).fetchone()
        conn.close()
        return dict(row) if row else None


def get_contact_by_id(contact_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


def list_contacts(limit: int = 100, offset: int = 0) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM contacts ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def update_contact(contact_id: int, **fields: Any) -> None:
    with _lock:
        conn = _get_conn()
        allowed = ("name", "email", "company", "notes", "avatar_url", "assigned_to", "auto_mode")
        sets = []
        vals = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            sets.append("updated_at=?")
            vals.append(datetime.utcnow().isoformat())
            vals.append(contact_id)
            conn.execute(f"UPDATE contacts SET {','.join(sets)} WHERE id=?", vals)
            conn.commit()
        conn.close()


def delete_contact(contact_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM contact_tags WHERE contact_id=?", (contact_id,))
        conn.execute("DELETE FROM tasks WHERE contact_id=?", (contact_id,))
        conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))
        conn.commit()
        conn.close()


# ── Conversations ─────────────────────────────────────────────────────────────

def get_or_create_conversation(phone: str) -> dict:
    """Retorna conversa aberta (new|open|waiting) ou cria uma nova."""
    with _lock:
        conn = _get_conn()
        contact = conn.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()
        if not contact:
            now = datetime.utcnow().isoformat()
            conn.execute("INSERT INTO contacts (phone, created_at, updated_at) VALUES (?,?,?)", (phone, now, now))
            conn.commit()
            contact = conn.execute("SELECT id FROM contacts WHERE phone=?", (phone,)).fetchone()
        contact_id = contact["id"]

        conv = conn.execute(
            "SELECT * FROM conversations WHERE contact_id=? AND status IN ('new','open','waiting') ORDER BY id DESC LIMIT 1",
            (contact_id,),
        ).fetchone()
        if conv:
            conn.close()
            return dict(conv)

        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO conversations (contact_id, status, last_message_at, created_at, updated_at) VALUES (?,?,?,?,?)",
            (contact_id, "new", now, now, now),
        )
        conn.commit()
        conv = conn.execute(
            "SELECT * FROM conversations WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)
        ).fetchone()
        conn.close()
        return dict(conv)


def get_conversation(conv_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


def list_conversations(status: str = "", archived: bool | None = None,
                       limit: int = 100, offset: int = 0) -> list[dict]:
    """Lista conversas. Por padrão esconde arquivadas; `archived=True` mostra só arquivadas.

    Fixadas (pinned=1) sobem para o topo mantendo a ordenação por última mensagem.
    """
    if status == "archived":
        status = ""
        archived = True

    with _lock:
        conn = _get_conn()
        where = []
        params: list[Any] = []
        if status:
            where.append("c.status=?")
            params.append(status)
        if archived is True:
            where.append("c.archived=1")
        else:
            where.append("c.archived=0")
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        rows = conn.execute(
            "SELECT c.*, ct.name as contact_name, ct.phone as contact_phone "
            "FROM conversations c LEFT JOIN contacts ct ON c.contact_id=ct.id "
            f"{where_sql} ORDER BY c.pinned DESC, c.last_message_at DESC, c.id DESC "
            "LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()

        # Agrega as tags de cada conversa para exibição direta na lista
        conv_ids = [r["id"] for r in rows]
        tags_by_conv: dict[int, list[dict]] = {}
        if conv_ids:
            placeholders = ",".join("?" * len(conv_ids))
            tag_rows = conn.execute(
                f"SELECT ct.conversation_id AS conv_id, t.id, t.name, t.color "
                f"FROM conversation_tags ct JOIN tags t ON t.id=ct.tag_id "
                f"WHERE ct.conversation_id IN ({placeholders}) ORDER BY t.name",
                conv_ids,
            ).fetchall()
            for tr in tag_rows:
                tags_by_conv.setdefault(tr["conv_id"], []).append(
                    {"id": tr["id"], "name": tr["name"], "color": tr["color"]}
                )
            conn.close()
        else:
            conn.close()

        result = []
        for r in rows:
            item = dict(r)
            item["tags"] = tags_by_conv.get(item["id"], [])
            result.append(item)
        return result


def update_conversation(conv_id: int, **fields: Any) -> None:
    with _lock:
        conn = _get_conn()
        allowed = ("status", "assigned_to", "priority", "unread_count", "last_message_at",
                   "archived", "muted", "pinned", "favorite")
        sets = []
        vals = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            sets.append("updated_at=?")
            vals.append(datetime.utcnow().isoformat())
            vals.append(conv_id)
            conn.execute(f"UPDATE conversations SET {','.join(sets)} WHERE id=?", vals)
            conn.commit()
        conn.close()


def increment_unread(conv_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE conversations SET unread_count=unread_count+1, updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), conv_id),
        )
        conn.commit()
        conn.close()


def reset_unread(conv_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE conversations SET unread_count=0, updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), conv_id),
        )
        conn.commit()
        conn.close()


def mark_unread(conv_id: int) -> None:
    """Marca como não lida (garante unread_count >= 1)."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE conversations SET unread_count=MAX(unread_count,1), updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), conv_id),
        )
        conn.commit()
        conn.close()


def clear_conversation(conv_id: int) -> None:
    """Remove todas as mensagens da conversa (mantém o chat/contato)."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute(
            "UPDATE conversations SET unread_count=0, last_message_at=NULL, updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), conv_id),
        )
        conn.commit()
        conn.close()


def delete_conversation(conv_id: int) -> None:
    """Remove mensagens, vínculo de tags e a própria conversa."""
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversation_tags WHERE conversation_id=?", (conv_id,))
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
        conn.commit()
        conn.close()


# ── Messages ──────────────────────────────────────────────────────────────────

def save_message(
    conversation_id: int,
    phone: str,
    direction: str,
    body: str,
    sender: str = "",
    msg_type: str = "text",
    media_url: str = "",
    uazapi_id: str = "",
    status: str = "sent",
) -> dict | None:
    """Salva mensagem com idempotência via uazapi_id. Retorna a mensagem ou None se duplicata."""
    # uazapi_id vazio deve virar NULL para não violar a constraint UNIQUE.
    if not uazapi_id:
        uazapi_id = None
    with _lock:
        conn = _get_conn()
        if uazapi_id:
            existing = conn.execute("SELECT id FROM messages WHERE uazapi_id=?", (uazapi_id,)).fetchone()
            if existing:
                conn.close()
                return None

        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO messages (conversation_id, phone, uazapi_id, direction, sender, body, msg_type, media_url, status, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (conversation_id, phone, uazapi_id, direction, sender, body, msg_type, media_url, status, now),
        )
        conn.commit()
        msg = conn.execute("SELECT * FROM messages WHERE id=last_insert_rowid()").fetchone()
        conn.close()
        return dict(msg) if msg else None


def get_messages(conversation_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
            (conversation_id, limit, offset),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def count_messages(conversation_id: int) -> int:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM messages WHERE conversation_id=?", (conversation_id,)).fetchone()
        conn.close()
        return row["cnt"] if row else 0


def search_messages(query: str, limit: int = 50) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT m.*, c.id as conv_id, ct.name as contact_name "
            "FROM messages m "
            "JOIN conversations c ON m.conversation_id=c.id "
            "LEFT JOIN contacts ct ON c.contact_id=ct.id "
            "WHERE m.body LIKE ? ORDER BY m.created_at DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ── Tags ──────────────────────────────────────────────────────────────────────

def create_tag(name: str, color: str = "#6366f1") -> dict:
    with _lock:
        conn = _get_conn()
        conn.execute("INSERT INTO tags (name, color) VALUES (?,?)", (name, color))
        conn.commit()
        tag = conn.execute("SELECT * FROM tags WHERE name=?", (name,)).fetchone()
        conn.close()
        return dict(tag)


def list_tags() -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM tags ORDER BY name").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_tag(tag_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM tags WHERE id=?", (tag_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


def update_tag(tag_id: int, name: str = "", color: str = "") -> None:
    with _lock:
        conn = _get_conn()
        sets, vals = [], []
        if name:
            sets.append("name=?")
            vals.append(name)
        if color:
            sets.append("color=?")
            vals.append(color)
        if sets:
            vals.append(tag_id)
            conn.execute(f"UPDATE tags SET {','.join(sets)} WHERE id=?", vals)
            conn.commit()
        conn.close()


def delete_tag(tag_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM contact_tags WHERE tag_id=?", (tag_id,))
        conn.execute("DELETE FROM conversation_tags WHERE tag_id=?", (tag_id,))
        conn.execute("DELETE FROM tags WHERE id=?", (tag_id,))
        conn.commit()
        conn.close()


def add_tag_to_contact(contact_id: int, tag_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO contact_tags (contact_id, tag_id) VALUES (?,?)",
            (contact_id, tag_id),
        )
        conn.commit()
        conn.close()


def remove_tag_from_contact(contact_id: int, tag_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM contact_tags WHERE contact_id=? AND tag_id=?", (contact_id, tag_id))
        conn.commit()
        conn.close()


def get_contact_tags(contact_id: int) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT t.* FROM tags t JOIN contact_tags ct ON t.id=ct.tag_id WHERE ct.contact_id=?",
            (contact_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


def add_tag_to_conversation(conversation_id: int, tag_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO conversation_tags (conversation_id, tag_id) VALUES (?,?)",
            (conversation_id, tag_id),
        )
        conn.commit()
        conn.close()


def get_conversation_tags(conversation_id: int) -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT t.* FROM tags t JOIN conversation_tags ct ON t.id=ct.tag_id WHERE ct.conversation_id=?",
            (conversation_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ── Templates ─────────────────────────────────────────────────────────────────

def create_template(title: str, body: str) -> dict:
    with _lock:
        conn = _get_conn()
        now = datetime.utcnow().isoformat()
        conn.execute("INSERT INTO templates (title, body, created_at) VALUES (?,?,?)", (title, body, now))
        conn.commit()
        tpl = conn.execute("SELECT * FROM templates WHERE id=last_insert_rowid()").fetchone()
        conn.close()
        return dict(tpl)


def list_templates() -> list[dict]:
    with _lock:
        conn = _get_conn()
        rows = conn.execute("SELECT * FROM templates ORDER BY title").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_template(tpl_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM templates WHERE id=?", (tpl_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


def update_template(tpl_id: int, title: str = "", body: str = "") -> None:
    with _lock:
        conn = _get_conn()
        sets, vals = [], []
        if title:
            sets.append("title=?")
            vals.append(title)
        if body:
            sets.append("body=?")
            vals.append(body)
        if sets:
            vals.append(tpl_id)
            conn.execute(f"UPDATE templates SET {','.join(sets)} WHERE id=?", vals)
            conn.commit()
        conn.close()


def delete_template(tpl_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM templates WHERE id=?", (tpl_id,))
        conn.commit()
        conn.close()


# ── Tasks ─────────────────────────────────────────────────────────────────────

def create_task(contact_id: int, title: str, description: str = "", due_date: str = "") -> dict:
    with _lock:
        conn = _get_conn()
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT INTO tasks (contact_id, title, description, due_date, created_at) VALUES (?,?,?,?,?)",
            (contact_id, title, description, due_date, now),
        )
        conn.commit()
        task = conn.execute("SELECT * FROM tasks WHERE title=? AND contact_id=?", (title, contact_id)).fetchone()
        conn.close()
        return dict(task)


def list_tasks(contact_id: int | None = None) -> list[dict]:
    with _lock:
        conn = _get_conn()
        if contact_id:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE contact_id=? ORDER BY created_at DESC", (contact_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        conn.close()
        return [dict(r) for r in rows]


def get_task(task_id: int) -> dict | None:
    with _lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        conn.close()
        return dict(row) if row else None


def update_task(task_id: int, **fields: Any) -> None:
    with _lock:
        conn = _get_conn()
        allowed = ("title", "description", "status", "due_date")
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.append(task_id)
            conn.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", vals)
            conn.commit()
        conn.close()


def delete_task(task_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        conn.close()


# ── Dashboard / Stats ─────────────────────────────────────────────────────────

def get_dashboard_stats() -> dict:
    with _lock:
        conn = _get_conn()
        total_contacts = conn.execute("SELECT COUNT(*) as cnt FROM contacts").fetchone()["cnt"]
        total_conversations = conn.execute("SELECT COUNT(*) as cnt FROM conversations").fetchone()["cnt"]
        open_conversations = conn.execute(
            "SELECT COUNT(*) as cnt FROM conversations WHERE status IN ('new','open','waiting')"
        ).fetchone()["cnt"]
        total_messages = conn.execute("SELECT COUNT(*) as cnt FROM messages").fetchone()["cnt"]
        unread_messages = conn.execute(
            "SELECT COALESCE(SUM(unread_count),0) as cnt FROM conversations"
        ).fetchone()["cnt"]
        today = datetime.utcnow().strftime("%Y-%m-%d")
        messages_today = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE created_at >= ?", (today,)
        ).fetchone()["cnt"]
        conn.close()
        return {
            "total_contacts": total_contacts,
            "total_conversations": total_conversations,
            "open_conversations": open_conversations,
            "total_messages": total_messages,
            "unread_messages": unread_messages,
            "messages_today": messages_today,
        }
