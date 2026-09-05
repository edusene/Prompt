# Guia de Migração de Banco de Dados

Este arquivo instrui uma IA sobre como migrar o banco de dados de SQLite para PostgreSQL (Supabase, Railway, Render, etc.).

---

## 1. Visão Geral da Arquitetura

```
database.py  →  Chama funções de  →  db.py  →  Detecta DB_DRIVER  →  Conecta no banco
(app usa)                            (abstração)                     (sqlite ou postgresql)
```

- **`database.py`**: Toda a lógica de negócio (CRUD de contacts, conversations, messages, tags, etc.)
- **`db.py`**: Camada de abstração que traduz queries entre SQLite e PostgreSQL
- **`app.py`**: Usa apenas `database.*` — **não precisa mudar**
- **`memory.py`**: Histórico do agente Gemini (in-memory) — **não envolve banco**

---

## 2. Antes de Migrar

### 2.1 Provisionar o banco PostgreSQL

Opções recomendadas (todas com plano gratuito):

| Serviço | URL | Limite Free |
|---|---|---|
| **Supabase** | https://supabase.com | 500MB, 50k rows |
| **Railway** | https://railway.app | $5 crédito/mês |
| **Render** | https://render.com | 90 dias free |
| **Neon** | https://neon.tech | 512MB |

Obtenha a `DATABASE_URL` no formato:
```
postgresql://usuario:senha@host:5432/nome_do_banco
```

### 2.2 Instalar dependência

```bash
pip install psycopg2-binary
```

Ou descomente em `requirements.txt`:
```
psycopg2-binary>=2.9.0
```

### 2.3 Variáveis de ambiente (.env)

```env
DB_DRIVER=postgresql
DATABASE_URL=postgresql://usuario:senha@host:5432/nome_do_banco
```

> **IMPORTANTE**: Remova ou comente `DB_PATH` (não é usado por PostgreSQL).

---

## 3. Schema do Banco (tabelas)

O schema está definido em `database.py` na constante `SCHEMA` (linhas ~28-114).
O `db.py` executa esse schema automaticamente via `init_db()`.

### Tabelas

| Tabela | Propósito |
|---|---|
| `contacts` | Contatos do WhatsApp |
| `conversations` | Conversas (1:1 com contacts) |
| `messages` | Mensagens trocadas |
| `tags` | Etiquetas para classificar |
| `contact_tags` | Vínculo contato ↔ tag |
| `conversation_tags` | Vínculo conversa ↔ tag |
| `templates` | Mensagens rápidas |
| `tasks` | Tarefas vinculadas a contatos |

### Índices

```sql
idx_messages_conversation ON messages(conversation_id)
idx_messages_created ON messages(created_at)
idx_messages_uazapi ON messages(uazapi_id)
idx_conversations_contact ON conversations(contact_id)
idx_conversations_status ON conversations(status)
idx_tasks_contact ON tasks(contact_id)
```

---

## 4. Passo a Passo da Migração

### Passo 1: Criar banco no serviço escolhido

Exemplo Supabase:
1. Acesse https://app.supabase.com
2. Crie um novo projeto
3. Copie a `DATABASE_URL` em Settings → Database

### Passo 2: Atualizar .env

```env
DB_DRIVER=postgresql
DATABASE_URL=postgresql://postgres.sua-project:senha@aws-0-us-east-1.pooler.supabase.com:6543/postgres
```

### Passo 3: Instalar psycopg2

```bash
pip install psycopg2-binary
```

### Passo 4: Criar as tabelas

Execute uma vez para criar o schema:

```python
python -c "import database; database.init_db()"
```

Ou se houver erro de schema, execute o SQL manualmente no painel do banco (SQL Editor no Supabase).

### Passo 5: Migrar dados existentes (SQLite → PostgreSQL)

Se quiser manter os dados atuais do SQLite, execute este script:

```python
"""migrate_sqlite_to_postgres.py — Migra dados do SQLite para PostgreSQL."""
import sqlite3
import os
import db

# Conexão com SQLite antigo
sqlite_conn = sqlite3.connect("whatsapp.db")
sqlite_conn.row_factory = sqlite3.Row

# Tabelas para migrar (na ordem correta devido a foreign keys)
TABLES = ["contacts", "conversations", "messages", "tags", "contact_tags", "conversation_tags", "templates", "tasks"]

for table in TABLES:
    rows = sqlite_conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"[{table}] 0 linhas — pulando")
        continue

    # Pega os nomes das colunas
    cols = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(cols))
    cols_str = ",".join(cols)

    count = 0
    for row in rows:
        values = tuple(row[c] for c in cols)
        try:
            db.execute(
                f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                values,
            )
            count += 1
        except Exception as e:
            print(f"[{table}] ERRO: {e}")

    print(f"[{table}] {count}/{len(rows)} linhas migradas")

sqlite_conn.close()
print("\nMigração concluída!")
```

Rode:
```bash
python migrate_sqlite_to_postgres.py
```

### Passo 6: Testar

```bash
python app.py
```

Verifique:
1. Dashboard carrega corretamente
2. Conversas aparecem
3. Mensagens são exibidas
4. Tags e templates funcionam

---

## 5. O que o db.py faz (referência técnica)

O `db.py` é a camada de abstração. Ele:

| Função | O que faz | SQLite | PostgreSQL |
|---|---|---|---|
| `_get_conn()` | Cria conexão | `sqlite3.connect()` + PRAGMAs | `psycopg2.connect(url)` |
| `_adapt_query(query)` | Troca placeholders | Mantém `?` | Converte para `%s` |
| `execute(query, params)` | Executa query | `conn.execute()` | `cur.execute()` |
| `fetch_one(query, params)` | Retorna 1 linha | `dict(row)` | `dict(row)` |
| `fetch_all(query, params)` | Retorna N linhas | `[dict(r) for r]` | `[dict(r) for r]` |
| `insert_ignore(table, cols, vals)` | Insere sem erro em duplicata | `INSERT OR IGNORE` | `ON CONFLICT DO NOTHING` |
| `last_insert_id(cursor)` | Pega ID da inserção | `cursor.lastrowid` | `cursor.fetchone()["id"]` |
| `table_columns(table)` | Lista colunas da tabela | `PRAGMA table_info()` | `information_schema.columns` |

---

## 6. Checklist de Validação

Após a migração, verifique:

- [ ] `python -m py_compile db.py database.py app.py` — compila sem erros
- [ ] Dashboard mostra estatísticas corretas
- [ ] Lista de conversas carrega
- [ ] Mensagens são exibidas ao abrir conversa
- [ ] Tags podem ser criadas/editadas
- [ ] Templates funcionam
- [ ] Mensagens recebidas via webhook são salvas
- [ ] Respostas do agente são salvas
- [ ] Busca de mensagens funciona
- [ ] Contato pode ser criado/editado
- [ ] Tarefas podem ser criadas

---

## 7. Troubleshooting

### Erro: `relation "contacts" does not exist`
As tabelas não foram criadas. Execute:
```python
python -c "import database; database.init_db()"
```

### Erro: `password authentication failed`
Verifique a `DATABASE_URL` no `.env`. No Supabase, use a senha do **pooler** (não a do dashboard).

### Erro: `SSL required`
Adicione `?sslmode=require` ao final da `DATABASE_URL`:
```
DATABASE_URL=postgresql://...?sslmode=require
```

### Erro: `connection refused`
O banco pode estar pausado (Render free). Verifique no painel do serviço.

### Migração falhou e quer recomeçar
Execute no SQL Editor do banco:
```sql
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
```
Depois rode `python -c "import database; database.init_db()"` novamente.
