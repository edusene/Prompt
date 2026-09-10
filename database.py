"""
database.py — ORM leve para o painel CRM.
Usa db.py como camada de abstração (suporta SQLite e PostgreSQL).
O agente Gemini continua usando memory.py (in-memory) para seu histórico.
"""

import db
from datetime import datetime
from typing import Any


def init_db() -> None:
    """Cria todas as tabelas se não existirem (e aplica migrações idempotentes)."""
    db.executescript(SCHEMA)

    # Migração idempotente: adiciona colunas de gerenciamento de conversa
    cols = db.table_columns("conversations")
    for col, ddl in (
        ("archived", "ALTER TABLE conversations ADD COLUMN archived INTEGER DEFAULT 0"),
        ("muted", "ALTER TABLE conversations ADD COLUMN muted INTEGER DEFAULT 0"),
        ("pinned", "ALTER TABLE conversations ADD COLUMN pinned INTEGER DEFAULT 0"),
        ("favorite", "ALTER TABLE conversations ADD COLUMN favorite INTEGER DEFAULT 0"),
    ):
        if col not in cols:
            db.execute(ddl)

    # Habilita extensão pgvector no PostgreSQL (necessário para busca semântica)
    if db.DB_DRIVER == "postgresql":
        try:
            db.execute("CREATE EXTENSION IF NOT EXISTS vector")
            print("[db] extensão vector habilitada")
        except Exception as e:
            print(f"[db] WARN: vector extension: {e}")

    # Migração idempotente: cria tabela knowledge se não existir
    kcols = db.table_columns("knowledge") if "knowledge" in _get_tables() else set()
    if not kcols:
        db.executescript(KNOWLEDGE_SCHEMA)
        print("[db] tabela knowledge criada")
    elif "embedding" not in kcols and db.DB_DRIVER == "postgresql":
        try:
            db.execute("ALTER TABLE knowledge ADD COLUMN embedding vector(768)")
            print("[db] coluna embedding adicionada")
        except Exception:
            pass

    # Migração idempotente: cria tabela knowledge_files (descrições)
    if "knowledge_files" not in _get_tables():
        db.executescript(KNOWLEDGE_FILES_SCHEMA)
        print("[db] tabela knowledge_files criada")

    _ensure_indexes()


# ═══ Settings (key-value global) ═════════════════════════════════════════════

def get_setting(key: str, default: str = "") -> str:
    row = db.fetch_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    now = datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at",
        (key, value, now),
    )


def _ensure_indexes() -> None:
    """Crea índices faltantes que aceleran las consultas más usadas (idempotente).

    La mayoría ya los crea el SCHEMA (messages.conversation_id/created_at/uazapi_id,
    conversations.contact_id/status). Aquí solo añadimos el orden por pinned de la
    lista de conversas, sin duplicar ni alterar las consultas existentes.
    """
    for name, table, column in (
        ("idx_conversations_pinned", "conversations", "pinned"),
    ):
        try:
            db.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")
        except Exception as e:
            print(f"[db] WARN index {name}: {e}")


def _get_tables() -> set[str]:
    """Retorna nomes das tabelas existentes no banco."""
    if db.DB_DRIVER == "postgresql":
        rows = db.fetch_all(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        )
        return {r["table_name"] for r in rows}
    else:
        rows = db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
        return {r["name"] for r in rows}


KNOWLEDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    chunk_index INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    content TEXT DEFAULT '',
    created_at TEXT DEFAULT NOW(),
    updated_at TEXT DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_filename ON knowledge(filename);
"""

KNOWLEDGE_FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_files (
    filename TEXT PRIMARY KEY,
    description TEXT DEFAULT '',
    created_at TEXT DEFAULT NOW(),
    updated_at TEXT DEFAULT NOW()
);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id SERIAL PRIMARY KEY,
    phone TEXT UNIQUE NOT NULL,
    name TEXT DEFAULT '',
    email TEXT DEFAULT '',
    company TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    assigned_to TEXT DEFAULT '',
    auto_mode INTEGER DEFAULT 1,
    created_at TEXT DEFAULT NOW(),
    updated_at TEXT DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
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
    created_at TEXT DEFAULT NOW(),
    updated_at TEXT DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id),
    phone TEXT NOT NULL,
    uazapi_id TEXT UNIQUE,
    direction TEXT NOT NULL,
    sender TEXT DEFAULT '',
    body TEXT DEFAULT '',
    msg_type TEXT DEFAULT 'text',
    media_url TEXT DEFAULT '',
    status TEXT DEFAULT 'sent',
    created_at TEXT DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tags (
    id SERIAL PRIMARY KEY,
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
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    contact_id INTEGER REFERENCES contacts(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    due_date TEXT,
    created_at TEXT DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    created_at TEXT DEFAULT NOW(),
    updated_at TEXT DEFAULT NOW()
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
    now = datetime.utcnow().isoformat()
    existing = db.fetch_one("SELECT * FROM contacts WHERE phone=?", (phone,))
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
        db.execute(f"UPDATE contacts SET {','.join(fields)} WHERE phone=?", tuple(vals))
    else:
        default_auto = int(get_setting("agent_mode", "1"))
        db.execute(
            "INSERT INTO contacts (phone, name, email, company, notes, avatar_url, assigned_to, auto_mode, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (phone, name, extra.get("email", ""), extra.get("company", ""),
             extra.get("notes", ""), extra.get("avatar_url", ""),
             extra.get("assigned_to", ""), extra.get("auto_mode", default_auto), now, now),
        )
    return db.fetch_one("SELECT * FROM contacts WHERE phone=?", (phone,))


def get_contact(phone: str) -> dict | None:
    return db.fetch_one("SELECT * FROM contacts WHERE phone=?", (phone,))


def get_contact_by_id(contact_id: int) -> dict | None:
    return db.fetch_one("SELECT * FROM contacts WHERE id=?", (contact_id,))


def list_contacts(limit: int = 100, offset: int = 0) -> list[dict]:
    return db.fetch_all(
        "SELECT * FROM contacts ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
    )


def update_contact(contact_id: int, **fields: Any) -> None:
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
        db.execute(f"UPDATE contacts SET {','.join(sets)} WHERE id=?", tuple(vals))


def delete_contact(contact_id: int) -> None:
    convs = db.fetch_all("SELECT id FROM conversations WHERE contact_id=?", (contact_id,))
    for conv in convs:
        db.execute("DELETE FROM conversation_tags WHERE conversation_id=?", (conv["id"],))
        db.execute("DELETE FROM messages WHERE conversation_id=?", (conv["id"],))
    db.execute("DELETE FROM conversations WHERE contact_id=?", (contact_id,))
    db.execute("DELETE FROM contact_tags WHERE contact_id=?", (contact_id,))
    db.execute("DELETE FROM tasks WHERE contact_id=?", (contact_id,))
    db.execute("DELETE FROM contacts WHERE id=?", (contact_id,))


# ── Conversations ─────────────────────────────────────────────────────────────

def get_or_create_conversation(phone: str) -> dict:
    """Retorna conversa aberta (new|open|waiting) ou cria uma nova."""
    contact = db.fetch_one("SELECT id FROM contacts WHERE phone=?", (phone,))
    if not contact:
        now = datetime.utcnow().isoformat()
        db.execute("INSERT INTO contacts (phone, created_at, updated_at) VALUES (?,?,?)", (phone, now, now))
        contact = db.fetch_one("SELECT id FROM contacts WHERE phone=?", (phone,))
    contact_id = contact["id"]

    conv = db.fetch_one(
        "SELECT * FROM conversations WHERE contact_id=? ORDER BY id DESC LIMIT 1",
        (contact_id,),
    )
    if conv:
        return conv

    now = datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO conversations (contact_id, status, last_message_at, created_at, updated_at) VALUES (?,?,?,?,?)",
        (contact_id, "new", now, now, now),
    )
    return db.fetch_one(
        "SELECT * FROM conversations WHERE contact_id=? ORDER BY id DESC LIMIT 1", (contact_id,)
    )


def get_conversation(conv_id: int) -> dict | None:
    return db.fetch_one("SELECT * FROM conversations WHERE id=?", (conv_id,))


def list_conversations(status: str = "", archived: bool | None = None,
                       limit: int = 100, offset: int = 0) -> list[dict]:
    """Lista conversas. Por padrão esconde arquivadas; `archived=True` mostra só arquivadas."""
    if status == "archived":
        status = ""
        archived = True

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
    rows = db.fetch_all(
        "SELECT c.*, ct.name as contact_name, ct.phone as contact_phone, ct.avatar_url as contact_avatar "
        "FROM conversations c LEFT JOIN contacts ct ON c.contact_id=ct.id "
        f"{where_sql} ORDER BY c.pinned DESC, c.last_message_at DESC, c.id DESC "
        "LIMIT ? OFFSET ?",
        (*params, limit, offset),
    )

    conv_ids = [r["id"] for r in rows]
    tags_by_conv: dict[int, list[dict]] = {}
    if conv_ids:
        placeholders = ",".join(["?"] * len(conv_ids))
        tag_rows = db.fetch_all(
            f"SELECT ct.conversation_id AS conv_id, t.id, t.name, t.color "
            f"FROM conversation_tags ct JOIN tags t ON t.id=ct.tag_id "
            f"WHERE ct.conversation_id IN ({placeholders}) ORDER BY t.name",
            conv_ids,
        )
        for tr in tag_rows:
            tags_by_conv.setdefault(tr["conv_id"], []).append(
                {"id": tr["id"], "name": tr["name"], "color": tr["color"]}
            )

    result = []
    for r in rows:
        item = dict(r)
        item["tags"] = tags_by_conv.get(item["id"], [])
        result.append(item)
    return result


def update_conversation(conv_id: int, **fields: Any) -> None:
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
        db.execute(f"UPDATE conversations SET {','.join(sets)} WHERE id=?", tuple(vals))


def increment_unread(conv_id: int) -> None:
    db.execute(
        "UPDATE conversations SET unread_count=unread_count+1, updated_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), conv_id),
    )


def reset_unread(conv_id: int) -> None:
    db.execute(
        "UPDATE conversations SET unread_count=0, updated_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), conv_id),
    )


def mark_unread(conv_id: int) -> None:
    """Marca como não lida (garante unread_count >= 1)."""
    db.execute(
        "UPDATE conversations SET unread_count=MAX(unread_count,1), updated_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), conv_id),
    )


def clear_conversation(conv_id: int) -> None:
    """Remove todas as mensagens da conversa (mantém o chat/contato)."""
    db.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
    db.execute(
        "UPDATE conversations SET unread_count=0, last_message_at=NULL, updated_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), conv_id),
    )


def delete_conversation(conv_id: int) -> None:
    """Remove mensagens, vínculo de tags e a própria conversa."""
    db.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
    db.execute("DELETE FROM conversation_tags WHERE conversation_id=?", (conv_id,))
    db.execute("DELETE FROM conversations WHERE id=?", (conv_id,))


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
    uazapi_id_clean = uazapi_id if uazapi_id else None
    if uazapi_id_clean:
        existing = db.fetch_one("SELECT id FROM messages WHERE uazapi_id=?", (uazapi_id_clean,))
        if existing:
            return None

    now = datetime.utcnow().isoformat()
    msg_id = db.execute_returning(
        "INSERT INTO messages (conversation_id, phone, uazapi_id, direction, sender, body, msg_type, media_url, status, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (conversation_id, phone, uazapi_id_clean, direction, sender, body, msg_type, media_url, status, now),
    )
    print(f"[db] save_message: msg_id={msg_id} uazapi_id_clean={uazapi_id_clean} direction={direction}")
    if msg_id:
        return db.fetch_one("SELECT * FROM messages WHERE id=?", (msg_id,))
    print(f"[db] save_message: FALHA - msg_id is None!")
    return None


def update_message_media(msg_id: int, media_url: str) -> None:
    """Guarda o data-URI base64 da média baixada (image/audio/video/sticker)."""
    try:
        db.execute(
            "UPDATE messages SET media_url=? WHERE id=?",
            (media_url or "", int(msg_id)),
        )
    except Exception as e:
        print(f"[db] update_message_media: {e}")


def get_message(msg_id: int) -> dict | None:
    return db.fetch_one("SELECT * FROM messages WHERE id=?", (msg_id,))


def link_or_save_outgoing(conversation_id: int, phone: str, body: str, uazapi_id: str | None,
                          sender: str = "human", msg_type: str = "text") -> dict | None:
    """Linka ou cria mensagem de saída com proteção contra duplicidade.
    Prioridade:
      (1) uazapi_id exato já existe → retorna (dedup entre formatos API/webhook).
      (2) msg recente (30s) mesma conversa+body → linka o uazapi_id.
      (3) Nada encontrado → insere nova via save_message."""
    print(f"[db] link_or_save: conv={conversation_id} phone={phone} uazapi_id={uazapi_id} body_len={len(body)}")

    if uazapi_id:
        existing_exact = db.fetch_one(
            "SELECT id FROM messages WHERE uazapi_id=?", (uazapi_id,),
        )
        if existing_exact:
            print(f"[db] link_or_save: uazapi_id exato ja existe msg_id={existing_exact['id']}")
            return db.fetch_one("SELECT * FROM messages WHERE id=?", (existing_exact["id"],))

        existing_recent = db.fetch_one(
            "SELECT id FROM messages WHERE conversation_id=? AND body=? AND direction='out' "
            f"AND {db.ago_sql('created_at', 30)}",
            (conversation_id, body),
        )
        if existing_recent:
            print(f"[db] link_or_save: linkando msg id={existing_recent['id']} com uazapi_id={uazapi_id}")
            db.execute("UPDATE messages SET uazapi_id=? WHERE id=?", (uazapi_id, existing_recent["id"]))
            return db.fetch_one("SELECT * FROM messages WHERE id=?", (existing_recent["id"],))

    print(f"[db] link_or_save: nenhuma msg anterior, chamando save_message")
    result = save_message(
        conversation_id=conversation_id, phone=phone, direction="out",
        body=body, sender=sender, msg_type=msg_type, uazapi_id=uazapi_id or "",
    )
    print(f"[db] link_or_save: save_message retornou id={result['id'] if result else None}")
    return result


def get_messages(conversation_id: int, limit: int = 50, offset: int = 0) -> list[dict]:
    return db.fetch_all(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC LIMIT ? OFFSET ?",
        (conversation_id, limit, offset),
    )


def count_messages(conversation_id: int) -> int:
    row = db.fetch_one("SELECT COUNT(*) as cnt FROM messages WHERE conversation_id=?", (conversation_id,))
    return row["cnt"] if row else 0


def search_messages(query: str, limit: int = 50) -> list[dict]:
    return db.fetch_all(
        "SELECT m.*, c.id as conv_id, ct.name as contact_name "
        "FROM messages m "
        "JOIN conversations c ON m.conversation_id=c.id "
        "LEFT JOIN contacts ct ON c.contact_id=ct.id "
        "WHERE m.body LIKE ? ORDER BY m.created_at DESC LIMIT ?",
        (f"%{query}%", limit),
    )


# ── Tags ──────────────────────────────────────────────────────────────────────

def create_tag(name: str, color: str = "#6366f1") -> dict:
    db.execute("INSERT INTO tags (name, color) VALUES (?,?)", (name, color))
    return db.fetch_one("SELECT * FROM tags WHERE name=?", (name,))


def list_tags() -> list[dict]:
    return db.fetch_all("SELECT * FROM tags ORDER BY name")


def get_tag(tag_id: int) -> dict | None:
    return db.fetch_one("SELECT * FROM tags WHERE id=?", (tag_id,))


def update_tag(tag_id: int, name: str = "", color: str = "") -> None:
    sets, vals = [], []
    if name:
        sets.append("name=?")
        vals.append(name)
    if color:
        sets.append("color=?")
        vals.append(color)
    if sets:
        vals.append(tag_id)
        db.execute(f"UPDATE tags SET {','.join(sets)} WHERE id=?", tuple(vals))


def delete_tag(tag_id: int) -> None:
    db.execute("DELETE FROM contact_tags WHERE tag_id=?", (tag_id,))
    db.execute("DELETE FROM conversation_tags WHERE tag_id=?", (tag_id,))
    db.execute("DELETE FROM tags WHERE id=?", (tag_id,))


def add_tag_to_contact(contact_id: int, tag_id: int) -> None:
    db.insert_ignore("contact_tags", ["contact_id", "tag_id"], (contact_id, tag_id))


def remove_tag_from_contact(contact_id: int, tag_id: int) -> None:
    db.execute("DELETE FROM contact_tags WHERE contact_id=? AND tag_id=?", (contact_id, tag_id))


def get_contact_tags(contact_id: int) -> list[dict]:
    return db.fetch_all(
        "SELECT t.* FROM tags t JOIN contact_tags ct ON t.id=ct.tag_id WHERE ct.contact_id=?",
        (contact_id,),
    )


def add_tag_to_conversation(conversation_id: int, tag_id: int) -> None:
    db.insert_ignore("conversation_tags", ["conversation_id", "tag_id"], (conversation_id, tag_id))


def get_conversation_tags(conversation_id: int) -> list[dict]:
    return db.fetch_all(
        "SELECT t.* FROM tags t JOIN conversation_tags ct ON t.id=ct.tag_id WHERE ct.conversation_id=?",
        (conversation_id,),
    )


# ── Templates ─────────────────────────────────────────────────────────────────

def create_template(title: str, body: str) -> dict:
    now = datetime.utcnow().isoformat()
    tpl_id = db.execute_returning("INSERT INTO templates (title, body, created_at) VALUES (?,?,?)", (title, body, now))
    return db.fetch_one("SELECT * FROM templates WHERE id=?", (tpl_id,))


def list_templates() -> list[dict]:
    return db.fetch_all("SELECT * FROM templates ORDER BY title")


def get_template(tpl_id: int) -> dict | None:
    return db.fetch_one("SELECT * FROM templates WHERE id=?", (tpl_id,))


def update_template(tpl_id: int, title: str = "", body: str = "") -> None:
    sets, vals = [], []
    if title:
        sets.append("title=?")
        vals.append(title)
    if body:
        sets.append("body=?")
        vals.append(body)
    if sets:
        vals.append(tpl_id)
        db.execute(f"UPDATE templates SET {','.join(sets)} WHERE id=?", tuple(vals))


def delete_template(tpl_id: int) -> None:
    db.execute("DELETE FROM templates WHERE id=?", (tpl_id,))


# ── Tasks ─────────────────────────────────────────────────────────────────────

def create_task(contact_id: int, title: str, description: str = "", due_date: str = "") -> dict:
    now = datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO tasks (contact_id, title, description, due_date, created_at) VALUES (?,?,?,?,?)",
        (contact_id, title, description, due_date, now),
    )
    return db.fetch_one("SELECT * FROM tasks WHERE title=? AND contact_id=?", (title, contact_id))


def list_tasks(contact_id: int | None = None) -> list[dict]:
    if contact_id:
        return db.fetch_all(
            "SELECT * FROM tasks WHERE contact_id=? ORDER BY created_at DESC", (contact_id,)
        )
    return db.fetch_all("SELECT * FROM tasks ORDER BY created_at DESC")


def get_task(task_id: int) -> dict | None:
    return db.fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))


def update_task(task_id: int, **fields: Any) -> None:
    allowed = ("title", "description", "status", "due_date")
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k}=?")
            vals.append(v)
    if sets:
        vals.append(task_id)
        db.execute(f"UPDATE tasks SET {','.join(sets)} WHERE id=?", tuple(vals))


def delete_task(task_id: int) -> None:
    db.execute("DELETE FROM tasks WHERE id=?", (task_id,))


# ── Dashboard / Stats ─────────────────────────────────────────────────────────

def get_dashboard_stats() -> dict:
    total_contacts = db.fetch_one("SELECT COUNT(*) as cnt FROM contacts")["cnt"]
    total_conversations = db.fetch_one("SELECT COUNT(*) as cnt FROM conversations")["cnt"]
    open_conversations = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM conversations WHERE status IN ('new','open','waiting')"
    )["cnt"]
    total_messages = db.fetch_one("SELECT COUNT(*) as cnt FROM messages")["cnt"]
    unread_messages = db.fetch_one(
        "SELECT COALESCE(SUM(unread_count),0) as cnt FROM conversations"
    )["cnt"]
    today = datetime.utcnow().strftime("%Y-%m-%d")
    messages_today = db.fetch_one(
        "SELECT COUNT(*) as cnt FROM messages WHERE created_at >= ?", (today,)
    )["cnt"]
    return {
        "total_contacts": total_contacts,
        "total_conversations": total_conversations,
        "open_conversations": open_conversations,
        "total_messages": total_messages,
        "unread_messages": unread_messages,
        "messages_today": messages_today,
    }


# ── Knowledge (RAG) ──────────────────────────────────────────────────────────

def insert_knowledge_chunks(filename: str, chunks: list[str], embeddings: list) -> None:
    """Insere chunks de knowledge com embeddings."""
    now = datetime.utcnow().isoformat()
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        if db.DB_DRIVER == "postgresql" and emb is not None:
            # Converte lista para string pgvector: '[0.1, 0.2, ...]'
            emb_str = "[" + ",".join(str(x) for x in emb) + "]"
            db.execute(
                "INSERT INTO knowledge (filename, chunk_index, content, embedding, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (filename, i, chunk, emb_str, now, now),
            )
        else:
            db.execute(
                "INSERT INTO knowledge (filename, chunk_index, content, created_at, updated_at) "
                "VALUES (?,?,?, ?,?)",
                (filename, i, chunk, now, now),
            )


def search_knowledge(query_embedding: list[float], top_k: int = 5, query_text: str = "") -> list[dict]:
    """Busca chunks por cosine similarity. Fallback para LIKE se pgvector indisponível."""
    if db.DB_DRIVER != "postgresql":
        # Busca textual por LIKE quando SQLite (sem pgvector)
        if query_text:
            keywords = [w.strip() for w in query_text.split() if len(w.strip()) >= 3]
            if keywords:
                like_conditions = " OR ".join(["content LIKE ?"] * len(keywords))
                like_params = [f"%{kw}%" for kw in keywords]
                rows = db.fetch_all(
                    f"SELECT * FROM knowledge WHERE {like_conditions} ORDER BY filename, chunk_index LIMIT ?",
                    (*like_params, top_k),
                )
                if rows:
                    return rows
        # Último recurso: retorna os primeiros chunks
        return db.fetch_all(
            "SELECT * FROM knowledge ORDER BY filename, chunk_index LIMIT ?", (top_k,)
        )

    emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
    try:
        rows = db.fetch_all(
            "SELECT *, 1 - (embedding <=> %s::vector) AS similarity "
            "FROM knowledge "
            "WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector "
            "LIMIT %s",
            (emb_str, emb_str, top_k),
        )
        if rows:
            return rows
    except Exception as e:
        print(f"[db] WARN: busca vetorial falhou, usando LIKE: {e}")

    # Fallback PostgreSQL: busca textual por LIKE
    if query_text:
        keywords = [w.strip() for w in query_text.split() if len(w.strip()) >= 3]
        if keywords:
            like_conditions = " OR ".join(["content LIKE %s"] * len(keywords))
            like_params = [f"%{kw}%" for kw in keywords]
            try:
                rows = db.fetch_all(
                    f"SELECT * FROM knowledge WHERE {like_conditions} ORDER BY filename, chunk_index LIMIT %s",
                    (*like_params, top_k),
                )
                if rows:
                    return rows
            except Exception:
                pass

    # Último recurso
    return db.fetch_all(
        "SELECT * FROM knowledge ORDER BY filename, chunk_index LIMIT %s", (top_k,)
    )


def list_knowledge_files() -> list[dict]:
    """Lista arquivos distintos com contagem de chunks e descrição."""
    rows = db.fetch_all(
        "SELECT k.filename, COUNT(*) as chunks, kf.description, MIN(k.created_at) as created_at "
        "FROM knowledge k LEFT JOIN knowledge_files kf ON k.filename = kf.filename "
        "GROUP BY k.filename, kf.description ORDER BY k.filename"
    )
    return rows


def get_knowledge_chunks(filename: str) -> list[dict]:
    """Retorna todos os chunks de um arquivo."""
    return db.fetch_all(
        "SELECT * FROM knowledge WHERE filename=? ORDER BY chunk_index",
        (filename,),
    )


def delete_knowledge_file(filename: str) -> None:
    """Deleta todos os chunks de um arquivo e sua descrição."""
    db.execute("DELETE FROM knowledge WHERE filename=?", (filename,))
    db.execute("DELETE FROM knowledge_files WHERE filename=?", (filename,))


# ── Knowledge Files (descrições) ──────────────────────────────────────────────

def upsert_knowledge_file(filename: str, description: str = "") -> None:
    """Insere ou atualiza a descrição de um arquivo knowledge."""
    now = datetime.utcnow().isoformat()
    db.execute(
        "INSERT INTO knowledge_files (filename, description, created_at, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(filename) DO UPDATE SET description=EXCLUDED.description, updated_at=EXCLUDED.updated_at",
        (filename, description, now, now),
    )


def get_knowledge_file_description(filename: str) -> str:
    """Retorna a descrição de um arquivo knowledge."""
    row = db.fetch_one("SELECT description FROM knowledge_files WHERE filename=?", (filename,))
    return row["description"] if row else ""


def get_all_knowledge_descriptions() -> list[dict]:
    """Retorna todos os arquivos com suas descrições (para montar o índice)."""
    return db.fetch_all(
        "SELECT filename, description FROM knowledge_files ORDER BY filename"
    )


def delete_knowledge_file_meta(filename: str) -> None:
    """Deleta apenas os metadados (descrição) de um arquivo."""
    db.execute("DELETE FROM knowledge_files WHERE filename=?", (filename,))
