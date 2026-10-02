"""
parse_knowledge.py — Le o .docx do prompt operacional da BALBS
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
