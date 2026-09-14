"""
knowledge_rag.py — Knowledge base via RAG no Supabase (pgvector + OpenAI embeddings).
Chunking, embedding e busca semântica para injetar contexto no LLM.
"""

import os
import math
from openai import OpenAI
import database

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

_client = OpenAI(api_key=OPENAI_API_KEY)

# ── Embeddings ───────────────────────────────────────────────────────────────


def embed_text(text: str) -> list[float]:
    """Gera embedding via OpenAI text-embedding-3-small."""
    result = _client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    return result.data[0].embedding


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Gera embeddings em lote (mais eficiente para indexação)."""
    result = _client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [e.embedding for e in result.data]


# ── Chunking ─────────────────────────────────────────────────────────────────


def chunk_text(text: str, max_len: int = 500, overlap: int = 50) -> list[str]:
    """Divide texto em chunks para embedding com sobreposição entre chunks.
    Prioridade: paragrafo duplo (\\n\\n) > sentenca (.\\s) > corte bruto por max_len.
    """
    text = text.strip()
    if not text:
        return []

    # 1) Quebra por parágrafo duplo
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    raw_chunks = []
    for para in paragraphs:
        if len(para) <= max_len:
            raw_chunks.append(para)
        else:
            # 2) Quebra por sentença dentro do parágrafo grande
            sentences = []
            current = ""
            for char in para:
                current += char
                if char in (".", "!", "?") and len(current.strip()) > 0:
                    sentences.append(current.strip())
                    current = ""
            if current.strip():
                sentences.append(current.strip())

            # Agrupa sentenças respeitando max_len
            buffer = ""
            for sent in sentences:
                if buffer and len(buffer) + len(sent) + 1 > max_len:
                    raw_chunks.append(buffer)
                    buffer = sent
                else:
                    buffer = f"{buffer} {sent}".strip() if buffer else sent
            if buffer:
                raw_chunks.append(buffer)

    # 3) Remove chunks muito curtos (< 10 chars) — provavelmente lixo
    raw_chunks = [c for c in raw_chunks if len(c) >= 10]

    if not raw_chunks:
        return [text[:max_len]]

    # 4) Adiciona sobreposição entre chunks para preservar contexto nas fronteiras
    if overlap > 0 and len(raw_chunks) > 1:
        chunks = [raw_chunks[0]]
        for i in range(1, len(raw_chunks)):
            prev_tail = raw_chunks[i - 1][-overlap:]
            chunks.append(prev_tail + " " + raw_chunks[i])
        return chunks

    return raw_chunks


# ── Knowledge CRUD ───────────────────────────────────────────────────────────


def save_knowledge(filename: str, content: str, description: str = "") -> None:
    """Salva knowledge no banco: deleta antigo, chunka, embeda, insere."""
    content = (content or "").strip()
    if not content:
        return

    # Deleta chunks anteriores deste arquivo
    database.delete_knowledge_file(filename)

    chunks = chunk_text(content)
    if not chunks:
        return

    # Gera embeddings em lote
    try:
        embeddings = embed_batch(chunks)
    except Exception as e:
        print(f"[rag] ERRO embedding {filename}: {e}")
        # Fallback: salva sem embedding (busca por LIKE ainda funciona)
        embeddings = [None] * len(chunks)

    # Insere no banco
    database.insert_knowledge_chunks(filename, chunks, embeddings)

    # Salva descrição do arquivo
    if description:
        database.upsert_knowledge_file(filename, description)

    print(f"[rag] salvo {filename}: {len(chunks)} chunks")


def search_context(query: str, top_k: int = 3) -> str:
    """Busca os chunks mais relevantes para a query do usuario.
    Retorna texto concatenado com labels de arquivo para injetar no system prompt.
    Se embedding falhar, retorna os chunks mais recentes como fallback.
    """
    if not query or not query.strip():
        return ""

    # Tenta busca vetorial
    try:
        query_embedding = embed_text(query)
        results = database.search_knowledge(query_embedding, top_k=top_k, query_text=query)
        if results:
            parts = [f"[{r['filename']}] {r['content']}" for r in results]
            joined = "\n\n---\n\n".join(parts)
            print(f"[rag] search vectorial query_len={len(query)} results={len(results)} context_len={len(joined)}")
            return joined
    except Exception as e:
        print(f"[rag] WARN busca vetorial falhou: {e}")

    # Fallback: busca textual por palavras-chave no banco
    try:
        results = database.search_knowledge([], top_k=top_k, query_text=query)
        if results:
            parts = [f"[{r['filename']}] {r['content']}" for r in results]
            joined = "\n\n---\n\n".join(parts)
            print(f"[rag] search fallback query_len={len(query)} results={len(results)} context_len={len(joined)}")
            return joined
    except Exception as e:
        print(f"[rag] WARN fallback falhou: {e}")

    return ""


def list_files() -> list[dict]:
    """Lista arquivos de knowledge no banco (filename + chunks + descrição)."""
    return database.list_knowledge_files()


def get_file(filename: str) -> dict | None:
    """Retorna conteúdo completo de um arquivo (concatenado dos chunks) + descrição."""
    chunks = database.get_knowledge_chunks(filename)
    if not chunks:
        return None
    chunks.sort(key=lambda c: c["chunk_index"])
    content = "\n\n".join(c["content"] for c in chunks)
    description = database.get_knowledge_file_description(filename)
    return {"filename": filename, "content": content, "description": description}


def delete_file(filename: str) -> None:
    """Deleta todos os chunks de um arquivo e sua descrição."""
    database.delete_knowledge_file(filename)
    print(f"[rag] deletado {filename}")


# ── Smart Knowledge (tool call support) ──────────────────────────────────────

def get_file_descriptions() -> list[dict]:
    """Retorna índice compacto de arquivos + descrições para injetar no system prompt."""
    return database.get_all_knowledge_descriptions()


def get_file_content(filename: str) -> str:
    """Retorna o conteúdo completo de um arquivo específico (chamado pela tool)."""
    chunks = database.get_knowledge_chunks(filename)
    if not chunks:
        return ""
    chunks.sort(key=lambda c: c["chunk_index"])
    return "\n\n".join(c["content"] for c in chunks)


# ── Knowledge Batch Operations ────────────────────────────────────────────────


def save_knowledge_raw(filename: str, content: str, description: str = "") -> None:
    """Salva knowledge SEM embeddings (status=pending). Para upload em lote."""
    content = (content or "").strip()
    if not content:
        return

    database.delete_knowledge_file(filename)

    chunks = chunk_text(content)
    if not chunks:
        return

    database.insert_knowledge_chunks(filename, chunks, [None] * len(chunks))
    database.upsert_knowledge_file_with_status(filename, description, status="pending")
    print(f"[rag] salvo (raw) {filename}: {len(chunks)} chunks (pendente de embedding)")


def embed_pending_files() -> dict:
    """Processa todos os arquivos com status='pending': gera embeddings e atualiza status.
    Retorna {processed: int, errors: int, files: list[str]}.
    """
    pending = database.get_pending_knowledge_files()
    if not pending:
        return {"processed": 0, "errors": 0, "files": []}

    processed = 0
    errors = 0
    processed_files = []

    for file_info in pending:
        filename = file_info["filename"]
        try:
            chunks = database.get_knowledge_chunks(filename)
            if not chunks:
                database.update_knowledge_file_status(filename, "error")
                errors += 1
                continue

            chunks.sort(key=lambda c: c["chunk_index"])
            texts = [c["content"] for c in chunks]

            try:
                embeddings = embed_batch(texts)
            except Exception as e:
                print(f"[rag] ERRO embedding {filename}: {e}")
                database.update_knowledge_file_status(filename, "error")
                errors += 1
                continue

            database.delete_knowledge_chunks_by_filename(filename)
            database.insert_knowledge_chunks(filename, texts, embeddings)
            database.update_knowledge_file_status(filename, "indexed")
            processed += 1
            processed_files.append(filename)
            print(f"[rag] embedado {filename}: {len(chunks)} chunks")

        except Exception as e:
            print(f"[rag] ERRO processando {filename}: {e}")
            database.update_knowledge_file_status(filename, "error")
            errors += 1

    return {"processed": processed, "errors": errors, "files": processed_files}


def clear_all_embeddings() -> int:
    """Remove todos os embeddings, mantém conteúdo e marca como pending."""
    count = database.clear_knowledge_embeddings()
    database.set_all_knowledge_pending()
    print(f"[rag] embeddings limpos: {count} chunks marcados como pending")
    return count


def delete_all() -> int:
    """Deleta todo o knowledge (chunks + metadados)."""
    count = database.delete_all_knowledge()
    print(f"[rag] todo knowledge deletado: {count} arquivos")
    return count


def list_files_with_status() -> list[dict]:
    """Lista arquivos com status (pending/indexed/error)."""
    return database.list_knowledge_files_with_status()
