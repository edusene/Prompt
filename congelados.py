"""
congelados.py — Oferta e pedido de salgados congelados.

Saiu de app.py em 23/10; app.py reexporta os nomes.
"""

import re as _re_mod

import cardapio
import facts
from agents.conversation import _extract_data_from_message, ConversationState


# ── Congelados ─────────────────────────────────────────────────────────────
# Texto de venda do dono (08/10). Os sabores e o preco vem do cardapio digital
# quando o produto congelado estiver cadastrado la; ate isso, vale a lista do
# dono. Pedido de congelado sem o produto no cardapio nao tem preco: ia virar
# o salgado FRITO (R$ 80 o cento no lugar de R$ 40) - a equipe registra.
_SABORES_CONGELADOS_DO_DONO = ("Coxinha", "Bolinha de Queijo", "Risole de Carne",
                               "Presunto e Queijo", "Kibe")
_RX_CONGELADO = _re_mod.compile(r"\bcongelad\w*")
# Nome curto do cardapio -> como a cliente pede. Com "Salsicha" na lista, ela
# ouviu que enroladinho de salsicha congelado nao existia (teste de 27/10).
_NOME_DO_SABOR_CONGELADO = {"salsicha": "Enroladinho de Salsicha",
                            "rissoles carne": "Rissole de Carne"}
TEXTO_CONGELADO_PARA_EQUIPE = ("Anotei seu pedido de congelados! ❄️ Vou passar para "
                               "nossa equipe confirmar e finalizar com você. "
                               "Um instante, por favor. 💛")


def _congelados_do_cardapio() -> list[dict]:
    """As linhas do cardapio digital que sao de congelado (categoria ou nome)."""
    try:
        return [p for p in facts.get()["produtos"] if p.get("disponivel", True)
                and _RX_CONGELADO.search(facts.normalize(f"{p['categoria']} {p['item']}"))]
    except Exception:
        return []


def _sem_congelado(nome: str) -> str:
    return _re_mod.sub(r"\s*\bcongelad[oa]s?\b", "", nome, flags=_re_mod.IGNORECASE).strip()


def texto_congelado() -> str:
    """A oferta de congelado no padrao do dono, com os dados do cardapio."""
    linhas = _congelados_do_cardapio()
    # O pacote do cardapio traz os sabores nas opcoes (27/10: a lista fixa do
    # dono so tinha 5 dos 10).
    sabores = [_NOME_DO_SABOR_CONGELADO.get(facts.normalize(str(s)), str(s).strip())
               for p in linhas for s in p.get("opcoes") or []]
    sabores += [_sem_congelado(p["item"]) for p in linhas
                if not p.get("opcoes") and not cardapio.nome_e_so_formato(p["item"])
                and facts.normalize(p["item"]) != facts.normalize(p["categoria"])]
    sabores = [s for s in dict.fromkeys(sabores) if s] or list(_SABORES_CONGELADOS_DO_DONO)
    cento = next((p["preco"] for p in linhas if p.get("preco")
                  and cardapio.unidades_do_rotulo(p["item"]) == 100), None)
    preco = cardapio._brl(cento).replace(",00", "") if cento else "R$ 40"
    return ("Sim, trabalhamos sim! ❄️\n"
            f"Mini Salgados Congelados – {preco} o cento\n"
            "Salgadinhos prontos para fritar quando você quiser!\n"
            "Nossos sabores:\n"
            + "".join(f"• {s}\n" for s in sabores)
            + "💡 Dica: Fritar ainda congelados em óleo bem quente (180°C) até dourar.\n"
            "Qual sabor ou combinação você gostaria de pedir hoje?")


def _versao_congelada(nome: str) -> str:
    """"Coxinha" -> a linha congelada do cardapio com o mesmo sabor, ou ""."""
    from agents.conversation import _forma_de_busca
    palavras = [w[:5] for w in _forma_de_busca(nome).split() if len(w) >= 4]
    if not palavras:
        return ""
    import cardapio_catalogo
    for p in _congelados_do_cardapio():
        if cardapio.nome_e_so_formato(p["item"]):
            continue
        # Sem o pacote no de-para do pedido (nome no Base44, sabores), o envio
        # recusa a categoria: ate ligar o de-para, a equipe registra.
        if p["categoria"] not in cardapio_catalogo.LOTES:
            continue
        suas = _forma_de_busca(_sem_congelado(p["item"])).split()
        if any(w.startswith(palavras[0]) for w in suas):
            return p["item"]
    return ""


def pedido_de_congelado(texto: str, ultima_do_agente: str) -> bool:
    """O cliente esta PEDINDO congelado: com a palavra, ou logo depois da oferta.

    "quero 1 cento de coxinha" respondendo ao texto de congelado e congelado;
    "frito"/"assado" na mensagem desfaz.
    """
    t = facts.normalize(texto or "")
    if _re_mod.search(r"\b(?:frit\w*|assad\w*)\b", t) and not _RX_CONGELADO.search(t):
        return False
    if not (_RX_CONGELADO.search(t) or "Mini Salgados Congelados" in (ultima_do_agente or "")):
        return False
    itens = _extract_data_from_message((texto or "").lower(),
                                       ConversationState(phone="")).get("items") or []
    return bool(itens)
