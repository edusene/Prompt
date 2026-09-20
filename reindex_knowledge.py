"""
reindex_knowledge.py — Reconstroi a base de knowledge a partir dos arquivos
originais e regrava chunks + embeddings com o pipeline corrigido.

Por que existe: ate aqui o texto oficial era remontado a partir dos chunks, e o
chunker antigo cortava linhas no meio e gravava 50 caracteres do chunk anterior
dentro do seguinte. O resultado era tabela de preco/frete quebrada e frases
duplicadas no meio do prompt — a causa das respostas inventadas.

Uso:
    python reindex_knowledge.py --dry-run   # so compara, nao grava
    python reindex_knowledge.py             # regrava documentos + chunks
    python reindex_knowledge.py --embed     # regrava e gera embeddings
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

import database  # noqa: E402
import knowledge_rag  # noqa: E402
import knowledge_repair  # noqa: E402
import parse_knowledge  # noqa: E402

BASE = Path(__file__).parent

XLSX = "PLANILHA CARDAPIO E BAIRROS EDUARDO.xlsx"
DOCX = parse_knowledge.PROMPT_DOCX_NAME
# Versoes anteriores do prompt: sao APAGADAS da base. O agente le a primeira
# versao que encontrar, e duas na base valem como duas instrucoes diferentes
# para a mesma coisa — foi assim que o prompt antigo continuou valendo depois
# de o dono mandar o novo.
DOCX_OBSOLETOS = tuple(n for n in parse_knowledge.PROMPT_DOCX_VERSOES if n != DOCX)
AGENT_PROMPTS = ("triagem.md", "cardapio.md", "pedido.md", "logistica.md", "pagamento.md", "crise.md", "router.md")


def _read_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _undo_overlap(filename: str) -> str:
    """Remonta o texto removendo a sobreposicao de 50 chars do chunker antigo."""
    chunks = database.get_knowledge_chunks(filename)
    if not chunks:
        return ""
    chunks.sort(key=lambda c: c["chunk_index"])
    parts = [chunks[0]["content"]]
    for prev, cur in zip(chunks, chunks[1:]):
        content = cur["content"]
        tail = prev["content"][-50:]
        if content.startswith(tail + " "):
            content = content[len(tail) + 1:]
        parts.append(content)
    return "\n\n".join(parts)


def _best_source(filename: str, disco: str) -> str:
    """Escolhe a melhor origem do texto de um arquivo.

    Ordem:
    1. documento original ja guardado em knowledge_files.content — quando existe
       e nao esta danificado, ele e a fonte da verdade (roda de novo sem perda);
    2. senao, compara o arquivo em disco com a remontagem dos chunks antigos.
       Os dois podem estar danificados (o painel regravava o .docx a partir da
       remontagem), entao vence o que ficar MENOR depois do conserto: duplicacao
       e o unico motivo de um deles ser maior.
    """
    documento = database.get_knowledge_document(filename)
    if documento.strip() and not knowledge_repair.looks_corrupted(documento):
        print(f"[reindex] {filename}: documento original ja intacto ({len(documento)} chars)")
        return documento

    banco = _undo_overlap(filename)
    candidatos = []
    if disco.strip():
        candidatos.append(("disco", knowledge_repair.repair(disco, f"{filename} (disco)")))
    if banco.strip():
        candidatos.append(("banco", knowledge_repair.repair(banco, f"{filename} (banco)")))
    if documento.strip():
        candidatos.append(("documento", knowledge_repair.repair(documento, f"{filename} (documento)")))
    if not candidatos:
        return ""

    origem, texto = min(candidatos, key=lambda c: len(c[1]))
    if len(candidatos) > 1:
        print(f"[reindex] {filename}: usando copia do {origem}")
    return texto


def collect_sources() -> dict:
    """{filename: texto_consertado} a partir das melhores origens disponiveis."""
    sources = {}

    xlsx_path = BASE / XLSX
    if xlsx_path.exists():
        # A planilha e reconvertida do binario original: nunca esta danificada.
        texto = parse_knowledge.xlsx_to_text(str(xlsx_path))
        if texto:
            sources[XLSX] = texto
    else:
        print(f"[reindex] AVISO: {XLSX} nao encontrado em disco")

    docx_path = BASE / DOCX
    disco_docx = ""
    if docx_path.exists():
        try:
            disco_docx = _read_docx(docx_path)
        except Exception as e:
            print(f"[reindex] AVISO: falha ao ler {DOCX}: {e}")
    texto_docx = _best_source(DOCX, disco_docx)
    if texto_docx:
        sources[DOCX] = texto_docx

    for name in AGENT_PROMPTS:
        p = BASE / "prompts" / name
        disco = p.read_text(encoding="utf-8") if p.exists() else ""
        texto = _best_source(name, disco)
        if texto:
            sources[name] = texto

    return sources


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    do_embed = "--embed" in sys.argv

    sources = collect_sources()
    if not sources:
        print("[reindex] nenhum arquivo de origem encontrado — nada a fazer")
        return 1

    print(f"[reindex] arquivos de origem: {len(sources)}")
    for filename, content in sorted(sources.items()):
        atual = _undo_overlap(filename)
        descricao = database.get_knowledge_file_description(filename)
        if not descricao and filename == DOCX:
            # Prompt novo herda a descricao da versao que substitui, senao a
            # aba Knowledge do painel mostra o arquivo mais importante sem
            # legenda nenhuma.
            for obsoleto in DOCX_OBSOLETOS:
                descricao = database.get_knowledge_file_description(obsoleto)
                if descricao:
                    break
            descricao = descricao or "Prompt principal do agente BALBS"
        chunks = knowledge_rag.chunk_text(content)
        print(
            f"  - {filename}: origem={len(content)} chars, "
            f"base_atual={len(atual)} chars, novos_chunks={len(chunks)}, desc={descricao!r}"
        )
        if dry_run:
            continue
        knowledge_rag.save_knowledge_raw(filename, content, description=descricao)

    for obsoleto in DOCX_OBSOLETOS:
        if not database.get_knowledge_document(obsoleto) and not database.get_knowledge_chunks(obsoleto):
            continue
        print(f"[reindex] removendo versao antiga do prompt: {obsoleto}")
        if not dry_run:
            knowledge_rag.delete_file(obsoleto)

    if dry_run:
        print("[reindex] dry-run: nada gravado")
        return 0

    if do_embed:
        print("[reindex] gerando embeddings...")
        result = knowledge_rag.embed_pending_files()
        print(f"[reindex] embeddings: {result}")
    else:
        print("[reindex] chunks gravados como 'pending'. Rode com --embed (ou use o botao do painel) para indexar.")

    knowledge_rag.invalidate_knowledge_caches()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
