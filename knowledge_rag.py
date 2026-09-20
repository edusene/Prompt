"""
knowledge_rag.py — Knowledge base via RAG no Supabase (pgvector + OpenAI embeddings).
Chunking, embedding e busca semântica para injetar contexto no LLM.
"""

import re
import os
import math
from openai import OpenAI
import database

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

_client = OpenAI(api_key=OPENAI_API_KEY)

# Caches de leitura. Toda escrita no knowledge passa por
# invalidate_knowledge_caches(), que limpa os dois.
_DOC_CACHE: dict[str, str] = {}
_CONTEXT_CACHE: dict[str, str] = {}

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


import parse_knowledge as _parse_knowledge

# ── Chunking ─────────────────────────────────────────────────────────────────

# Arquivos que sao INSTRUCOES do agente (prompts), nao dados consultaveis.
# Eles nunca entram no bloco de knowledge injetado no system prompt: o prompt
# operacional ja e carregado por parse_knowledge e os .md sao dos sub-agentes.
PROMPT_FILES = {
    # Todas as versoes do prompt operacional, nao so a vigente: uma versao
    # antiga que tenha sobrado na base tambem e instrucao, e injeta-la junto
    # aos DADOS traria de volta as regras que o dono acabou de substituir.
    *_parse_knowledge.PROMPT_DOCX_VERSOES,
    "triagem.md",
    "cardapio.md",
    "pedido.md",
    "logistica.md",
    "pagamento.md",
    "crise.md",
    "router.md",
}


def is_prompt_file(filename: str) -> bool:
    """True para arquivos de prompt (instrucoes), False para dados."""
    return (filename or "").strip() in PROMPT_FILES


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    if stripped.startswith("===") and stripped.endswith("==="):
        return True
    return False


def chunk_text(text: str, max_len: int = 900, overlap_lines: int = 1) -> list[str]:
    """Divide o texto em chunks SEM cortar linhas no meio.

    Regras:
    - nunca parte uma linha (linhas = registros de tabela, itens de lista);
    - cada chunk carrega o cabecalho de secao vigente, para continuar
      legivel quando recuperado isolado pela busca vetorial;
    - a sobreposicao e feita por LINHAS inteiras, nunca por fatia de caracteres.

    O conteudo salvo aqui e usado apenas para BUSCA. O texto oficial do
    arquivo fica em knowledge_files.content (ver database.set_knowledge_document).
    """
    text = (text or "").strip()
    if not text:
        return []

    lines = text.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    heading = ""

    def flush():
        nonlocal current, current_len
        if not current:
            return
        body = "\n".join(current).strip()
        if body:
            if heading and not body.startswith(heading):
                chunks.append(f"{heading}\n{body}")
            else:
                chunks.append(body)
        current = []
        current_len = 0

    for line in lines:
        if _is_heading(line):
            flush()
            heading = line.strip()
            current = [line]
            current_len = len(line)
            continue

        line_len = len(line) + 1
        if current_len + line_len > max_len and current:
            tail = [ln for ln in current[-overlap_lines:] if ln.strip()] if overlap_lines > 0 else []
            flush()
            current = list(tail)
            current_len = sum(len(ln) + 1 for ln in current)

        current.append(line)
        current_len += line_len

    flush()

    # Descarta chunks sem conteudo util (so cabecalho ou lixo curto)
    result = [c for c in chunks if len(c.strip()) >= 12]
    return result or [text[:max_len]]


# ── Knowledge CRUD ───────────────────────────────────────────────────────────


class ConteudoCorrompido(ValueError):
    """Conteudo com a assinatura do chunker antigo (sobreposicao de 50 chars)."""


# Trecho longo repetido 3x ou mais em sequencia na mesma linha. E a marca que o
# chunker antigo deixava ao remontar o texto: "...avul sa, sem lote minimo de
# 25. cobranca unitaria avul sa, sem lote minimo de 25. cobranca unitaria avul".
# Prosa normal nao repete 25+ caracteres tres vezes seguidas.
_ASSINATURA_OVERLAP = re.compile(r"(.{25,}?)\1{2,}")


def detectar_corrupcao(content: str) -> str:
    """Descreve a corrupcao encontrada no texto, ou "" se estiver limpo.

    O prompt operacional foi destruido uma vez assim: o conteudo remontado dos
    chunks foi salvo de volta por cima do original, e cada edicao seguinte pela
    aba Knowledge amplificou o estrago. Salvar e o ponto onde da para barrar.
    """
    linhas_ruins = []
    for n, linha in enumerate((content or "").split("\n"), start=1):
        m = _ASSINATURA_OVERLAP.search(linha)
        if m:
            linhas_ruins.append((n, m.group(1).strip()[:60]))
    if not linhas_ruins:
        return ""
    amostra = "; ".join(f"linha {n}: ...{t}..." for n, t in linhas_ruins[:3])
    return (
        f"{len(linhas_ruins)} linha(s) com trecho repetido em sequencia "
        f"({amostra}). Isso e sinal de texto remontado dos chunks do RAG, nao "
        f"de conteudo real - salvar assim apagaria o original."
    )


def save_knowledge(filename: str, content: str, description: str = "") -> None:
    """Salva knowledge no banco: deleta antigo, chunka, embeda, insere."""
    content = (content or "").strip()
    if not content:
        return

    problema = detectar_corrupcao(content)
    if problema:
        raise ConteudoCorrompido(f"{filename}: {problema}")

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

    # Texto ORIGINAL: fonte da verdade lida pelo agente (chunks sao so busca)
    database.set_knowledge_document(filename, content, description, status="indexed")

    invalidate_knowledge_caches()
    print(f"[rag] salvo {filename}: {len(chunks)} chunks")


def _format_search_results(results: list[dict], query: str) -> str:
    """Formata os trechos recuperados, cada um rotulado com o arquivo de origem.

    Quando os trechos recuperados de um arquivo forem pequenos demais para
    responder com segurança (tabelas cortadas, por exemplo), devolve o arquivo
    inteiro daquele arquivo — e só dele, nunca de todos.
    """
    if not results:
        return ""

    by_file: dict[str, list[str]] = {}
    for result in results:
        filename = result.get("filename", "")
        content = (result.get("content") or "").strip()
        if not content:
            continue
        by_file.setdefault(filename, []).append(content)

    blocks = []
    for filename, contents in by_file.items():
        joined = ("\n\n").join(contents)
        full = get_file_content(filename)
        # Se o arquivo inteiro e pequeno, ou se os trechos cobrem quase tudo,
        # vale mais entregar o documento completo do que fragmentos.
        if full and (len(full) <= 4000 or len(joined) >= 0.6 * len(full)):
            blocks.append(f"[{filename}]" + "\n" + full)
        else:
            blocks.append(f"[{filename}]" + "\n" + joined)

    return ("\n\n---\n\n").join(blocks)


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
            joined = _format_search_results(results, query)
            print(f"[rag] search vectorial query_len={len(query)} results={len(results)} context_len={len(joined)}")
            return joined
    except Exception as e:
        print(f"[rag] WARN busca vetorial falhou: {e}")

    # Fallback: busca textual por palavras-chave no banco
    try:
        results = database.search_knowledge([], top_k=top_k, query_text=query)
        if results:
            joined = _format_search_results(results, query)
            print(f"[rag] search fallback query_len={len(query)} results={len(results)} context_len={len(joined)}")
            return joined
    except Exception as e:
        print(f"[rag] WARN fallback falhou: {e}")

    return ""


def list_files() -> list[dict]:
    """Lista arquivos de knowledge no banco (filename + chunks + descrição)."""
    return database.list_knowledge_files()


def get_file(filename: str) -> dict | None:
    """Retorna o conteúdo completo de um arquivo + descrição."""
    content = get_file_content(filename)
    if not content:
        return None
    description = database.get_knowledge_file_description(filename)
    return {"filename": filename, "content": content, "description": description}


def delete_file(filename: str) -> None:
    """Deleta todos os chunks de um arquivo e sua descrição."""
    database.delete_knowledge_file(filename)
    invalidate_knowledge_caches()
    print(f"[rag] deletado {filename}")


# ── Smart Knowledge (tool call support) ──────────────────────────────────────

def get_file_descriptions() -> list[dict]:
    """Retorna índice compacto de arquivos + descrições para injetar no system prompt."""
    return database.get_all_knowledge_descriptions()


def get_file_content(filename: str) -> str:
    """Retorna o conteúdo completo e FIEL de um arquivo.

    Prioriza o documento original guardado em knowledge_files.content. Só cai
    para a remontagem dos chunks em bases antigas ainda não reindexadas —
    essa remontagem é aproximada; rode reindex_knowledge.py.
    """
    filename = (filename or "").strip()
    if not filename:
        return ""

    cached = _DOC_CACHE.get(filename)
    if cached is not None:
        return cached

    content = ""
    try:
        content = database.get_knowledge_document(filename)
    except Exception as e:
        print(f"[rag] WARN documento original indisponivel ({filename}): {e}")

    if not content:
        chunks = database.get_knowledge_chunks(filename)
        if not chunks:
            return ""
        chunks.sort(key=lambda c: c["chunk_index"])
        content = "\n".join(c["content"] for c in chunks)
        print(f"[rag] AVISO: {filename} sem documento original - remontado dos chunks (rode reindex_knowledge.py)")

    _DOC_CACHE[filename] = content
    return content


def load_prompt_db_first(db_name: str, local_path: str, fallback_content: str = "") -> str:
    """Carrega prompt do banco primeiro, depois disco, depois fallback.
    Usado por sub-agentes para carregar seus .md de prompt."""
    # 1. Tenta do banco de dados
    content = get_file_content(db_name)
    if content:
        return content
    # 2. Tenta do arquivo local
    try:
        from pathlib import Path
        p = Path(local_path)
        if p.exists():
            return p.read_text(encoding="utf-8")
    except Exception:
        pass
    # 3. Fallback
    return fallback_content


def reload_agent_prompts():
    """Limpa cache de prompts de todos os sub-agentes."""
    try:
        from agents import triagem, cardapio, pedido, logistica, pagamento, crise
        for mod in (triagem, cardapio, pedido, logistica, pagamento, crise):
            # Cada agent tem _prompt como atributo de classe, resetamos
            cls = getattr(mod, mod.__name__.split('.')[-1].title() + 'Agent', None)
            if cls and hasattr(cls, '_prompt'):
                # Reset no prototype para afetar todas as instâncias
                cls._prompt = None
        # Os agentes ativos guardam _prompt na instancia, portanto tambem
        # precisam ser invalidados no singleton ja criado.
        from agents import conversation
        manager = conversation._manager
        if manager:
            for agent in manager.agents.values():
                if hasattr(agent, "_prompt"):
                    agent._prompt = None
        print("[knowledge] prompts dos sub-agentes recarregados")
    except Exception as e:
        print(f"[knowledge] aviso ao recarregar prompts: {e}")


def invalidate_knowledge_caches():
    """Invalida caches que dependem do conteudo atualizado do knowledge."""
    _DOC_CACHE.clear()
    _CONTEXT_CACHE.clear()
    try:
        import facts
        facts.invalidate()
    except Exception as e:
        print(f"[knowledge] aviso ao limpar cache de facts: {e}")
    try:
        import parse_knowledge
        parse_knowledge.reload_cache()
    except Exception as e:
        print(f"[knowledge] aviso ao limpar cache de parse: {e}")
    reload_agent_prompts()


def get_full_knowledge_content(include_prompts: bool = False) -> str:
    """Retorna o conteúdo dos arquivos de knowledge, pronto para o system prompt.

    Por padrão devolve só os arquivos de DADOS (cardápio, bairros, horários,
    políticas). Os arquivos de prompt ficam de fora: o prompt operacional já é
    carregado por parse_knowledge e os .md são dos sub-agentes — injetá-los
    aqui duplicava instruções e criava regras conflitantes no mesmo contexto.
    """
    cache_key = "all" if include_prompts else "dados"
    cached = _CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    docs = database.get_all_knowledge_documents()
    parts = []
    used = []

    if docs:
        for doc in docs:
            filename = doc.get("filename", "")
            if not include_prompts and is_prompt_file(filename):
                continue
            content = (doc.get("content") or "").strip()
            if not content:
                continue
            used.append(filename)
            parts.append(f"=== {filename} ==="  + "\n\n" + content)
    else:
        # Base antiga (sem documento original): remonta pelos chunks
        chunks = database.get_all_knowledge_content()
        current_file = None
        buf = []
        for c in chunks:
            if c["filename"] != current_file:
                if buf and current_file and (include_prompts or not is_prompt_file(current_file)):
                    used.append(current_file)
                    parts.append(f"=== {current_file} ==="  + "\n\n" + "\n".join(buf))
                current_file = c["filename"]
                buf = []
            buf.append(c["content"])
        if buf and current_file and (include_prompts or not is_prompt_file(current_file)):
            used.append(current_file)
            parts.append(f"=== {current_file} ==="  + "\n\n" + "\n".join(buf))

    result = ("\n\n").join(parts)
    _CONTEXT_CACHE[cache_key] = result
    print(f"[rag] knowledge context: {len(result)} chars, arquivos={used}")
    return result


def get_knowledge_for_agent(agent_name: str) -> str:
    """Retorna knowledge relevante por area, com fallback seguro para a base completa."""
    keywords = {
        "triagem": ("horario", "horário", "cardapio", "cardápio", "geral"),
        "cardapio": ("cardapio", "cardápio", "produto", "preco", "preço"),
        "pedido": ("cardapio", "cardápio", "produto", "preco", "preço", "frete"),
        "logistica": ("frete", "bairro", "entrega", "horario", "horário"),
        "crise": ("crise", "reclam", "cancel", "troca", "devol", "politica", "política"),
    }
    terms = keywords.get(agent_name.lower())
    if not terms:
        return get_full_knowledge_content()

    selected = []
    for item in list_files():
        filename = item.get("filename", "")
        # cardapio.md e o PROMPT do sub-agente, nao dado consultavel: sem este
        # filtro o agente recebia as proprias instrucoes como se fossem base.
        if is_prompt_file(filename):
            continue
        description = item.get("description", "") or ""
        if any(term in f"{filename} {description}".lower() for term in terms):
            content = get_file_content(filename)
            if content:
                selected.append(f"=== {filename} ===\n{content}")
    return "\n\n".join(selected) or get_full_knowledge_content()


# ── Knowledge Batch Operations ────────────────────────────────────────────────


def save_knowledge_raw(filename: str, content: str, description: str = "") -> None:
    """Salva knowledge SEM embeddings (status=pending). Para upload em lote."""
    content = (content or "").strip()
    if not content:
        return

    problema = detectar_corrupcao(content)
    if problema:
        raise ConteudoCorrompido(f"{filename}: {problema}")

    database.delete_knowledge_file(filename)

    chunks = chunk_text(content)
    if not chunks:
        return

    database.insert_knowledge_chunks(filename, chunks, [None] * len(chunks))
    database.set_knowledge_document(filename, content, description, status="pending")
    invalidate_knowledge_caches()
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
    invalidate_knowledge_caches()
    print(f"[rag] todo knowledge deletado: {count} arquivos")
    return count


def list_files_with_status() -> list[dict]:
    """Lista arquivos com status (pending/indexed/error)."""
    return database.list_knowledge_files_with_status()
