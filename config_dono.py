"""
config_dono.py — O que o dono ajusta pelo painel, por cima do padrao do codigo.

Duas coisas moram aqui: as frases da oferta de acompanhamento (uma por faixa de
horario) e os produtos que ele liga ao cardapio digital (produto novo ou item
que estava bloqueado). As duas ficam na tabela `settings` como JSON.

A regra e uma so: o codigo continua sendo o PADRAO. Setting ausente, vazio ou
com JSON quebrado devolve exatamente o que o codigo faria sem este modulo - a
Sandrinha nunca fica pior por causa de uma gravacao ruim no painel.
"""

import json
import time

import database

CHAVE_OFERTAS = "ofertas_por_horario"
CHAVE_CATALOGO = "catalogo_extra"

# Quantas faixas de horario a oferta tem (as faixas sao fixas no codigo:
# conversation_memory._OFERTAS_POR_HORARIO).
FAIXAS_DE_OFERTA = 4

# Tamanho maximo de uma frase de oferta: ela sai inteira para o cliente.
MAX_CARACTERES_OFERTA = 200

TIPOS = ("sabor", "unidade")
FAMILIAS = ("Fritos", "Assados", "Canudinhos")

# A oferta e lida varias vezes por mensagem. Sem cache cada leitura ia ao banco;
# 30s e curto o bastante para a troca no painel valer "na hora" para o dono.
_TTL_SEGUNDOS = 30
_cache: dict[str, tuple[float, object]] = {}


def _ler_json(chave: str):
    """Valor do setting ja decodificado, ou None (ausente, vazio ou invalido)."""
    agora = time.monotonic()
    guardado = _cache.get(chave)
    if guardado and agora - guardado[0] < _TTL_SEGUNDOS:
        return guardado[1]
    try:
        bruto = database.get_setting(chave, "")
        valor = json.loads(bruto) if bruto else None
    except Exception as e:
        print(f"[config] {chave} ilegivel, usando o padrao: {e}")
        valor = None
    _cache[chave] = (agora, valor)
    return valor


# Sobe a cada gravacao. Quem monta indice a partir do catalogo (nomes de
# produto, familia do sabor) guarda a versao com que montou e remonta quando
# ela muda. Importar os modulos daqui para zerar o cache deles nao serve: o
# servidor roda como `python app.py`, e `import app` carregaria uma SEGUNDA
# copia do servidor inteiro.
_versao = 0


def versao() -> int:
    return _versao


def invalidar() -> None:
    """Esquece o cache: chamado sempre que o painel grava."""
    global _versao
    _cache.clear()
    _versao += 1


# ── Frases da oferta ────────────────────────────────────────────────────────

def ofertas() -> list[str]:
    """As 4 frases do dono, na ordem das faixas. '' onde vale o padrao."""
    valor = _ler_json(CHAVE_OFERTAS)
    if not isinstance(valor, list):
        return [""] * FAIXAS_DE_OFERTA
    frases = [str(v).strip() if isinstance(v, str) else "" for v in valor[:FAIXAS_DE_OFERTA]]
    return frases + [""] * (FAIXAS_DE_OFERTA - len(frases))


def validar_oferta(frase: str) -> str:
    """Motivo da recusa, ou '' quando a frase pode ser gravada. '' vazio = padrao."""
    frase = (frase or "").strip()
    if not frase:
        return ""
    if len(frase) > MAX_CARACTERES_OFERTA:
        return f"passa de {MAX_CARACTERES_OFERTA} caracteres"
    if "?" not in frase:
        return "precisa ser uma pergunta (terminar com '?')"
    return ""


def salvar_ofertas(frases: list) -> None:
    frases = [(f or "").strip() for f in (frases or [])][:FAIXAS_DE_OFERTA]
    frases += [""] * (FAIXAS_DE_OFERTA - len(frases))
    database.set_setting(CHAVE_OFERTAS, json.dumps(frases, ensure_ascii=False))
    invalidar()


# ── Produtos ligados ao cardapio digital ────────────────────────────────────

def _entrada_valida(e) -> dict | None:
    """A entrada normalizada, ou None se faltar o essencial."""
    if not isinstance(e, dict):
        return None
    nome = str(e.get("nome_planilha") or "").strip()
    tipo = str(e.get("tipo") or "").strip()
    destino = str(e.get("nome_cardapio") or "").strip()
    if not nome or not destino or tipo not in TIPOS:
        return None
    saida = {"nome_planilha": nome, "tipo": tipo, "nome_cardapio": destino}
    if tipo == "sabor":
        familia = str(e.get("familia") or "").strip()
        if familia not in FAMILIAS:
            return None
        saida["familia"] = familia
    else:
        saida["categoria"] = str(e.get("categoria") or "").strip()
        try:
            saida["fator"] = max(1, int(e.get("fator") or 1))
        except (TypeError, ValueError):
            saida["fator"] = 1
    apelidos = e.get("apelidos") or []
    if isinstance(apelidos, str):
        apelidos = apelidos.split(",")
    saida["apelidos"] = [str(a).strip().lower() for a in apelidos if str(a).strip()]
    return saida


def catalogo_extra() -> list[dict]:
    """Entradas validas que o dono gravou. [] quando nao ha nada."""
    valor = _ler_json(CHAVE_CATALOGO)
    if not isinstance(valor, list):
        return []
    return [e for e in (_entrada_valida(v) for v in valor) if e]


def salvar_catalogo(entradas: list) -> None:
    limpas = [e for e in (_entrada_valida(v) for v in (entradas or [])) if e]
    database.set_setting(CHAVE_CATALOGO, json.dumps(limpas, ensure_ascii=False))
    invalidar()
