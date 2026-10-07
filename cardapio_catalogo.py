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
            {"nome_base44": "Cento Salgadinhos Assados", "minimo": 100, "max_sabores": 4},
            {"nome_base44": "Meio Cento Salgadinhos Assados", "minimo": 50, "max_sabores": 2},
            # Porcao de 25 cadastrada pelo dono em 07/10 (R$ 30,00, 1 sabor).
            # Os sabores la tem outra grafia: o de-para do pacote traduz o
            # nome do cento para o desta lista.
            {"nome_base44": "25 uni Salgadinhos assados", "minimo": 25, "max_sabores": 1,
             "sabores": {
                 "Esfirra (carne)": "esfirra de carne",
                 "Empadinha (frango)": "empada de frango",
                 "Pão pizza (queijo presunto)": "pão pizza",
                 "Calzone de frango": "calzone de frango",
                 "Enroladinho de salsicha com Catupiry": "enroladinho de salsicha",
             }},
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
        # Cadastrado nos fritos em 07/10 (25, 50 e cento).
        "Pastel queijo": "pastel de queijo",
        "Quibe": "quibe",
        "Rissole de Carne": "rissoles de carne",
        "Misto de Queijo e Presunto": "misto de queijo e presunto",
        "Churros doce de leite": "churros",
        # O cardapio digital vende croquete de MACAXEIRA como sabor de lote
        # frito; a planilha chama de Croquete de Macaxeira e poe em Salgados
        # Grandes. Como sabor de lote, funciona.
        "Croquete de Macaxeira": "croquete macaxeira",
        # O dono cadastrou o croquete no cardapio digital como "CROQUETE
        # MACAXEIRA E CARNE" (01/10): e o mesmo sabor de lote, e o de carne
        # deixa de ser bloqueado.
        "Croquete de carne": "croquete macaxeira",
    },
    "Assados": {
        "Esfirra de Carne": "Esfirra (carne)",
        "Empadinha de Frango": "Empadinha (frango)",
        "Pão Pizza": "Pão pizza (queijo presunto)",
        "Canudinho de Frango": "Canudinho (frango)",
        "Canudinho de Camarão": "Canudinho (camarão)",
        # Com a porcao de 25 (07/10) a primeira grafia lida passou a ser a
        # dela ("calzone de frango"); o cento e o meio cento usam estas.
        "Calzone de frango": "Calzone de frango",
        "Enroladinho de salsicha com Catupiry": "Enroladinho de salsicha com Catupiry",
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
        "categoria": "Salgados Grandes", "nome_base44": "Rissoles de Carne (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Rissole de Queijo e Presunto": {
        "categoria": "Salgados Grandes", "nome_base44": "Rissole de Queijo e Presunto", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Empada de Frango": {
        "categoria": "Salgados Grandes", "nome_base44": "Empada de Frango (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Empada de Camarão": {
        "categoria": "Salgados Grandes", "nome_base44": "Empada de Camarão (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Saltenha Assada": {
        "categoria": "Salgados Grandes", "nome_base44": "Saltenha Assada", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Bomba de Carne de Sol": {
        "categoria": "Salgados Grandes", "nome_base44": "Bomba de carne de sol", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 10.00,
    },
    "Quiche de Frango": {
        "categoria": "Salgados Grandes", "nome_base44": "Quiche de Frango", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 12.00,
    },
    "Bolo de Milho": {
        "categoria": "Bolos e Doces", "nome_base44": "Bolo de Milho", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 30.00,
    },
    # Recuperados: existiam no cardapio com outro nome.
    "Coxinha de Frango com Catupiry": {
        "categoria": "Salgados Grandes", "nome_base44": "Coxinha de frango (grande)", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 9.00,
    },
    "Quiche de Carne de Sol": {
        "categoria": "Salgados Grandes", "nome_base44": "Quiche carne de sol", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 12.00,
    },
    "Guaraná Tuchaua 2 Litros": {
        "categoria": "Bebidas", "nome_base44": "Refrigerante Tuchaua 2L", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 12.00,
    },
    "Bandeja de Brigadeiros (16 un)": {
        "categoria": "Bolos e Doces", "nome_base44": "Bandeja com 16 Docinhos", "context": "unidade",
        "sabor_fixo": "Brigadeiro",
        "quantidade_teste": 1, "preco_esperado": 20.80,
    },
    # O cardapio digital nao tem cento de brigadeiro: 1 cento = 2 meios centos.
    "Cento de Brigadeiros (100 un)": {
        "categoria": "Bolos e Doces", "nome_base44": "Meio Cento de brigadeiro", "context": "unidade",
        "sabor_fixo": "Brigadeiro",
        "fator": 2, "quantidade_teste": 2, "preco_esperado": 130.00,
    },
    "Meio Cento de Brigadeiros (50 un)": {
        "categoria": "Bolos e Doces", "nome_base44": "Meio Cento de brigadeiro", "context": "unidade",
        "sabor_fixo": "Brigadeiro",
        "quantidade_teste": 1, "preco_esperado": 65.00,
    },
    # Cadastrado no cardapio digital (com sabores) - antes so existia o Tuchaua.
    "Refrigerante 2 Litros": {
        "categoria": "Bebidas", "nome_base44": "Refrigerante 2 L", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 15.00,
    },
    "Refrigerante Lata": {
        "categoria": "Bebidas", "nome_base44": "Refrigerante Lata", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 7.00,
    },
    "Refrigerante 600ml": {
        "categoria": "Bebidas", "nome_base44": "Refrigerante 600ml", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 8.00,
    },
    "Enroladinho de Salsicha": {
        "categoria": "Salgados Grandes", "nome_base44": "Enroladinho de Salsicha", "context": "unidade",
        "quantidade_teste": 1, "preco_esperado": 8.00,
    },
}


# ── Itens que a planilha vende e o cardapio digital nao tem ─────────────────
# Conferido com varias grafias; nenhuma casou com o produto certo. So o dono
# resolve, cadastrando no cardapio digital.
BLOQUEADOS = {
    "Panqueca de Carne": ("Salgados Grandes", "nao existe no cardapio digital"),
    "Panqueca de Frango": ("Salgados Grandes", "nao existe no cardapio digital"),
    "Saltenha Frita": ("Salgados Grandes", "o cardapio so tem 'Mini Saltenha Frita' (R$ 2,00, minimo 10), que e outro produto"),
    "Cento de Beijinhos (100 un)": ("Bolos e Doces", "o cardapio digital nao tem beijinho; a busca cai em brigadeiro"),
    "Meio Cento de Beijinhos (50 un)": ("Bolos e Doces", "o cardapio digital nao tem beijinho; a busca cai na bandeja de 16 docinhos"),
    "Cento Misto Brigadeiro e Beijinho (100 un)": ("Bolos e Doces", "o cardapio digital nao tem misto; a busca cai em brigadeiro"),
    "Meio Cento Misto (50 un)": ("Bolos e Doces", "o cardapio digital nao tem misto; a busca cai em brigadeiro"),
    "Suco 500ml": ("Bebidas", "o cardapio digital so tem 'Suco Natural 300 ml', de tamanho diferente"),
}


# ── Padrao + o que o dono ligou pelo painel ─────────────────────────────────
# Os dicionarios acima sao o PADRAO e nao mudam. O dono acrescenta produto
# novo ou libera um bloqueado pelo painel (config_dono.catalogo_extra); estas
# tres funcoes devolvem o padrao com a camada dele por cima. Sem nada gravado,
# devolvem exatamente SABORES, MAPA e BLOQUEADOS.

def _extras(extras: list | None = None) -> list[dict]:
    """A camada do dono. `extras` explicito e o teste do painel (entrada ainda
    nao gravada) - passado por parametro, e nao por variavel global, para um
    pedido real atendido ao mesmo tempo nunca enxergar o que nao foi salvo."""
    if extras is not None:
        return extras
    try:
        import config_dono
        return config_dono.catalogo_extra()
    except Exception as e:
        print(f"[catalogo] camada do dono ignorada: {e}")
        return []


def sabores_efetivos(extras: list | None = None) -> dict:
    extras = [e for e in _extras(extras) if e["tipo"] == "sabor"]
    if not extras:
        return SABORES
    saida = {familia: dict(sabores) for familia, sabores in SABORES.items()}
    for e in extras:
        saida.setdefault(e["familia"], {})[e["nome_planilha"]] = e["nome_cardapio"]
    return saida


def mapa_efetivo(extras: list | None = None) -> dict:
    extras = [e for e in _extras(extras) if e["tipo"] == "unidade"]
    if not extras:
        return MAPA
    saida = dict(MAPA)
    for e in extras:
        saida[e["nome_planilha"]] = {
            "categoria": e.get("categoria", ""), "nome_base44": e["nome_cardapio"],
            "context": "unidade", "fator": e.get("fator", 1),
            "quantidade_teste": 1, "preco_esperado": None,
        }
    return saida


def bloqueados_efetivos(extras: list | None = None) -> dict:
    """BLOQUEADOS menos o que o dono ligou ao cardapio digital."""
    liberados = {_norm(e["nome_planilha"]) for e in _extras(extras)}
    if not liberados:
        return BLOQUEADOS
    return {k: v for k, v in BLOQUEADOS.items() if _norm(k) not in liberados}


def _norm(texto: str) -> str:
    return facts.normalize(texto or "")


def opcoes_do_item(nome: str) -> list[str]:
    """Sabores que o cardapio digital oferece para a bebida ("Refrigerante
    Lata" -> Coca, Coca zero, ...). [] quando nao ha escolha a fazer."""
    destino = _achar(mapa_efetivo(), nome)
    if not destino or destino.get("categoria") != "Bebidas":
        return []
    try:
        import cardapio
        return cardapio.opcoes_de_sabor(destino["nome_base44"])
    except Exception:
        return []


def _achar(mapa: dict, nome: str):
    """Busca em um dicionario ignorando acento e caixa."""
    alvo = _norm(nome)
    for chave, valor in mapa.items():
        if _norm(chave) == alvo:
            return valor
    return None


def _linha_da_planilha(nome: str) -> dict | None:
    """Linha da aba CARDAPIO que corresponde ao item pedido."""
    alvo = _norm(nome)
    for p in facts.get()["produtos"]:
        if _norm(p["item"]) == alvo:
            return p
    return None


def _sabor_oficial(familia: str, nome: str, sabores: dict | None = None) -> str | None:
    """Nome do sabor como o cardapio digital o conhece."""
    achado = _achar((sabores if sabores is not None else sabores_efetivos()).get(familia, {}), nome)
    if achado:
        return achado
    # Com os dados vindos do cardapio digital (facts), a
    # linha do sabor ja traz o nome da opcao la ("Calzone de frango").
    linha = _linha_da_planilha(nome)
    if linha and linha.get("origem") and linha.get("preco") is None:
        return linha["origem"]
    return None


def _sabor_no_pacote(pacote: dict, sabor: str) -> str:
    """O sabor com a grafia do pacote, quando ele tem lista propria."""
    return _achar(pacote.get("sabores") or {}, sabor) or sabor


def _escolher_pacote(familia: str, quantidade: int) -> dict | None:
    """Maior pacote da familia que cabe nessa quantidade."""
    for pacote in LOTES[familia]["pacotes"]:
        if quantidade >= pacote["minimo"]:
            return pacote
    return None


def _sem_rotulo(nome: str, qtd: int) -> tuple[str, int]:
    """Tira o rotulo de formato do nome e converte pacotes em unidades.

    "Porção (25 un) Quibe" com quantity=1 nao e um quibe: e uma porcao, ou
    seja 25. O modelo conta PACOTES quando escreve o rotulo, e a API do
    cardapio digital so entende UNIDADES, multiplas de 25. Sem esta conversao
    o item ou era bloqueado por nome, ou passava com 1 unidade no lugar de 25.

    A quantidade so e multiplicada quando ela e menor que o lote: assim
    quantity=1 vira 25 e quantity=2 vira 50, mas quantity=25, que ja veio em
    unidades, fica como esta. Quem vier com um numero incoerente (30 com
    rotulo de 25) segue adiante e cai na regra dos multiplos, que pede ajuste
    em vez de adivinhar.
    """
    try:
        import cardapio
    except Exception:
        return nome, qtd

    limpo = cardapio.nome_sem_rotulo_de_formato(nome)
    if not limpo or limpo == nome:
        return nome, qtd

    unidades = cardapio.unidades_do_rotulo(nome)
    if unidades and qtd < unidades:
        qtd = qtd * unidades
    return limpo, qtd


def quebras_de_lote(items: list[dict]) -> list[dict]:
    """Sabores de pacote com quantidade que nao fecha no passo, com a sugestao.

    Existe porque a trava so sabia dizer QUE a quantidade estava quebrada. O
    modelo entao perguntava "quer 25 ou 50?" sem dizer de qual sabor falava — o
    cliente tinha pedido 30 coxinhas E 20 quibes e nao sabia a qual dos dois a
    pergunta se referia (teste do dono, 23/09). Aqui sai o nome do sabor e a
    quantidade ja arredondada, para a pergunta ser especifica.

    Devolve [{"sabor", "qtd", "passo", "sugerida"}].
    """
    quebras: list[dict] = []
    por_familia: dict[str, list[dict]] = {}
    for indice, item in enumerate(items or []):
        nome = str(item.get("name", "")).strip()
        try:
            qtd = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            qtd = 0
        if not nome or qtd <= 0:
            continue
        nome, qtd = _sem_rotulo(nome, qtd)

        linha = _linha_da_planilha(nome)
        if linha is None or linha["preco"] is not None:
            continue                      # nao e sabor de pacote: nao tem passo
        familia = linha["categoria"]
        if "canudinho" in _norm(nome):
            familia = "Canudinhos"
        if familia not in LOTES:
            continue

        # Nome que existe nas duas familias e esta ABAIXO do corte de festa
        # esta sendo lido como salgado grande avulso — e ali a regra de lote
        # nao vale. Sem isto, 3 rissoles (avulsos, R$ 9,00 cada) recebiam
        # "ajusto para 25?", e a Sandrinha chegou a dizer "25 unidades
        # avulsas", que seriam R$ 225,00 (teste do dono, 25/09).
        try:
            import facts
            oficial = facts.resolve_unit_price(nome, qtd)
            if (oficial and oficial.get("ambiguo")
                    and qtd < facts.QTD_MINIMA_FESTA):
                continue
        except Exception:
            pass

        cfg = LOTES[familia]
        passo = cfg["passo"]
        # O PASSO e por sabor: cada sabor tem de fechar em multiplo de 25.
        # O MINIMO e do PACOTE, sobre o total da familia — nao de cada sabor.
        # Cobrar o minimo sabor a sabor quebrava o produto mais comum da casa:
        # meio cento de assados com 2 sabores (25 + 25 = 50) virava "ajusto
        # para 50 de cada?", ou seja, 100 unidades (teste do dono, 25/09).
        if qtd % passo:
            quebras.append({
                "indice": indice,
                "familia": familia,
                "sabor": _sabor_oficial(familia, nome) or nome,
                "qtd": qtd,
                "passo": passo,
                "sugerida": max(passo, round(qtd / passo) * passo),
            })
            continue
        por_familia.setdefault(familia, []).append({
            "indice": indice,
            "familia": familia,
            "sabor": _sabor_oficial(familia, nome) or nome,
            "qtd": qtd,
            "passo": passo,
        })

    # Familia cujos sabores fecham no passo mas cujo TOTAL nao alcanca o pacote
    # minimo: 25 esfirras sozinhas nao viram meio cento. Aqui a sugestao sobe
    # o total, nao cada sabor.
    for familia, itens_ok in por_familia.items():
        cfg = LOTES[familia]
        minimo = min(p["minimo"] for p in cfg["pacotes"])
        total = sum(i["qtd"] for i in itens_ok)
        if total >= minimo:
            continue
        falta = minimo - total
        passo = cfg["passo"]
        subir = max(passo, -(-falta // passo) * passo)      # arredonda para cima
        primeiro = itens_ok[0]
        quebras.append({**primeiro, "sugerida": primeiro["qtd"] + subir})

    quebras.sort(key=lambda q: q["indice"])
    return quebras


def traduzir(items: list[dict], extras: list | None = None) -> tuple[list[dict], list[str]]:
    """Converte itens internos para o formato do cardapio digital.

    Entrada: itens como o agente os guarda, com o sabor no `name`
    ([{"name": "Coxinha frango", "quantity": 50, ...}]).

    Saida: (itens_para_a_api, bloqueados). Havendo qualquer bloqueado, quem
    chama NAO deve enviar o pedido.
    """
    lotes: dict[str, list[tuple[str, int]]] = {}  # familia -> [(sabor_oficial, qtd)]
    avulsos: list[dict] = []
    bloqueados: list[str] = []
    # Lidos uma vez por pedido: padrao + o que o dono ligou pelo painel.
    # `extras` so e passado pelo teste do painel (entrada ainda nao gravada).
    _bloqueados = bloqueados_efetivos(extras)
    _mapa = mapa_efetivo(extras)
    _sabores = sabores_efetivos(extras)

    for item in items or []:
        nome = str(item.get("name", "")).strip()
        try:
            qtd = int(float(item.get("quantity", 0) or 0))
        except (ValueError, TypeError):
            qtd = 0
        if not nome or qtd <= 0:
            continue

        # O rotulo do formato tem de sair ANTES de procurar no de-para. O
        # modelo manda o nome como ele aparece no resumo — "Porção (25 un)
        # Quibe fritos" — e o de-para so conhece "Quibe". Na bateria do dono
        # de 22/09 isso bloqueou dois pedidos inteiros (17:54 e 18:06): o
        # cliente confirmou, ouviu "nao consegui registrar" e foi para a fila
        # do humano, sem que faltasse nada no pedido alem do rotulo grudado.
        nome, qtd = _sem_rotulo(nome, qtd)

        bloqueio = _achar(_bloqueados, nome)
        if bloqueio:
            bloqueados.append(f"{nome}: {bloqueio[1]}")
            continue

        linha = _linha_da_planilha(nome)

        # Nome que existe nas DUAS familias, em quantidade pequena, e o salgado
        # grande avulso — e o de-para tem entrada propria para ele. Sem esta
        # saida, 3 rissoles (R$ 9,00 cada, pedido legitimo) batiam na regra de
        # lote e nao havia como precificar o pedido: a trava mandava "ajuste
        # para 25", que no avulso seriam R$ 225,00 (teste do dono, 25/09).
        _avulso = _achar(_mapa, nome)
        _pequeno = False
        if _avulso and linha is not None and linha["preco"] is None:
            try:
                import facts as _f
                _pequeno = qtd < _f.QTD_MINIMA_FESTA
            except Exception:
                _pequeno = False

        # Sabor de pacote
        if linha is not None and linha["preco"] is None and not _pequeno:
            familia = linha["categoria"]
            if "canudinho" in _norm(nome):
                familia = "Canudinhos"
            if familia not in LOTES:
                bloqueados.append(f"{nome}: categoria '{familia}' sem pacote equivalente")
                continue
            sabor = _sabor_oficial(familia, nome, _sabores)
            if not sabor:
                bloqueados.append(
                    f"{nome}: nao esta na lista de sabores do cardapio digital"
                )
                continue
            lotes.setdefault(familia, []).append((sabor, qtd))
            continue

        # Item por unidade
        destino = _achar(_mapa, nome)
        if (not destino and linha is not None and linha.get("origem")
                and linha.get("preco") is not None
                and linha.get("categoria") not in ("Fritos", "Assados")):
            # Produto que so o cardapio digital tem (Mini Pizza, Empada Doce...):
            # o nome dele la e o proprio destino.
            destino = {"nome_base44": linha["origem"], "context": "unidade"}
        if destino:
            api_item = {
                "name": destino["nome_base44"],
                "quantity": qtd * int(destino.get("fator", 1)),
                "context": destino.get("context", "unidade"),
            }
            # Sabor escolhido entre as opcoes do proprio cardapio digital
            # ("Coca" no Refrigerante Lata). Sem ele a comanda saia sem dizer
            # qual refrigerante mandar.
            if str(item.get("sabor") or "").strip():
                api_item["selected_flavors"] = [str(item["sabor"]).strip()]
            elif destino.get("sabor_fixo"):
                # A bandeja e o meio cento pedem o sabor no cardapio digital
                # (Brigadeiro, Beijinho, Mista); a planilha so vende brigadeiro.
                api_item["selected_flavors"] = [destino["sabor_fixo"]]
            avulsos.append(api_item)
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
                    "selected_flavors": [_sabor_no_pacote(pacote, sabor)],
                }
                if cfg.get("context"):
                    api_item["context"] = cfg["context"]
                saida.append(api_item)
            continue

        # Pacote que aceita varios sabores: tudo numa linha so, porque cada
        # sabor sozinho ficaria abaixo do minimo do pacote.
        if len(sabores) > pacote["max_sabores"]:
            # Mais sabores do que a linha aceita: vai em mais de um pacote,
            # cada um com ate max_sabores. O dono deixa escolher ate total/25
            # sabores (300 -> 12), e o pedido inteiro era recusado no envio.
            grupos = [sabores[i:i + pacote["max_sabores"]]
                      for i in range(0, len(sabores), pacote["max_sabores"])]
            linhas = []
            for grupo in grupos:
                total_g = sum(q for _, q in grupo)
                pac_g = _escolher_pacote(familia, total_g)
                if pac_g is None:
                    linhas = []
                    break
                # Sobra abaixo do cento: porcao de 25, uma linha por sabor.
                partes = ([(s, q, [_sabor_no_pacote(pac_g, s)]) for s, q in grupo]
                          if pac_g["max_sabores"] == 1
                          else [(None, total_g, [s for s, _ in grupo])])
                if len(partes[0][2]) > pac_g["max_sabores"]:
                    linhas = []
                    break
                for _s, qtd_l, sabores_l in partes:
                    linha_api = {"name": pac_g["nome_base44"], "quantity": qtd_l,
                                 "selected_flavors": sabores_l}
                    if cfg.get("context"):
                        linha_api["context"] = cfg["context"]
                    linhas.append(linha_api)
            if linhas:
                saida.extend(linhas)
                continue
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


# ── Teste de um produto antes de o painel gravar ─────────────────────────────

def _familia_na_planilha(familia: str) -> str:
    """Categoria da planilha em que o sabor dessa familia mora."""
    return "Assados" if familia == "Canudinhos" else familia


def testar_entrada(entrada: dict, ja_gravadas: list | None = None) -> dict:
    """Confere um produto do painel contra a planilha e o cardapio digital.

    {"ok": bool, "motivos": [...], "avisos": [...], "entendido_como": str}
    `motivos` impede a gravacao; `avisos` so informa. Nada e gravado aqui: o
    calculateOrder so CALCULA, nao cria pedido.

    O pedido de teste sai da mesma `traduzir` que o pedido real usa, com a
    entrada nova por cima - senao o teste mede um caminho que o agente nunca
    percorre.
    """
    import config_dono
    e = config_dono._entrada_valida(entrada)
    if not e:
        return {"ok": False, "avisos": [], "entendido_como": "",
                "motivos": ["preencha o nome na planilha, o tipo, a familia (para "
                            "sabor) e o nome no cardapio digital"]}
    motivos, avisos = [], []

    linha = _linha_da_planilha(e["nome_planilha"])
    if linha is None:
        motivos.append(f"'{e['nome_planilha']}' nao esta na planilha - cadastre la "
                       "primeiro, com o nome exato")
    elif e["tipo"] == "sabor":
        if linha["preco"] is not None:
            motivos.append("na planilha esse item tem preco proprio: e 'por unidade', "
                           "nao sabor de lote")
        elif _norm(linha["categoria"]) != _norm(_familia_na_planilha(e["familia"])):
            motivos.append(f"na planilha ele esta em '{linha['categoria']}', nao em "
                           f"'{_familia_na_planilha(e['familia'])}'")
    elif linha["preco"] is None:
        motivos.append("na planilha esse item nao tem preco proprio: e sabor de lote, "
                       "nao 'por unidade'")
    if linha is not None and not linha.get("disponivel", True):
        avisos.append("na planilha ele esta marcado NAO: continua esgotado ate voce "
                      "marcar SIM la")
    if motivos:
        return {"ok": False, "motivos": motivos, "avisos": avisos, "entendido_como": ""}

    if e["tipo"] == "sabor":
        quantidade = LOTES[e["familia"]]["quantidade_teste"]
    else:
        quantidade = 1
    outras = [x for x in (ja_gravadas or [])
              if _norm(x.get("nome_planilha", "")) != _norm(e["nome_planilha"])]
    itens_api, bloqueados = traduzir([{"name": linha["item"], "quantity": quantidade}],
                                     extras=outras + [e])
    if bloqueados or not itens_api:
        return {"ok": False, "avisos": avisos, "entendido_como": "",
                "motivos": bloqueados or ["a traducao nao gerou item nenhum"]}

    try:
        import httpx
        import cardapio
        base = cardapio._get_base_url()
        if not base:
            return {"ok": False, "avisos": avisos, "entendido_como": "",
                    "motivos": ["endereco do cardapio digital nao configurado"]}
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{base}/functions/calculateOrder",
                               json={"items": itens_api}, headers=cardapio._headers())
            data = resp.json()
    except Exception as ex:
        return {"ok": False, "avisos": avisos, "entendido_como": "",
                "motivos": [f"nao consegui falar com o cardapio digital: {ex}"]}

    item = (data.get("items") or [{}])[0] if isinstance(data, dict) else {}
    entendido = str(item.get("matchedName") or "")
    if not item.get("valid"):
        motivos.append(f"o cardapio digital recusou: {item.get('reason') or 'item nao aceito'}")
    elif e["tipo"] == "unidade" and _norm(entendido) != _norm(e["nome_cardapio"]):
        # E a armadilha registrada nos bloqueados: a busca do cardapio cai em
        # outro produto parecido ("a busca cai na lata") e o total sai certo
        # para o produto errado.
        motivos.append(f"o cardapio digital entendeu '{entendido or '?'}', e nao "
                       f"'{e['nome_cardapio']}' - confira o nome exato la")
    elif e["tipo"] == "sabor":
        opcoes = [str(o) for o in (item.get("flavorOptions") or [])]
        if opcoes and not any(_norm(o) == _norm(e["nome_cardapio"]) for o in opcoes):
            motivos.append(f"o sabor '{e['nome_cardapio']}' nao esta no pacote do cardapio "
                           f"digital. Sabores de la: {', '.join(opcoes)}")
    return {"ok": not motivos, "motivos": motivos, "avisos": avisos,
            "entendido_como": entendido,
            "subtotal_teste": float(data.get("subtotal") or 0) if isinstance(data, dict) else 0.0}
