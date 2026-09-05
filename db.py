"""
db.py — Camada de abstração de banco de dados.
Suporta SQLite (atual) e PostgreSQL (futuro).
O database.py chama essas funções em vez de usar sqlite3 diretamente.
"""

import os
import threading
from typing import Any

DB_DRIVER = os.getenv("DB_DRIVER", "sqlite")
_lock = threading.Lock()

# ── Conexão ──────────────────────────────────────────────────────────────────

def _get_conn():
    """Retorna uma conexão de banco. Detecta o driver automaticamente."""
    if DB_DRIVER == "postgresql":
        import psycopg2
        import psycopg2.extras
        url = os.getenv("DATABASE_URL", "")
        conn = psycopg2.connect(url)
        conn.autocommit = False
        return conn
    else:
        import sqlite3
        db_path = os.getenv("DB_PATH", "whatsapp.db")
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _cursor(conn):
    """Retorna um cursor adequado ao driver."""
    if DB_DRIVER == "postgresql":
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    else:
        return conn.cursor()


def _to_dict(row) -> dict | None:
    """Converte um resultado de query para dict."""
    if row is None:
        return None
    if DB_DRIVER == "postgresql":
        return dict(row)
    else:
        return dict(row)


def _to_dicts(rows) -> list[dict]:
    """Converte uma lista de resultados para lista de dicts."""
    return [_to_dict(r) for r in rows]


# ── Query helpers ────────────────────────────────────────────────────────────

def _adapt_query(query: str) -> str:
    """Adapta placeholders de SQLite (?) para PostgreSQL (%s) se necessário."""
    if DB_DRIVER == "postgresql":
        return query.replace("?", "%s")
    return query


def execute(query: str, params: tuple | list | None = None, *, commit: bool = True) -> Any:
    """Executa uma query e opcionalmente faz commit. Retorna o cursor."""
    with _lock:
        conn = _get_conn()
        cur = _cursor(conn)
        adapted = _adapt_query(query)
        if params:
            cur.execute(adapted, params)
        else:
            cur.execute(adapted)
        if commit:
            conn.commit()
        return cur


def fetch_one(query: str, params: tuple | list | None = None) -> dict | None:
    """Executa uma query e retorna uma linha como dict (ou None)."""
    with _lock:
        conn = _get_conn()
        cur = _cursor(conn)
        adapted = _adapt_query(query)
        if params:
            cur.execute(adapted, params)
        else:
            cur.execute(adapted)
        row = cur.fetchone()
        conn.close()
        return _to_dict(row)


def fetch_all(query: str, params: tuple | list | None = None) -> list[dict]:
    """Executa uma query e retorna todas as linhas como lista de dicts."""
    with _lock:
        conn = _get_conn()
        cur = _cursor(conn)
        adapted = _adapt_query(query)
        if params:
            cur.execute(adapted, params)
        else:
            cur.execute(adapted)
        rows = cur.fetchall()
        conn.close()
        return _to_dicts(rows)


def executescript(sql: str) -> None:
    """Executa um script SQL (múltiplos statements). Usado no init_db."""
    with _lock:
        conn = _get_conn()
        if DB_DRIVER == "postgresql":
            cur = _cursor(conn)
            cur.execute(sql)
            conn.commit()
        else:
            conn.executescript(sql)
        conn.close()


def last_insert_id(cur) -> int | None:
    """Retorna o ID da última inserção, independente do driver."""
    if DB_DRIVER == "postgresql":
        row = cur.fetchone()
        return row["id"] if row else None
    else:
        return cur.lastrowid


def insert_ignore(table: str, columns: list[str], values: tuple) -> None:
    """Insere uma linha ignorando duplicatas (INSERT OR IGNORE / ON CONFLICT DO NOTHING)."""
    placeholders = ",".join(["?"] * len(columns))
    cols = ",".join(columns)
    if DB_DRIVER == "postgresql":
        ph = ",".join(["%s"] * len(columns))
        query = f"INSERT INTO {table} ({cols}) VALUES ({ph}) ON CONFLICT DO NOTHING"
    else:
        query = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"
    execute(query, values)


def table_columns(table_name: str) -> set[str]:
    """Retorna os nomes das colunas de uma tabela."""
    if DB_DRIVER == "postgresql":
        rows = fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s", (table_name,)
        )
        return {r["column_name"] for r in rows}
    else:
        rows = fetch_all(f"PRAGMA table_info({table_name})")
        return {r["name"] for r in rows}
