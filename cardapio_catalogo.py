"""
cardapio_catalogo.py — De-para entre a planilha oficial e o cardapio digital.

Os dois catalogos sao mantidos separados e divergem em varios pontos. A planilha
manda no que o agente oferece; o cardapio digital manda no que aceita receber.
Este modulo traduz de um para o outro e, quando nao ha traducao confiavel,
BLOQUEIA o item em vez de deixar sair pedido com preco ou produto errado.

Tudo aqui foi conferido chamando calculateOrder de verdade. Use
verificar_catalogo.py para reconferir sempre que o cardapio mudar.

Como o cardapio digital modela os lotes: o pacote e o produto e o sabor vai em
`selected_flavors`, com nomes proprios que a API devolve no campo
`flavorOptions`. Mandar o nome da planilha ("Esfirra de Carne") no lugar do nome
do cardapio ("Esfirra (carne)") era o que fazia os assados parecerem ausentes.

A quantidade e sempre em UNIDADES e deve ser multipla de 25; o preco acompanha
proporcionalmente.
"""

import facts

# Categorias da planilha que nao viram item de pedido.
CATEGORIAS_IGNORADAS = {"Combos"}

_FORMATOS = {"Porção (25 un)", "Meio cento (50 un)", "Cento (100 un)"}


# ── Lotes ────────────────────────────────────────────────────────────────────
# Cada familia tem um ou mais pacotes no cardapio digital. `max_sabores` e
# `minimo` vem da resposta da API (maxFlavors / minOrder) e mudam como as linhas
# sao montadas: com max_sabores=1 vai uma linha por sabor; acima disso os
# sabores dividem a mesma linha.
LOTES = {
    "Fritos": {
        "preco_unitario": 0.80,
        "passo": 25,
        "pacotes": [
            # O cento aceita ate 4 sabores na mesma linha e e o unico que tem
            # churros; abaixo de 100 so existe o pacote de 25, de um sabor so.
            {"nome_base44": "Cento de salgadinhos fritos", "minimo": 100, "max_sabores": 4},
            {"nome_base44": "25 uni Salgadinhos fritos", "minimo": 25, "max_sabores": 1},
        ],
        "quantidade_teste": 50,
        "preco_esperado": 40.00,
    },
    "Assados": {
        "preco_unitario": 1.20,
        "passo": 25,
        "pacotes": [
            # O cardapio digital NAO tem porcao de 25 de assados: o minimo e 50.
            {"nome_base44": "Cento Salgadinhos Assados", "minimo": 100, "max_sabores": 4},
            {"nome_base44": "Meio Cento Salgadinhos Assados", "minimo": 50, "max_sabores": 2},
        ],
        "quantidade_teste": 100,
        "preco_esperado": 120.00,
    },
    # Canudinho e Assado na planilha, mas no cardapio digital e produto proprio
    # com preco de porcao de 25 - e a regra do dono diz que so sai em 25.
    "Canudinhos": {
        "preco_unitario": 1.20,
        "passo": 25,
        "context": "lote",
        "pacotes": [
            {"nome_base44": "Canudinhos", "minimo": 25, "max_sabores": 1},
        ],
        "quantidade_teste": 25,
        "preco_esperado": 30.00,
    },
}


# ── Sabores: nome na planilha -> nome no flavorOptions do cardapio ───────────
# Extraidos da propria API, nao de palpite.
SABORES = {
    "Fritos": {
        "Coxinha frango": "coxinha de frango",
        "Bolinha de Queijo": "bolinha de queijo",
        "Enroladinho de Salsicha": "enroladinho de salsicha",
        "Pastel carne": "pastel de carne",
        "Quibe": "quibe",
        "Rissole de Carne": "rissoles de carne",
        "Misto de Queijo e Presunto": "misto de queijo e presunto",
        "Churros doce de leite": "churros",
        # O cardapio digital vende croquete de MACAXEIRA como sabor de lote
        # frito; a planilha chama de Croquete de Macaxeira e poe em Salgados
        # Grandes. Como sabor de lote, funciona.
        "Croquete de Macaxeira": "croquete macaxeira",
    },
    "Assados": {
        "Esfirra de Carne": "Esfirra (carne)",
        "Empadinha de Frango": "Empadinha (frango)",
        "Pão Pizza": "Pão pizza (queijo presunto)",
        "Canudinho de Frango": "Canudinho (frango)",
        "Canudinho de Camarão": "Canudinho (camarão)",
    },
    "Canudinhos": {
        "Canudinho de Frango": "Canudinho (frango)",
        "Canudinho de Camarão": "Canudinho (camarão)",
    },
}


# ── Itens vendidos por unidade ───────────────────────────────────────────────
# `fator`: quantas unidades do cardapio digital equivalem a 1 da planilha. Usado
# quando o cardapio nao tem o mesmo tamanho de embalagem.
MAPA = {
    "Rissole de Carne": {
        "nome_base44": "Rissoles de Carne (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
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
    # Recuperados: existiam no cardapio com outro nome.
    "Coxinha de Frango com Catupiry": {
        "nome_base44": "Coxinha de frango (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Quiche de Carne de Sol": {
        "nome_base44": "Quiche carne de sol", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 12.00,
    },
    "Guaraná Tuchaua 2 Litros": {
        "nome_base44": "Refrigerante Tuchaua 2L", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 12.00,
    },
    "Bandeja de Brigadeiros (16 un)": {
        "nome_base44": "Bandeja com 16 Docinhos", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 20.80,
    },
    # O cardapio digital nao tem cento de brigadeiro: 1 cento = 2 meios centos.
    "Cento de Brigadeiros (100 un)": {
        "nome_base44": "Meio Cento de brigadeiro", "context": "unidade",
        "fator": 2, "quantidade_teste": 2, "preco_esperado": 130.00,
    },
    "Meio Cento de Brigadeiros (50 un)": {
        "nome_base44": "Meio Cento de brigadeiro", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 65.00,
    },
    "Refrigerante Lata": {
        "nome_base44": "Refrigerante Lata", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 7.00,
    },
    "Refrigerante 600ml": {
        "nome_base44": "Refrigerante 600ml", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 8.00,
    },
    "Enroladinho de Salsicha": {
        "nome_base44": "Enroladinho de Salsicha", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 8.00,
    },
}


# ── Preco do cardapio digital, quando diverge da planilha ───────────────────
# Decisao: vale o preco do CARDAPIO, que e o que o cliente ve ao abrir o menu.
# Aplicado em cardapio._normalizar_itens para o agente cotar o mesmo valor nos
# dois modos. A planilha continua divergindo ate o dono acertar; e o que o
# verificar_catalogo.py reporta.
PRECO_CARDAPIO = {
    "Enroladinho de Salsicha": 8.00,          # planilha: 9,00 (unidade grande)
    "Meio Cento de Brigadeiros (50 un)": 65.00,  # planilha: 70,00
    "Refrigerante Lata": 7.00,                # planilha: 6,00
    "Refrigerante 600ml": 8.00,               # planilha: 7,00
}


# ── Itens que a planilha vende e o cardapio digital nao tem ─────────────────
# Conferido com varias grafias; nenhuma casou com o produto certo. So o dono
# resolve, cadastrando no cardapio digital.
BLOQUEADOS = {
    "Panqueca de Carne": "nao existe no cardapio digital",
    "Panqueca de Frango": "nao existe no cardapio digital",
    "Saltenha Frita": "o cardapio so tem 'Mini Saltenha Frita' (R$ 2,00, minimo 10), que e outro produto",
    "Pastel queijo": "nao esta na lista de sabores de fritos do cardapio digital",
    "Croquete de carne": "o cardapio digital so tem croquete de MACAXEIRA como sabor de lote",
    "Cento de Beijinhos (100 un)": "o cardapio digital nao tem beijinho; a busca cai em brigadeiro",
    "Meio Cento de Beijinhos (50 un)": "o cardapio digital nao tem beijinho; a busca cai na bandeja de 16 docinhos",
    "Cento Misto Brigadeiro e Beijinho (100 un)": "o cardapio digital nao tem misto; a busca cai em brigadeiro",
    "Meio Cento Misto (50 un)": "o cardapio digital nao tem misto; a busca cai em brigadeiro",
    "Refrigerante 2 Litros": "o cardapio so tem 2L do Tuchaua; a busca cai na lata",
    "Suco 500ml": "o cardapio digital so tem 'Suco Natural 300 ml', de tamanho diferente",
}


def _norm(texto: str) -> str:
    return facts.normalize(texto or "")


def _achar(mapa: dict, nome: str):
    """Busca em um dicionario ignorando acento e caixa."""
    alvo = _norm(nome)
    for chave, valor in mapa.items():
        if _norm(chave) == alvo:
            return valor
    return None


def preco_do_cardapio(nome: str) -> float | None:
    """Preco que o cardapio digital cobra, quando difere da planilha."""
    return _achar(PRECO_CARDAPIO, nome)


def _linha_da_planilha(nome: str) -> dict | None:
    """Linha da aba CARDAPIO que corresponde ao item pedido."""
    alvo = _norm(nome)
    for p in facts.get()["produtos"]:
        if _norm(p["item"]) == alvo:
            return p
    return None


def _sabor_oficial(familia: str, nome: str) -> str | None:
    """Nome do sabor como o cardapio digital o conhece."""
    return _achar(SABORES.get(familia, {}), nome)


def _escolher_pacote(familia: str, quantidade: int) -> dict | None:
    """Maior pacote da familia que cabe nessa quantidade."""
    for pacote in LOTES[familia]["pacotes"]:
        if quantidade >= pacote["minimo"]:
            return pacote
    return None


def traduzir(items: list[dict]) -> tuple[list[dict], list[str]]:
    """Converte itens internos para o formato do cardapio digital.

    Entrada: itens como o agente os guarda, com o sabor no `name`
    ([{"name": "Coxinha frango", "quantity": 50, ...}]).

    Saida: (itens_para_a_api, bloqueados). Havendo qualquer bloqueado, quem
    chama NAO deve enviar o pedido.
    """
    lotes: dict[str, list[tuple[str, int]]] = {}  # familia -> [(sabor_oficial, qtd)]
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

        motivo = _achar(BLOQUEADOS, nome)
        if motivo:
            bloqueados.append(f"{nome}: {motivo}")
            continue

        linha = _linha_da_planilha(nome)

        # Sabor de pacote
        if linha is not None and linha["preco"] is None:
            familia = linha["categoria"]
            if "canudinho" in _norm(nome):
                familia = "Canudinhos"
            if familia not in LOTES:
                bloqueados.append(f"{nome}: categoria '{familia}' sem pacote equivalente")
                continue
            sabor = _sabor_oficial(familia, nome)
            if not sabor:
                bloqueados.append(
                    f"{nome}: nao esta na lista de sabores do cardapio digital"
                )
                continue
            lotes.setdefault(familia, []).append((sabor, qtd))
            continue

        # Item por unidade
        destino = _achar(MAPA, nome)
        if destino:
            avulsos.append({
                "name": destino["nome_base44"],
                "quantity": qtd * int(destino.get("fator", 1)),
                "context": destino.get("context", "unidade"),
            })
            continue

        bloqueados.append(f"{nome}: sem equivalente conferido no cardapio digital")

    saida: list[dict] = []
    for familia, sabores in lotes.items():
        cfg = LOTES[familia]
        passo = cfg["passo"]
        total = sum(q for _, q in sabores)

        quebrados = [f"{s} ({q} un)" for s, q in sabores if q % passo]
        if quebrados:
            bloqueados.append(
                f"{familia}: o cardapio digital so aceita multiplos de {passo} por sabor "
                f"- ajuste {', '.join(quebrados)}"
            )
            continue

        pacote = _escolher_pacote(familia, total)
        if pacote is None:
            minimo = cfg["pacotes"][-1]["minimo"]
            bloqueados.append(
                f"{familia}: {total} unidades, mas o cardapio digital exige no minimo "
                f"{minimo} nessa categoria"
            )
            continue

        if pacote["max_sabores"] == 1:
            # Uma linha por sabor: cada linha e N pacotes de um sabor so.
            for sabor, qtd in sabores:
                api_item = {
                    "name": pacote["nome_base44"],
                    "quantity": qtd,
                    "selected_flavors": [sabor],
                }
                if cfg.get("context"):
                    api_item["context"] = cfg["context"]
                saida.append(api_item)
            continue

        # Pacote que aceita varios sabores: tudo numa linha so, porque cada
        # sabor sozinho ficaria abaixo do minimo do pacote.
        if len(sabores) > pacote["max_sabores"]:
            bloqueados.append(
                f"{familia}: {len(sabores)} sabores, mas o pacote "
                f"'{pacote['nome_base44']}' aceita no maximo {pacote['max_sabores']}"
            )
            continue

        api_item = {
            "name": pacote["nome_base44"],
            "quantity": total,
            "selected_flavors": [s for s, _ in sabores],
        }
        if cfg.get("context"):
            api_item["context"] = cfg["context"]
        saida.append(api_item)

    saida.extend(avulsos)
    return saida, bloqueados
