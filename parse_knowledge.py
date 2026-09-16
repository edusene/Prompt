"""
parse_knowledge.py — Le os arquivos .docx e .xlsx de conhecimento da BALBS
e retorna o conteudo formatado para injetar no system prompt do agente.
"""

import os
import re
from pathlib import Path

_dir = Path(__file__).parent
_PROMPT_DOCX = _dir / "PROMPTS EDUARDO 13.09.26.docx"
_DADOS_XLSX = _dir / "PLANILHA CARDAPIO E BAIRROS EDUARDO.xlsx"


def _read_docx() -> str:
    """Le o .docx e retorna o texto completo."""
    try:
        from docx import Document
    except ImportError:
        print("[parse_knowledge] python-docx nao instalado, pulando prompt docx")
        return ""
    if not _PROMPT_DOCX.exists():
        print(f"[parse_knowledge] arquivo nao encontrado: {_PROMPT_DOCX.name}")
        return ""
    doc = Document(str(_PROMPT_DOCX))
    lines = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(lines)


def _adaptar_prompt_docx(texto: str) -> str:
    """Adapta o prompt do docx para o nosso sistema (remove refs de workflow)."""
    # Remove linhas de trigger/variaveis de workflow
    texto = re.sub(r".*\$\{\s*\.\s*trigger.*\}.*\n?", "", texto)
    # Remove refs a syncWhatsAppConversation
    texto = re.sub(r".*syncWhatsAppConversation.*\n?", "", texto)
    # Substitui createOrder por send_order_to_cardapio (referencias textuais)
    texto = texto.replace("createOrder", "send_order_to_cardapio")
    # Substitui sendWhatsAppMessage por "resposta direta do agente"
    texto = re.sub(
        r".*sendWhatsAppMessage.*\n?",
        "Envie a mensagem diretamente como resposta ao cliente.\n",
        texto,
    )
    # Remove linhas de disparo obrigatório duplicadas
    texto = re.sub(r"=+\nDISPARO OBRIGATÓRIO.*", "", texto, flags=re.DOTALL)
    return texto.strip()


def _read_xlsx() -> str:
    """Le o .xlsx e converte cada aba para texto estruturado limpo."""
    try:
        import pandas as pd
    except ImportError:
        print("[parse_knowledge] pandas/openpyxl nao instalado, pulando xlsx")
        return ""
    if not _DADOS_XLSX.exists():
        print(f"[parse_knowledge] arquivo nao encontrado: {_DADOS_XLSX.name}")
        return ""

    xls = pd.ExcelFile(str(_DADOS_XLSX))
    partes = []

    for sheet in xls.sheet_names:
        df = pd.read_excel(xls, sheet_name=sheet)
        # Remove linhas 100% NaN
        df = df.dropna(how="all")
        # Remove colunas 100% NaN
        df = df.dropna(axis=1, how="all")
        if df.empty:
            continue
        # Limpa nomes de colunas Unnamed
        df.columns = [
            f"col{i}" if "Unnamed" in str(c) else str(c) for i, c in enumerate(df.columns)
        ]
        texto = df.to_string(index=False, max_colwidth=120)
        partes.append(f"=== {sheet} ===\n{texto}")

    return "\n\n".join(partes)


def get_prompt_system() -> str:
    """Retorna o system prompt adaptado do docx (bloco principal de instrucoes)."""
    raw = _read_docx()
    if not raw:
        return ""
    return _adaptar_prompt_docx(raw)


def get_knowledge_dados() -> str:
    """Retorna os dados do xlsx (cardapio, bairros, horarios, pagamentos, regras)."""
    return _read_xlsx()


def get_full_prompt_with_knowledge() -> str:
    """Retorna prompt + knowledge completos para injetar no system prompt."""
    prompt = get_prompt_system()
    dados = get_knowledge_dados()
    parts = []
    if prompt:
        parts.append(prompt)
    if dados:
        parts.append(
            "\n\n=== BASE DE DADOS OFICIAL (PRECOS, BAIRROS, HORARIOS, PAGAMENTOS) ===\n"
            "Use estes dados como FONTE VERDADEIRA para precos, taxas de frete, "
            "horarios de funcionamento e formas de pagamento.\n\n"
            + dados
        )
    return "\n\n".join(parts)


# Cache em memoria para evitar re-leitura a cada chamada
_cached_prompt = None
_cached_dados = None


def get_prompt_cached() -> str:
    """Retorna o system prompt completo (cached)."""
    global _cached_prompt
    if _cached_prompt is None:
        _cached_prompt = get_full_prompt_with_knowledge()
        if _cached_prompt:
            print(
                f"[parse_knowledge] prompt carregado: {len(_cached_prompt)} chars "
                f"(docx={len(get_prompt_system())} chars, "
                f"xlsx={len(get_knowledge_dados())} chars)"
            )
    return _cached_prompt or ""


def reload_cache():
    """Forca recarregamento do cache (util para atualizacoes)."""
    global _cached_prompt, _cached_dados
    _cached_prompt = None
    _cached_dados = None
    print("[parse_knowledge] cache recarregado")
