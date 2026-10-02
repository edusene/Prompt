"""cardapio_fonte.py — os dados do pedido lidos do cardapio digital (Base44).

Ate 02/10/2026 produtos, precos, disponibilidade, bairros e horarios vinham da
PLANILHA (knowledge + painel), mas o pedido e calculado e cobrado pelo cardapio
digital. As duas fontes divergiam: 12 bairros com taxa diferente (Eldorado R$ 10
na planilha, R$ 15 no cardapio), 26 bairros que so o cardapio tinha, produtos
que a planilha nao listava (Empada Doce, Mini Pizza...) e o Refrigerante 2 L
esgotado numa e disponivel na outra.

Aqui o cardapio vira a fonte. Leitura publica da API de entidades do app:

    GET {base}/api/apps/{APP_ID}/entities/<Entidade>

Le a copia INTERNA (InternalMenuItem, InternalDeliveryFee) porque e ela que o
calculateOrder usa para cobrar: a Sandrinha informa o mesmo valor que o pedido
vai ter. A copia publica (MenuItem) so entra no relatorio de divergencia.

`montar_dados()` devolve o MESMO dicionario que facts._build() montava a partir
da planilha, com os MESMOS nomes de item que o resto do codigo ja conhece
("Coxinha frango", "Guarana Tuchaua 2 Litros", "Porcao (25 un)"). So o local
dos dados muda; produto novo do cardapio entra com o nome que tem la.

Nunca derruba o atendimento: cache de 5 minutos, ultima leitura boa guardada
no banco e, por ultimo, o retrato salvo em fixtures/.
"""

import json
import threading
import time

import httpx

APP_ID = "68f8f95fb3eb7abc71694599"
ENTIDADES = ("InternalMenuItem", "InternalDeliveryFee", "BusinessHours",
             "InternalBusinessRule")
TTL_SEG = 300.0
_SNAPSHOT_KEY = "cardapio_snapshot"

_lock = threading.Lock()
_cache: dict | None = None
_cache_em = 0.0


def _url(entidade: str) -> str:
    try:
        import cardapio
        base = cardapio._get_base_url() or cardapio.URL_CARDAPIO_PADRAO
    except Exception:
        base = "https://balbs-cardapio.base44.app"
    return f"{base.rstrip('/')}/api/apps/{APP_ID}/entities/{entidade}"


def _headers() -> dict:
    try:
        import cardapio
        return {"x-api-key": cardapio._get_api_key()}
    except Exception:
        return {}


def ler(entidade: str, timeout: float = 10.0) -> list[dict]:
    """Uma entidade do cardapio. Levanta excecao se a leitura falhar."""
    with httpx.Client(timeout=timeout) as client:
        resp = client.get(_url(entidade), headers=_headers())
    if resp.status_code != 200:
        raise RuntimeError(f"{entidade}: HTTP {resp.status_code}")
    dados = resp.json()
    if not isinstance(dados, list):
        raise RuntimeError(f"{entidade}: resposta nao e lista")
    return dados


def _salvar_snapshot(bruto: dict) -> None:
    try:
        import database
        database.set_setting(_SNAPSHOT_KEY, json.dumps(
            {"lido_em": time.time(), "dados": bruto}, ensure_ascii=False))
    except Exception as e:
        print(f"[cardapio-fonte] WARN nao salvou o snapshot: {e}")


def _snapshot_do_banco() -> dict | None:
    try:
        import database
        texto = database.get_setting(_SNAPSHOT_KEY, "") or ""
        if texto:
            return (json.loads(texto) or {}).get("dados") or None
    except Exception as e:
        print(f"[cardapio-fonte] WARN snapshot ilegivel: {e}")
    return None


def bruto(forcar: bool = False) -> dict | None:
    """{entidade: [registros]} do cardapio, com cache e snapshot.

    None quando nem o cardapio nem o snapshot estao disponiveis - quem chama
    usa a planilha.
    """
    global _cache, _cache_em
    with _lock:
        if not forcar and _cache is not None and time.monotonic() - _cache_em < TTL_SEG:
            return _cache
        try:
            novo = {e: ler(e) for e in ENTIDADES}
            if not novo["InternalMenuItem"] or not novo["InternalDeliveryFee"]:
                raise RuntimeError("cardapio veio vazio")
            _cache, _cache_em = novo, time.monotonic()
            _salvar_snapshot(novo)
            return novo
        except Exception as e:
            print(f"[cardapio-fonte] WARN leitura do cardapio falhou ({e}); "
                  f"usando a ultima leitura boa")
            if _cache is not None:
                _cache_em = time.monotonic()     # nao martela a API caida
                return _cache
            guardado = _snapshot_do_banco() or _retrato_do_repositorio()
            if guardado:
                _cache, _cache_em = guardado, time.monotonic()
                return guardado
            return None


# Ultima reserva: o retrato do cardapio salvo no repositorio. Com a planilha
# fora do sistema, e o que garante que um servidor novo, sem banco e com o
# cardapio fora do ar, ainda tenha produtos e bairros para atender.
_RETRATO = "fixtures/cardapio_snapshot_2026-10-02.json"


def _retrato_do_repositorio() -> dict | None:
    try:
        import os
        caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), _RETRATO)
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
        print(f"[cardapio-fonte] WARN usando o retrato do repositorio ({_RETRATO})")
        return dados or None
    except Exception as e:
        print(f"[cardapio-fonte] WARN retrato do repositorio ilegivel: {e}")
        return None


# ── Nomes: cardapio -> nome que o codigo ja conhece ───────────────────────────
# A planilha deu nome a tudo que o codigo usa (apelidos da extracao, mapas do
# catalogo, testes). Manter esses nomes e o que faz "so mudar o local": o preco,
# a disponibilidade e as opcoes vem do cardapio; o nome continua o mesmo.

def _n(texto: str) -> str:
    import facts
    return facts.normalize(texto or "")


# Itens por unidade / bebidas / doces / combos: nome no cardapio -> nome interno.
_NOME_DO_ITEM = {
    "coxinha de frango (grande)": "Coxinha de Frango com Catupiry",  # chaves normalizadas no uso
    "croquete": "Croquete de Macaxeira",
    "empada de camarao (grande)": "Empada de Camarão",
    "empada de frango (grande)": "Empada de Frango",
    "enroladinho de salsicha (grande)": "Enroladinho de Salsicha",
    "rissoles de carne (grande)": "Rissole de Carne",
    "rissoles de queijo e presunto": "Rissole de Queijo e Presunto",
    "bomba de carne de sol": "Bomba de Carne de Sol",
    "quiche carne de sol": "Quiche de Carne de Sol",
    "refrigerante 2 l": "Refrigerante 2 Litros",
    "refrigerante tuchaua 2l": "Guaraná Tuchaua 2 Litros",
    "cento de docinhos": "Cento de Brigadeiros (100 un)",
    "meio cento de brigadeiro": "Meio Cento de Brigadeiros (50 un)",
    "bandeja com 16 docinhos": "Bandeja de Brigadeiros (16 un)",
}

# Sabores de lote: opcao no cardapio -> nome interno do sabor.
_NOME_DO_SABOR = {
    "coxinha de frango": "Coxinha frango",
    "rissoles de carne": "Rissole de Carne",
    "bolinha de queijo": "Bolinha de Queijo",
    "pastel de carne": "Pastel carne",
    "enroladinho de salsicha": "Enroladinho de Salsicha",
    "misto de queijo e presunto": "Misto de Queijo e Presunto",
    "misto queijo e presunto": "Misto de Queijo e Presunto",
    "quibe": "Quibe",
    "croquete macaxeira": "Croquete de carne",
    "croquete macaxeira e carne": "Croquete de carne",
    "churros": "Churros doce de leite",
    # O pacote de 50 escreve os sabores com parenteses ("Coxinha (frango)").
    "coxinha frango": "Coxinha frango",
    "rissole carne": "Rissole de Carne",
    "bolinha queijo": "Bolinha de Queijo",
    "enroladinho salsicha": "Enroladinho de Salsicha",
    "pastel carne": "Pastel carne",
    "empadinha frango": "Empadinha de Frango",
    "canudinho frango": "Canudinho de Frango",
    "canudinho camarao": "Canudinho de Camarão",
    "esfirra carne": "Esfirra de Carne",
    "pao pizza queijo presunto": "Pão Pizza",
}

# Categoria do cardapio -> categoria interna (a da planilha).
_CATEGORIA = {
    "salgadinhos_fritos": "Fritos",
    "salgadinhos_assados": "Assados",
    "lanches": "Salgados Grandes",
    "assados_especiais": "Salgados Grandes",
    "combos": "Combos",
    "doces": "Bolos e Doces",
    "bebidas": "Bebidas",
}

# Rotulo do pacote pelo tamanho do lote.
_ROTULO_DO_LOTE = {25: ("Porção (25 un)", "Porção"), 50: ("Meio cento (50 un)", "Meio Cento"),
                   100: ("Cento (100 un)", "Cento")}

# Bairros que a planilha escrevia de outro jeito: o nome antigo vira apelido,
# para "Tupi" ou "Cuniae" continuarem casando.
_APELIDOS_DE_BAIRRO = {
    "cunia": ["cuniae", "cunian"],
    "tupy": ["tupi"],
    "baixa da uniao": ["baixa uniao"],
    "flodoaldo pontes pinto": ["flodoaldo p pinto", "flodoaldo"],
    "area militar e aeroporto": ["aeroporto"],
    "juscelino kubitschek jk": ["jk", "juscelino"],
    # Apelidos que a planilha tinha. "tupy/tupi" eram apelidos de Tucumanzal
    # na planilha, mas no cardapio Tupy e bairro proprio - ficaram com ele.
    "aponia": ["apunia"],
    "cidade do lobo": ["lobo"],
    "esperanca da comunidade": ["esperanca"],
    "sao joao bosco": ["joao bosco"],
    "ronaldo aragao": ["aragao"],
    "ulysses guimaraes": ["ulisses", "ulisses guimaraes", "ulysses"],
    "nova porto velho": ["nova pvh"],
}


def _mapa_normalizado(mapa: dict) -> dict:
    return {_n(k): v for k, v in mapa.items()}


def _nome_do_sabor(opcao: str) -> str:
    chave = _n(opcao)
    return (_mapa_normalizado(_NOME_DO_SABOR).get(chave)
            or opcao.strip()[:1].upper() + opcao.strip()[1:])


def _produtos(itens: list[dict]) -> list[dict]:
    produtos: list[dict] = []
    sabores_por_familia: dict[str, dict[str, bool]] = {}
    for it in itens:
        nome = str(it.get("name") or "").strip()
        if not nome:
            continue
        cat_cardapio = str(it.get("category") or "").strip()
        categoria = _CATEGORIA.get(cat_cardapio)
        if not categoria:
            # mini_churros e afins: o churros ja e sabor do lote de fritos.
            continue
        disponivel = bool(it.get("available"))
        preco = it.get("price")
        try:
            preco = float(preco) if preco is not None else None
        except (TypeError, ValueError):
            preco = None
        modo = str(it.get("sales_mode") or "")
        if categoria in ("Fritos", "Assados") and modo == "batch_25" and "canudinho" in _n(nome):
            # Pacote proprio de canudinhos (R$ 30 o lote de 25): os sabores ja
            # sao opcoes do lote de assados, como na planilha.
            continue
        if categoria in ("Fritos", "Assados") and modo == "batch_25":
            # Pacote em lote: a linha do formato tem o preco; cada sabor das
            # opcoes vira uma linha "Incluso no Pacote".
            try:
                lote = int(float(it.get("batch_size") or it.get("min_quantity") or 25))
            except (TypeError, ValueError):
                lote = 25
            rotulo, formato = _ROTULO_DO_LOTE.get(lote, (f"{nome}", "Lote"))
            if lote in _ROTULO_DO_LOTE and disponivel:
                produtos.append({"categoria": categoria, "item": rotulo, "formato": formato,
                                 "preco": preco, "preco_texto": f"{preco:.2f}" if preco else "",
                                 "disponivel": True, "origem": nome})
            familia = sabores_por_familia.setdefault(categoria, {})
            for opcao in it.get("flavor_options") or []:
                sabor = _nome_do_sabor(str(opcao))
                # Um sabor por nome: "Calzone de frango" e "Calzone de Frango"
                # vem de pacotes diferentes e sao o mesmo.
                chave = _n(sabor)
                atual = familia.get(chave)
                familia[chave] = (atual[0] if atual else sabor,
                                  (atual[1] if atual else False) or disponivel,
                                  atual[2] if atual else str(opcao).strip())
            continue
        if categoria in ("Fritos", "Assados"):
            # Bandeja/kit vendidos por unidade dentro da familia: item proprio.
            pass
        interno = _mapa_normalizado(_NOME_DO_ITEM).get(_n(nome), nome)
        rotulo_preco = str(it.get("price_unit_label") or "")
        formato = ("Combo" if categoria == "Combos" else
                   "Cento" if interno.startswith("Cento") else
                   "Meio Cento" if interno.startswith("Meio Cento") else
                   "Bandeja" if interno.startswith("Bandeja") else "Unidade")
        produtos.append({"categoria": categoria, "item": interno, "formato": formato,
                         "preco": preco, "preco_texto": f"{preco:.2f}" if preco else rotulo_preco,
                         "disponivel": disponivel, "origem": nome,
                         "opcoes": list(it.get("flavor_options") or [])})
    # Mesma ordem da planilha: pacotes e sabores de Fritos e Assados primeiro,
    # depois o resto. A busca por nome pega a PRIMEIRA linha que casa, e "Quibe"
    # existe como sabor de lote e como salgado grande - com o grande na frente,
    # 30 quibes deixavam de ser lote e a pergunta de ajuste nao citava o quibe.
    sabores_rows = []
    for familia, sabores in sabores_por_familia.items():
        for sabor, disponivel, opcao in sabores.values():
            # "origem" e o nome da opcao no cardapio digital: e o que vai em
            # selected_flavors quando o catalogo nao tem traducao manual.
            sabores_rows.append({"categoria": familia, "item": sabor, "formato": "Sabor Cento/Porção",
                                 "preco": None, "preco_texto": "Incluso no Pacote",
                                 "disponivel": disponivel, "origem": opcao})
    _ordem = {"Fritos": 0, "Assados": 1, "Salgados Grandes": 2, "Combos": 3,
              "Bolos e Doces": 4, "Bebidas": 5}
    pacotes = [p for p in produtos if p["categoria"] in ("Fritos", "Assados") and p["formato"] != "Unidade"]
    resto = [p for p in produtos if p not in pacotes]
    produtos = sorted(pacotes, key=lambda p: _ordem.get(p["categoria"], 9)) +         sorted(sabores_rows, key=lambda p: _ordem.get(p["categoria"], 9)) +         sorted(resto, key=lambda p: _ordem.get(p["categoria"], 9))
    # Porcao de 25 dos assados: o cardapio vende a partir de 50, mas o preco
    # unitario e o mesmo - a linha so documenta o valor por porcao.
    return produtos


def _bairros(taxas: list[dict]) -> list[dict]:
    saida = []
    for t in taxas:
        if t.get("available") is False:
            continue
        nome = str(t.get("bairro") or "").strip()
        try:
            taxa = float(t.get("valor"))
        except (TypeError, ValueError):
            continue
        if not nome:
            continue
        saida.append({"nome": nome, "taxa": taxa,
                      "aliases": list(_mapa_normalizado(_APELIDOS_DE_BAIRRO).get(_n(nome), []))})
    return saida


_DIAS = ("Domingo", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado")


def _horarios(horas: list[dict]) -> list[dict]:
    saida = []
    for h in sorted(horas, key=lambda x: float(x.get("day_of_week") or 0)):
        dia = _DIAS[int(float(h.get("day_of_week") or 0)) % 7]
        if not h.get("is_open"):
            saida.append({"chave": dia, "valor": "Fechado", "obs": ""})
            continue
        saida.append({"chave": dia,
                      "valor": f"{h.get('open_time')} as {h.get('close_time')}",
                      "obs": f"intervalo {h.get('lunch_break_start')} as {h.get('lunch_break_end')}"})
    return saida


def _regras(regras: list[dict]) -> list[dict]:
    return [{"chave": r.get("rule_key", ""), "valor": str(r.get("rule_value") or ""),
             "obs": r.get("description", "")}
            for r in regras if r.get("active", True)]


def montar_dados(dados_brutos: dict | None = None) -> dict | None:
    """O dicionario de facts._build(), a partir do cardapio. None se indisponivel."""
    b = dados_brutos if dados_brutos is not None else bruto()
    if not b:
        return None
    regras = _regras(b.get("InternalBusinessRule") or [])
    chave_pix = next((r["valor"] for r in regras if r["chave"] == "chave_pix"), "")
    return {
        "bairros": _bairros(b.get("InternalDeliveryFee") or []),
        "produtos": _produtos(b.get("InternalMenuItem") or []),
        "horarios": _horarios(b.get("BusinessHours") or []),
        "pagamento": ([{"chave": "Pix", "valor": "Chave Pix CNPJ", "obs": chave_pix}]
                      if chave_pix else []),
        "regras": regras,
        "politicas": [],
        "fonte": "cardapio",
    }


def texto_para_prompt(dados: dict) -> str:
    """Os dados do cardapio no formato de tabela que a planilha tinha no prompt.

    No lugar do texto da planilha, que ia para o modelo (ele
    leria a taxa antiga do Eldorado, por exemplo); vai este, montado do cardapio.
    """
    linhas = ["=== CARDAPIO (cardapio digital) ===",
              "Categoria | Item | Formato | Preco | Disponivel"]
    for p in dados.get("produtos") or []:
        preco = f"{p['preco']:.2f}" if p.get("preco") is not None else (p.get("preco_texto") or "")
        opcoes = f" | opcoes: {', '.join(p['opcoes'])}" if p.get("opcoes") else ""
        linhas.append(f"{p['categoria']} | {p['item']} | {p['formato']} | {preco} | "
                      f"{'SIM' if p.get('disponivel', True) else 'NAO'}{opcoes}")
    linhas += ["", "=== BAIRROS E TAXAS DE ENTREGA (cardapio digital) ===", "Bairro | Taxa"]
    linhas += [f"{b['nome']} | {b['taxa']:.2f}" for b in dados.get("bairros") or []]
    linhas += ["", "=== HORARIOS (cardapio digital) ===", "Dia | Horario | Obs"]
    linhas += [f"{h['chave']} | {h['valor']} | {h['obs']}" for h in dados.get("horarios") or []]
    linhas += ["", "=== REGRAS (cardapio digital) ===", "Regra | Valor"]
    linhas += [f"{r['chave']} | {r['valor']}" for r in dados.get("regras") or []]
    return "\n".join(linhas)


def tempo_de_preparo(unidades: int) -> int | None:
    """Minutos de preparo pela regra tempo_preparo do cardapio.

    "Ate 100 unidades: ~30 min. Ate 500 unidades: ~40 min. 1000+ unidades:
    encaminhar..." -> 30 para ate 100, 40 para ate 500. None sem a regra.
    """
    import re
    texto = regra("tempo_preparo", "")
    faixas = sorted((int(a), int(b)) for a, b in
                    re.findall(r"at[eé]\s*(\d+)\s*unidades?\s*:\s*~?\s*(\d+)\s*min", texto, re.IGNORECASE))
    for limite, minutos in faixas:
        if unidades <= limite:
            return minutos
    return faixas[-1][1] if faixas else None


def regra(chave: str, padrao: str = "") -> str:
    """Valor de uma InternalBusinessRule (endereco_retirada, chave_pix...)."""
    b = bruto()
    for r in (b or {}).get("InternalBusinessRule") or []:
        if r.get("rule_key") == chave and r.get("active", True):
            return str(r.get("rule_value") or padrao)
    return padrao


def expediente() -> dict | None:
    """{dia_da_semana(0=domingo): (abre, ini_almoco, fim_almoco, fecha) ou None}."""
    b = bruto()
    if not b or not b.get("BusinessHours"):
        return None

    def _hm(texto):
        try:
            h, m = str(texto).split(":")[:2]
            return int(h), int(m)
        except Exception:
            return None
    saida = {}
    for h in b["BusinessHours"]:
        dia = int(float(h.get("day_of_week") or 0)) % 7
        if not h.get("is_open"):
            saida[dia] = None
            continue
        saida[dia] = (_hm(h.get("open_time")), _hm(h.get("lunch_break_start")),
                      _hm(h.get("lunch_break_end")), _hm(h.get("close_time")))
    return saida
