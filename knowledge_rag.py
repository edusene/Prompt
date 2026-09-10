"""
knowledge_rag.py — Knowledge base via RAG no Supabase (pgvector + Google embeddings).
Chunking, embedding e busca semântica para injetar contexto no LLM.
"""

import os
import math
from google import genai
from google.genai import types
import database

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-004")

_client = genai.Client(api_key=GEMINI_API_KEY)

# ── Embeddings ───────────────────────────────────────────────────────────────


def embed_text(text: str) -> list[float]:
    """Gera embedding via Google text-embedding-004."""
    result = _client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return result.embeddings[0].values


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Gera embeddings em lote (mais eficiente para indexação)."""
    result = _client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return [e.values for e in result.embeddings]


# ── Chunking ─────────────────────────────────────────────────────────────────


def chunk_text(text: str, max_len: int = 500) -> list[str]:
    """Divide texto em chunks para embedding.
    Prioridade: paragrafo duplo (\\n\\n) > sentenca (.\\s) > corte bruto por max_len.
    """
    text = text.strip()
    if not text:
        return []

    # 1) Quebra por parágrafo duplo
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    chunks = []
    for para in paragraphs:
        if len(para) <= max_len:
            chunks.append(para)
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
                    chunks.append(buffer)
                    buffer = sent
                else:
                    buffer = f"{buffer} {sent}".strip() if buffer else sent
            if buffer:
                chunks.append(buffer)

    # 3) Remove chunks muito curtos (< 10 chars) — provavelmente lixo
    chunks = [c for c in chunks if len(c) >= 10]

    return chunks if chunks else [text[:max_len]]


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
    Retorna texto concatenado pronto para injetar no system prompt.
    Se embedding falhar, retorna os chunks mais recentes como fallback.
    """
    if not query or not query.strip():
        return ""

    # Tenta busca vetorial
    try:
        query_embedding = embed_text(query)
        results = database.search_knowledge(query_embedding, top_k=top_k, query_text=query)
        if results:
            parts = [r["content"] for r in results]
            joined = "\n\n---\n\n".join(parts)
            print(f"[rag] search vectorial query_len={len(query)} results={len(results)} context_len={len(joined)}")
            return joined
    except Exception as e:
        print(f"[rag] WARN busca vetorial falhou: {e}")

    # Fallback: busca textual por palavras-chave no banco
    try:
        results = database.search_knowledge([], top_k=top_k, query_text=query)
        if results:
            parts = [r["content"] for r in results]
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
