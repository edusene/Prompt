"""
cardapio_catalogo.py — De-para entre a planilha oficial e o cardapio digital.

Os dois catalogos sao mantidos separados e divergem em varios pontos. A planilha
manda no preco que o cliente ve; o cardapio digital manda no que aceita receber.
Este modulo traduz de um para o outro e, quando nao ha traducao confiavel,
BLOQUEIA o item em vez de deixar sair pedido com preco errado.

Tudo aqui foi conferido chamando calculateOrder de verdade. Use
verificar_catalogo.py para reconferir sempre que o cardapio mudar.

Como o cardapio digital modela os lotes: o pacote e o produto e o sabor vai em
`selected_flavors`, que a API nao valida contra o cadastro. Entao mandamos o
pacote pelo nome dele e os sabores a parte — assim um sabor que a API nao
conhece (Coxinha frango casa com "Mini quiches" e vem indisponivel) nao derruba
o pedido.

A quantidade e sempre em UNIDADES, e o preco acompanha proporcionalmente:
"25 uni Salgadinhos fritos" com quantity=75 devolve R$ 60,00, igual a planilha.
"""

import facts

# Categorias da planilha que nao viram item de pedido: sao as linhas de formato
# (Porcao/Meio cento/Cento), cujo preco ja esta embutido no pacote.
CATEGORIAS_IGNORADAS = {"Combos"}

_FORMATOS = {"Porção (25 un)", "Meio cento (50 un)", "Cento (100 un)"}


# ── Lotes: um pacote do cardapio digital por familia da planilha ─────────────
# quantidade_teste/preco_esperado sao o caso que verificar_catalogo.py confere.
LOTES = {
    "Fritos": {
        "nome_base44": "25 uni Salgadinhos fritos",
        "minimo": 25,
        "preco_unitario": 0.80,
        "quantidade_teste": 50,
        "preco_esperado": 40.00,
    },
    "Assados": {
        "nome_base44": "Meio Cento Salgadinhos Assados",
        "minimo": 50,  # o cardapio digital NAO tem porcao de 25 de assados
        "preco_unitario": 1.20,
        "quantidade_teste": 100,
        "preco_esperado": 120.00,
    },
}

# Canudinho e Assado na planilha, mas no cardapio digital e produto proprio com
# preco de porcao de 25 - a regra do dono tambem diz que so sai em 25.
_CANUDINHOS = {
    "nome_base44": "Canudinhos",
    "context": "lote",
    "minimo": 25,
    "preco_unitario": 1.20,
    "quantidade_teste": 25,
    "preco_esperado": 30.00,
}


# ── Itens vendidos por unidade, conferidos um a um ───────────────────────────
MAPA = {
    "Rissole de Carne": {
        "nome_base44": "Rissoles de Carne (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    # O nome completo do cadastro nao casa; o proprio nome da planilha casa.
    "Rissole de Queijo e Presunto": {
        "nome_base44": "Rissole de Queijo e Presunto", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Empada de Frango": {
        "nome_base44": "Empada de Frango (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Empada de Camarão": {
        "nome_base44": "Empada de Camarão (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Saltenha Assada": {
        "nome_base44": "Saltenha Assada", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Bomba de Carne de Sol": {
        "nome_base44": "Bomba de carne de sol", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 10.00,
    },
    "Quiche de Frango": {
        "nome_base44": "Quiche de Frango", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 12.00,
    },
    "Bolo de Milho": {
        "nome_base44": "Bolo de Milho", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 30.00,
    },
}


# ── Itens que a planilha vende mas o cardapio digital nao aceita ─────────────
# O agente recusa fechar pedido com eles em producao. A correcao e no cadastro
# do cardapio digital, nao aqui.
BLOQUEADOS = {
    "Coxinha de Frango com Catupiry":
        "nao existe como unidade no cardapio digital; a busca cai no lote de 25",
    "Croquete de Macaxeira":
        "'Item nao encontrado no cardapio' no cardapio digital",
    "Saltenha Frita":
        "so existe como 'Mini Saltenha Frita', com minimo de 10 unidades",
    "Quiche de Carne de Sol":
        "cadastrado como indisponivel no cardapio digital",
    "Panqueca de Carne":
        "'Item nao encontrado no cardapio' no cardapio digital",
    "Panqueca de Frango":
        "cadastrado como indisponivel no cardapio digital",
    "Enroladinho de Salsicha":
        "grande sai a R$ 8,00 no cardapio digital contra R$ 9,00 na planilha",
    "Cento de Brigadeiros (100 un)":
        "cardapio digital devolve R$ 65,00 contra R$ 130,00 da planilha",
    "Cento de Beijinhos (100 un)":
        "cardapio digital devolve R$ 20,80 contra R$ 130,00 da planilha",
    "Cento Misto Brigadeiro e Beijinho (100 un)":
        "cardapio digital devolve R$ 65,00 contra R$ 130,00 da planilha",
    "Meio Cento de Brigadeiros (50 un)":
        "cardapio digital devolve R$ 65,00 contra R$ 70,00 da planilha",
    "Meio Cento de Beijinhos (50 un)":
        "cardapio digital devolve R$ 20,80 contra R$ 70,00 da planilha",
    "Meio Cento Misto (50 un)":
        "cardapio digital devolve R$ 65,00 contra R$ 70,00 da planilha",
    "Bandeja de Brigadeiros (16 un)":
        "cardapio digital devolve R$ 65,00 contra R$ 20,80 da planilha",
    "Refrigerante 2 Litros":
        "'Item nao encontrado no cardapio' no cardapio digital",
    "Guaraná Tuchaua 2 Litros":
        "'Item nao encontrado no cardapio' no cardapio digital",
    "Refrigerante 600ml":
        "cardapio digital devolve R$ 8,00 contra R$ 7,00 da planilha",
    "Refrigerante Lata":
        "cardapio digital devolve R$ 7,00 contra R$ 6,00 da planilha",
    "Suco 500ml":
        "'Item nao encontrado no cardapio' no cardapio digital",
}


def _linha_da_planilha(nome: str) -> dict | None:
    """Linha da aba CARDAPIO que corresponde ao item pedido."""
    alvo = facts.normalize(nome)
    for p in facts.get()["produtos"]:
        if facts.normalize(p["item"]) == alvo:
            return p
    return None


def _bloqueio(nome: str) -> str:
    alvo = facts.normalize(nome)
    for chave, motivo in BLOQUEADOS.items():
        if facts.normalize(chave) == alvo:
            return motivo
    return ""


def _mapa(nome: str) -> dict | None:
    alvo = facts.normalize(nome)
    for chave, destino in MAPA.items():
        if facts.normalize(chave) == alvo:
            return destino
    return None


def traduzir(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Converte itens internos para o formato do cardapio digital.

    Entrada: itens como o agente os guarda, com o sabor no `name` e o preco
    unitario oficial ja resolvido ([{"name": "Coxinha", "quantity": 50, ...}]).

    Saida: (itens_para_a_api, bloqueados). Havendo qualquer bloqueado, quem
    chama NAO deve enviar o pedido - o preco sairia errado para o cliente.
    """
    lotes: dict[str, dict] = {}   # familia -> {quantidade, sabores}
    avulsos: list[dict] = []
    bloqueados: list[str] = []

    for item in items or []:
        nome = str(item.get("name", "")).strip()
        try:
            qtd = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            qtd = 0
        if not nome or qtd <= 0:
            continue

        motivo = _bloqueio(nome)
        if motivo:
            bloqueados.append(f"{nome}: {motivo}")
            continue

        linha = _linha_da_planilha(nome)

        # Sabor de pacote: agrupa por familia e manda o sabor em selected_flavors
        if linha is not None and linha["preco"] is None:
            familia = linha["categoria"]
            if "canudinho" in facts.normalize(nome):
                destino = lotes.setdefault("Canudinhos", {"quantidade": 0, "sabores": []})
            elif familia in LOTES:
                destino = lotes.setdefault(familia, {"quantidade": 0, "sabores": []})
            else:
                bloqueados.append(f"{nome}: categoria '{familia}' sem pacote equivalente")
                continue
            destino["quantidade"] += qtd
            if nome not in destino["sabores"]:
                destino["sabores"].append(nome)
            continue

        # Item por unidade
        destino = _mapa(nome)
        if destino:
            avulsos.append({
                "name": destino["nome_base44"],
                "quantity": qtd,
                "context": destino.get("context", "unidade"),
            })
            continue

        bloqueados.append(f"{nome}: sem equivalente conferido no cardapio digital")

    saida: list[dict] = []
    for familia, dados in lotes.items():
        cfg = _CANUDINHOS if familia == "Canudinhos" else LOTES[familia]
        qtd = dados["quantidade"]
        if qtd < cfg["minimo"]:
            bloqueados.append(
                f"{familia}: {qtd} unidades, mas o cardapio digital exige no minimo "
                f"{cfg['minimo']} para esse pacote"
            )
            continue
        api_item = {
            "name": cfg["nome_base44"],
            "quantity": qtd,
            "selected_flavors": dados["sabores"],
        }
        if cfg.get("context"):
            api_item["context"] = cfg["context"]
        saida.append(api_item)

    saida.extend(avulsos)
    return saida, bloqueados
