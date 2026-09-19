"""
facts.py — Fatos estruturados extraidos da planilha oficial (cardapio, bairros,
horarios, pagamento, regras).

Motivacao: precos e taxas de frete NUNCA devem depender de o modelo "ler certo"
um trecho de texto recuperado. Aqui os dados viram tabelas em memoria com busca
exata (normalizacao + apelidos + similaridade), usadas por:
  - tools consultar_frete / consultar_preco (o agente consulta, nao inventa);
  - cardapio.calcular_pedido (calculo do pedido);
  - blocos compactos injetados no system prompt.
"""

import re
import unicodedata
from difflib import SequenceMatcher

DADOS_FILENAME = "PLANILHA CARDAPIO E BAIRROS EDUARDO.xlsx"

_cache: dict | None = None


# ── Normalizacao ─────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """minusculas, sem acento, sem pontuacao, espacos colapsados."""
    text = (text or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _to_float(value) -> float | None:
    """'R$ 12,50' -> 12.5 ; 'Incluso no Pacote' -> None."""
    if value is None:
        return None
    text = str(value).strip().replace("R$", "").strip()
    if text.count(",") and text.count("."):
        text = text.replace(".", "")
    text = text.replace(",", ".")
    match = re.fullmatch(r"-?\d+(?:\.\d+)?", text)
    return float(match.group(0)) if match else None


# ── Leitura da planilha ──────────────────────────────────────────────────────

def _load_raw() -> str:
    try:
        import knowledge_rag
        return knowledge_rag.get_file_content(DADOS_FILENAME)
    except Exception as e:
        print(f"[facts] WARN nao foi possivel ler {DADOS_FILENAME}: {e}")
        return ""


def _split_sections(text: str) -> dict:
    """Divide o texto em {nome_da_aba: [linhas]}."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("===") and stripped.endswith("==="):
            current = stripped.strip("= ").strip()
            sections[current] = []
        elif current and stripped:
            sections[current].append(stripped)
    return sections


def _find_section(sections: dict, *keywords: str) -> list[str]:
    for name, lines in sections.items():
        norm = normalize(name)
        if any(normalize(kw) in norm for kw in keywords):
            return lines
    return []


def _cells(line: str) -> list[str]:
    """Quebra uma linha de registro em celulas (formato 'a | b | c')."""
    if "|" in line:
        return [c.strip() for c in line.split("|")]
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    return [line.strip()]


def _build() -> dict:
    raw = _load_raw()
    sections = _split_sections(raw)

    # ── Bairros / frete ──────────────────────────────────────────────────
    bairros = []
    for line in _find_section(sections, "bairro", "taxa", "frete"):
        cells = _cells(line)
        if len(cells) < 2:
            continue
        nome = cells[0].strip(" ,;.")
        taxa = _to_float(cells[1])
        if not nome or taxa is None:
            continue
        if normalize(nome) in ("taxas dos bairros", "bairro", "bairros"):
            continue
        aliases = []
        for extra in cells[2:]:
            aliases.extend(a.strip() for a in extra.split(",") if a.strip())
        bairros.append({"nome": nome, "taxa": taxa, "aliases": aliases})

    # ── Cardapio / precos ────────────────────────────────────────────────
    produtos = []
    for line in _find_section(sections, "cardapio", "produto", "preco"):
        cells = _cells(line)
        if len(cells) < 3:
            continue
        categoria, item, formato = cells[0], cells[1], cells[2]
        preco = _to_float(cells[3]) if len(cells) > 3 else None
        preco_texto = cells[3] if len(cells) > 3 else ""
        disponivel = (cells[4].strip().upper() != "NAO") if len(cells) > 4 else True
        if normalize(categoria) in ("categoria", "") or not item:
            continue
        produtos.append({
            "categoria": categoria,
            "item": item,
            "formato": formato,
            "preco": preco,
            "preco_texto": preco_texto,
            "disponivel": disponivel,
        })

    # ── Demais abas: registros chave/valor ───────────────────────────────
    def _key_values(*keywords) -> list[dict]:
        out = []
        lines = _find_section(sections, *keywords)
        header = None
        for line in lines:
            cells = _cells(line)
            if header is None:
                header = cells
                continue
            if len(cells) < 2:
                continue
            out.append({
                "chave": cells[0],
                "valor": cells[1],
                "obs": cells[2] if len(cells) > 2 else "",
            })
        return out

    return {
        "bairros": bairros,
        "produtos": produtos,
        "horarios": _key_values("horario"),
        "pagamento": _key_values("pix", "pagamento"),
        "regras": _key_values("regras"),
        "politicas": _key_values("politica"),
    }


def get() -> dict:
    global _cache
    if _cache is None:
        _cache = _build()
        print(
            f"[facts] carregado: {len(_cache['bairros'])} bairros, "
            f"{len(_cache['produtos'])} produtos, "
            f"{len(_cache['horarios'])} regras de horario"
        )
    return _cache


def invalidate() -> None:
    """Chamado sempre que o knowledge muda."""
    global _cache
    _cache = None


def is_loaded() -> bool:
    dados = get()
    return bool(dados["bairros"] or dados["produtos"])


# ── Consultas ────────────────────────────────────────────────────────────────

def lookup_frete(bairro: str) -> dict | None:
    """Taxa de entrega de um bairro. None quando o bairro nao esta na tabela.

    Casa por nome exato, apelido cadastrado, prefixo e, por ultimo,
    similaridade alta (>= 0.86) para tolerar erro de digitacao.
    """
    alvo = normalize(bairro)
    if not alvo:
        return None

    bairros = get()["bairros"]
    if not bairros:
        return None

    for b in bairros:
        if normalize(b["nome"]) == alvo or any(normalize(a) == alvo for a in b["aliases"]):
            return {"bairro": b["nome"], "taxa": b["taxa"], "match": "exato"}

    for b in bairros:
        candidatos = [normalize(b["nome"])] + [normalize(a) for a in b["aliases"]]
        for candidato in candidatos:
            if not candidato or min(len(candidato), len(alvo)) < 4:
                continue
            if candidato.startswith(alvo) or alvo.startswith(candidato):
                return {"bairro": b["nome"], "taxa": b["taxa"], "match": "parcial"}

    melhor, score = None, 0.0
    for b in bairros:
        for candidato in [b["nome"]] + b["aliases"]:
            ratio = SequenceMatcher(None, alvo, normalize(candidato)).ratio()
            if ratio > score:
                melhor, score = b, ratio
    if melhor and score >= 0.86:
        return {
            "bairro": melhor["nome"],
            "taxa": melhor["taxa"],
            "match": f"aproximado({score:.2f})",
        }

    return None


def lookup_preco(produto: str, formato: str = "") -> list[dict]:
    """Itens do cardapio que casam com o texto informado (melhores primeiro)."""
    alvo = normalize(produto)
    if not alvo:
        return []

    alvo_formato = normalize(formato)
    termos = [t for t in alvo.split() if len(t) >= 3]
    resultados = []
    for p in get()["produtos"]:
        nome = normalize(p["item"])
        categoria = normalize(p["categoria"])
        completo = f"{categoria} {nome} {normalize(p['formato'])}"

        if alvo == nome:
            score = 1.0
        elif alvo in nome or nome in alvo:
            score = 0.85
        elif alvo in categoria:
            score = 0.7
        else:
            score = SequenceMatcher(None, alvo, nome).ratio()

        # Consultas que misturam categoria e formato ("porcao assados",
        # "cento de fritos") nao casam com o nome do item sozinho.
        if termos:
            cobertos = sum(1 for t in termos if t in completo)
            if cobertos == len(termos):
                score = max(score, 0.9)
            elif cobertos:
                score = max(score, 0.6 + 0.2 * cobertos / len(termos))

        if alvo_formato and alvo_formato in normalize(p["formato"]):
            score += 0.1
        if score >= 0.6:
            resultados.append((score, p))

    resultados.sort(key=lambda r: -r[0])
    selecionados = [p for _, p in resultados[:8]]

    # Sabores nao tem preco proprio ("Incluso no Pacote"): quem tem preco e o
    # formato da categoria (Porcao/Meio Cento/Cento). Sem isso o agente recebia
    # "incluso no pacote" e ficava sem valor nenhum para informar.
    categorias_sem_preco = {
        p["categoria"] for p in selecionados if p["preco"] is None
    }
    if categorias_sem_preco:
        ja_listados = {(p["categoria"], p["item"]) for p in selecionados}
        for p in get()["produtos"]:
            if (
                p["preco"] is not None
                and p["categoria"] in categorias_sem_preco
                and (p["categoria"], p["item"]) not in ja_listados
            ):
                selecionados.append(p)

    return selecionados[:12]


def bairro_names() -> list[str]:
    return [b["nome"] for b in get()["bairros"]]


_UNIDADES_RE = re.compile(r"(\d+)\s*un", re.IGNORECASE)


def _unidades_do_formato(item: dict) -> int:
    """Quantas unidades cabem na linha de preco ('Porcao (25 un)' -> 25)."""
    match = _UNIDADES_RE.search(item["item"]) or _UNIDADES_RE.search(item["formato"])
    return int(match.group(1)) if match else 1


def preco_unitario_da_categoria(categoria: str) -> tuple[float | None, str]:
    """Preco por unidade de uma categoria vendida em lote (Fritos, Assados).

    Fritos: porcao de 25 por R$ 20,00 -> R$ 0,80 a unidade. O sabor nao tem
    preco proprio no cardapio ("Incluso no Pacote"), entao e daqui que sai o
    valor de '50 coxinhas'.
    """
    alvo = normalize(categoria)
    melhor = None
    origem = ""
    for p in get()["produtos"]:
        if normalize(p["categoria"]) != alvo or p["preco"] is None:
            continue
        unidades = _unidades_do_formato(p)
        if unidades <= 1:
            continue
        unitario = p["preco"] / unidades
        if melhor is None or unitario < melhor:
            melhor = unitario
            origem = f"{p['item']} = {_brl(p['preco'])} ({unidades} un)"
    return melhor, origem


def resolve_unit_price(nome: str, quantity: int = 1) -> dict | None:
    """Preco unitario oficial de um item do pedido.

    Existe porque o modelo manda (ou omite) o preco na tool calculate_order — e
    um pedido inteiro ja saiu com subtotal R$ 0,00 por causa disso. Aqui o valor
    vem sempre do cardapio.

    Retorna {"preco_unitario", "item_oficial", "base"} ou None se o item nao
    estiver no cardapio.
    """
    candidatos = lookup_preco(nome)
    if not candidatos:
        return None

    alvo = normalize(nome)

    def _como_sabor(p: dict, exato: bool) -> dict | None:
        unitario, origem = preco_unitario_da_categoria(p["categoria"])
        if unitario is None:
            return None
        return {
            "preco_unitario": round(unitario, 2),
            "item_oficial": p["item"],
            "base": f"{p['categoria']}: {origem}",
            "exato": exato,
        }

    def _como_linha(p: dict, exato: bool) -> dict:
        return {
            "preco_unitario": p["preco"],
            "item_oficial": p["item"],
            "base": f"{p['item']} ({p['formato']}) = {_brl(p['preco'])}",
            "exato": exato,
        }

    # 1) nome ambiguo: existe como sabor de lote E como salgado grande avulso
    #    ("coxinha" e "rissole de carne" sao os dois). Quem decide e a
    #    quantidade: a partir de 25 e lote, abaixo disso e avulso. Esta checagem
    #    vem antes do match exato justamente porque o nome bate dos dois lados.
    sabores = [p for p in candidatos if p["preco"] is None and alvo in normalize(p["item"])]
    avulsos = [
        p for p in candidatos
        if p["preco"] is not None and "unidade" in normalize(p["formato"])
        and alvo in normalize(p["item"])
    ]

    if sabores and avulsos:
        escolhido = _como_sabor(sabores[0], False) if quantity >= 25 else _como_linha(avulsos[0], False)
        if escolhido:
            outro = avulsos[0] if quantity >= 25 else sabores[0]
            escolhido["ambiguo"] = True
            escolhido["alternativa"] = f"{outro['item']} ({outro['categoria']})"
            return escolhido

    # 2) nome bate exatamente com uma linha do cardapio
    for p in candidatos:
        if normalize(p["item"]) != alvo:
            continue
        if p["preco"] is not None:
            return _como_linha(p, True)
        resolvido = _como_sabor(p, True)
        if resolvido:
            return resolvido

    if sabores:
        resolvido = _como_sabor(sabores[0], False)
        if resolvido:
            return resolvido

    # 3) qualquer linha com preco definido
    for p in candidatos:
        if p["preco"] is not None:
            return _como_linha(p, False)

    # 4) sabor aproximado: preco por unidade da categoria dele
    for p in candidatos:
        resolvido = _como_sabor(p, False)
        if resolvido:
            return resolvido

    return None


def _brl(value: float) -> str:
    return ("R$ %.2f" % value).replace(".", ",")


# ── Blocos compactos para o system prompt ────────────────────────────────────

def render_tabela_fretes() -> str:
    """Tabela de fretes, uma linha por bairro (curta e exata)."""
    bairros = get()["bairros"]
    if not bairros:
        return ""
    linhas = [f"{b['nome']}: {_brl(b['taxa'])}" for b in bairros]
    return "TAXAS DE ENTREGA POR BAIRRO (valores oficiais):\n" + "\n".join(linhas)


def render_tabela_precos() -> str:
    """Precos com valor numerico, agrupados por categoria."""
    dados = get()
    com_preco = [p for p in dados["produtos"] if p["preco"] is not None]
    if not com_preco:
        return ""

    por_categoria: dict[str, list[str]] = {}
    for p in com_preco:
        por_categoria.setdefault(p["categoria"], []).append(
            f"{p['item']} = {_brl(p['preco'])}"
        )

    blocos = ["PRECOS OFICIAIS (nunca informe preco fora desta tabela):"]
    for categoria, itens in por_categoria.items():
        blocos.append(f"- {categoria}: " + "; ".join(itens))

    sabores: dict[str, list[str]] = {}
    for p in dados["produtos"]:
        if p["preco"] is None and p["item"]:
            sabores.setdefault(p["categoria"], []).append(p["item"])
    for categoria, itens in sabores.items():
        blocos.append(f"- Sabores de {categoria} (inclusos no pacote): " + "; ".join(itens))

    return "\n".join(blocos)


def render_operacao() -> str:
    """Horarios e formas de pagamento em formato compacto."""
    dados = get()
    blocos = []
    if dados["horarios"]:
        blocos.append("HORARIOS OFICIAIS:")
        for h in dados["horarios"]:
            extra = f" ({h['obs']})" if h["obs"] else ""
            blocos.append(f"- {h['chave']}: {h['valor']}{extra}")
    if dados["pagamento"]:
        blocos.append("PAGAMENTO:")
        vistos = set()
        for pg in dados["pagamento"]:
            chave = (normalize(pg["chave"]), normalize(pg["valor"]))
            if chave in vistos:
                continue
            vistos.add(chave)
            extra = f" | {pg['obs']}" if pg["obs"] else ""
            blocos.append(f"- {pg['chave']}: {pg['valor']}{extra}")
    return "\n".join(blocos)
