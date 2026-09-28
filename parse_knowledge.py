"""
parse_knowledge.py — Le os arquivos .docx e .xlsx de conhecimento da BALBS
e retorna o conteudo formatado para injetar no system prompt do agente.
Busca primeiro no banco de dados, depois fallback para arquivos locais.
"""

import os
import re
from pathlib import Path

_dir = Path(__file__).parent

# Versao vigente do prompt operacional, na frente das anteriores. Esta lista e
# a unica fonte do nome do arquivo: knowledge_rag e reindex_knowledge importam
# daqui. Quando o dono mandar uma versao nova, basta poe-la no topo.
#
# As versoes antigas continuam listadas depois de o arquivo sair do repositorio
# e da base. Nao e resto: e o que faz reindex_knowledge.DOCX_OBSOLETOS apagar a
# versao antiga se alguem reenvia-la pela aba Knowledge do painel, e o que faz
# knowledge_rag.PROMPT_FILES trata-la como INSTRUCAO e nao como dado — sem
# isso, um reenvio acidental volta a valer junto com a versao certa.
PROMPT_DOCX_VERSOES = (
    "PROMPTS ATUALIZADO PARA ENVIAR EDUARDO 18.09.26.docx",
    "PROMPTS EDUARDO 13.09.26.docx",  # substituida em 19/09/2026
)
PROMPT_DOCX_NAME = PROMPT_DOCX_VERSOES[0]

_DADOS_XLSX = _dir / "PLANILHA CARDAPIO E BAIRROS EDUARDO.xlsx"

# Nomes dos arquivos como serao salvos no knowledge DB
_DADOS_DB_NAME = "PLANILHA CARDAPIO E BAIRROS EDUARDO.xlsx"


def _read_from_db(filename: str) -> str:
    """Tenta ler conteudo de um arquivo knowledge do banco de dados."""
    try:
        import knowledge_rag
        content = knowledge_rag.get_file_content(filename)
        if content and content.strip():
            print(f"[parse_knowledge] lido do DB: {filename} ({len(content)} chars)")
            return content
    except Exception as e:
        print(f"[parse_knowledge] WARN nao conseguiu ler do DB: {e}")
    return ""


def _read_docx() -> str:
    """Le o prompt operacional: banco primeiro, disco depois, da versao mais
    nova para a mais antiga."""
    for nome in PROMPT_DOCX_VERSOES:
        content = _read_from_db(nome)
        if content:
            return content

    try:
        from docx import Document
    except ImportError:
        print("[parse_knowledge] python-docx nao instalado, pulando prompt docx")
        return ""

    for nome in PROMPT_DOCX_VERSOES:
        caminho = _dir / nome
        if not caminho.exists():
            continue
        doc = Document(str(caminho))
        linhas = [p.text for p in doc.paragraphs if p.text.strip()]
        texto = "\n".join(linhas)
        if texto.strip():
            print(f"[parse_knowledge] lido do disco: {nome} ({len(texto)} chars)")
            return texto

    print(f"[parse_knowledge] nenhum prompt operacional encontrado em disco nem no banco")
    return ""


def _adaptar_prompt_docx(texto: str) -> str:
    """Adapta o prompt do docx para o nosso sistema (remove refs de workflow)."""
    # Remove linhas de trigger/variaveis de workflow
    texto = re.sub(r".*\$\{\s*\.\s*trigger.*\}.*\n?", "", texto)
    # Remove refs a syncWhatsAppConversation
    texto = re.sub(r".*syncWhatsAppConversation.*\n?", "", texto)
    # Substitui createOrder por send_order_to_cardapio (referencias textuais)
    texto = texto.replace("createOrder", "send_order_to_cardapio")
    # DeliveryFee era a funcao de frete do workflow antigo. Aqui o frete sai da
    # tool consultar_frete; deixar o nome antigo no prompt fazia o modelo citar
    # uma ferramenta que nao existe em vez de chamar a que existe.
    texto = re.sub(r"\bDeliveryFee\b", "consultar_frete", texto)
    # change_for nao existe no contrato do cardapio digital: a lista oficial de
    # campos do desenvolvedor nao tem esse campo. Na versao V7 a unica mencao
    # cai na mesma linha do ${ .trigger }, que a regra acima ja apaga inteira —
    # isto aqui e para a proxima versao do documento, em que a mencao pode vir
    # sozinha. O troco vai na observacao do pedido, montada em codigo.
    texto = re.sub(
        r",?\s*change_for\b",
        "", texto,
    )
    texto = texto.replace(
        "registre troco = 0.00 e prossiga",
        "registre que ele tem o valor trocado e prossiga",
    )
    # Substitui sendWhatsAppMessage por "resposta direta do agente"
    texto = re.sub(
        r".*sendWhatsAppMessage.*\n?",
        "Envie a mensagem diretamente como resposta ao cliente.\n",
        texto,
    )
    # Remove linhas de disparo obrigatório duplicadas
    texto = re.sub(r"=+\nDISPARO OBRIGATÓRIO.*", "", texto, flags=re.DOTALL)
    return texto.strip()


def _clean_cell(value) -> str:
    """Normaliza uma celula da planilha para texto."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "nat", "none"):
        return ""
    # 8.0 -> 8 ; 12.5 -> 12.5 (evita "R$ 8.0" na tabela de frete)
    if text.replace(".", "", 1).replace("-", "", 1).isdigit() and text.endswith(".0"):
        text = text[:-2]
    return text


def sheet_rows_to_text(sheet_name: str, headers: list, rows: list) -> str:
    """Formata uma aba como cabecalho + UMA LINHA POR REGISTRO.

    Uma linha por registro e essencial: o chunker nunca quebra uma linha, entao
    nenhum bairro fica separado da sua taxa nem produto do seu preco.
    """
    headers = [_clean_cell(h) or f"col{i+1}" for i, h in enumerate(headers)]
    lines = [f"=== {sheet_name} ===", " | ".join(headers)]
    seen = set()
    for row in rows:
        cells = [_clean_cell(c) for c in row]
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            continue
        line = " | ".join(cells)
        # A planilha repete blocos de cabecalho/linhas; duplicata vira ruido
        if line in seen or line == " | ".join(headers):
            continue
        seen.add(line)
        lines.append(line)
    return ("\n").join(lines)


def _detect_header_row(rows: list) -> int:
    """Indice da primeira linha que parece cabecalho (2+ celulas de texto)."""
    for idx, row in enumerate(rows):
        filled = [c for c in row if c]
        if len(filled) >= 2 and any(not c.replace(".", "").replace(",", "").isdigit() for c in filled):
            return idx
    return 0


def xlsx_to_text(source) -> str:
    """Converte um .xlsx (caminho ou file-like) em texto estruturado limpo.

    Substitui o antigo df.to_string(), que alinhava colunas com dezenas de
    espacos e era truncado no meio das linhas pelo chunker — origem dos precos
    e fretes errados que o agente informava. Usa openpyxl (dependencia
    declarada); pandas nao e necessario.
    """
    try:
        import openpyxl
    except ImportError:
        print("[parse_knowledge] openpyxl nao instalado, pulando xlsx")
        return ""

    wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
    partes = []
    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            for row in ws.iter_rows(values_only=True):
                cells = [_clean_cell(c) for c in row]
                if any(cells):
                    rows.append(cells)
            if not rows:
                continue

            # Remove colunas totalmente vazias
            width = max(len(r) for r in rows)
            keep = [
                ci for ci in range(width)
                if any(ci < len(r) and r[ci] for r in rows)
            ]
            rows = [[r[ci] if ci < len(r) else "" for ci in keep] for r in rows]

            header_idx = _detect_header_row(rows)
            headers = rows[header_idx]
            texto = sheet_rows_to_text(sheet_name, headers, rows[header_idx + 1:])
            if texto:
                partes.append(texto)
    finally:
        try:
            wb.close()
        except Exception:
            pass

    return ("\n\n").join(partes)


def _read_xlsx() -> str:
    """Le o .xlsx e converte cada aba para texto estruturado limpo.
    Tenta DB primeiro, depois local.
    """
    content = _read_from_db(_DADOS_DB_NAME)
    if content:
        return content

    if not _DADOS_XLSX.exists():
        print(f"[parse_knowledge] arquivo nao encontrado: {_DADOS_XLSX.name}")
        return ""
    try:
        return xlsx_to_text(str(_DADOS_XLSX))
    except Exception as e:
        print(f"[parse_knowledge] ERRO ao ler xlsx: {e}")
        return ""


def get_prompt_system() -> str:
    """Retorna o system prompt adaptado do docx (bloco principal de instrucoes)."""
    raw = _read_docx()
    if not raw:
        return ""
    return _adaptar_prompt_docx(raw)


def get_full_prompt_with_knowledge() -> str:
    """Retorna apenas o prompt operacional (instrucoes do agente).

    Os DADOS (cardapio, bairros, horarios) NAO entram aqui: eles sao injetados
    uma unica vez por app.py, a partir de knowledge_rag.get_full_knowledge_content().
    Antes eram concatenados nos dois lugares e a planilha aparecia duplicada no
    mesmo contexto — instrucao repetida e o que fazia o modelo ignorar a base.
    """
    return get_prompt_system()


# Cache em memoria para evitar re-leitura a cada chamada
_cached_prompt = None


def get_prompt_cached() -> str:
    """Retorna o prompt operacional (cached)."""
    global _cached_prompt
    if _cached_prompt is None:
        _cached_prompt = get_full_prompt_with_knowledge()
        if _cached_prompt:
            print(f"[parse_knowledge] prompt operacional carregado: {len(_cached_prompt)} chars")
    return _cached_prompt or ""


def reload_cache():
    """Forca recarregamento do cache (util para atualizacoes via aba Knowledge)."""
    global _cached_prompt
    _cached_prompt = None
    print("[parse_knowledge] cache recarregado")
