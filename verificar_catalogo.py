"""
verificar_catalogo.py — Confere a planilha oficial contra o cardapio digital.

Os dois catalogos sao mantidos separados. Quando divergem, o agente fecha pedido
com preco que o cardapio nao reconhece — ou, pior, casa com outro produto.

Este script pergunta a propria API o que ela entende de cada item mapeado e
mostra onde os dois lados discordam. Use:

  - antes de desligar o modo teste;
  - toda vez que o cardapio digital mudar;
  - para manter o MAPA de cardapio_catalogo.py em dia.

A chamada usada e calculateOrder, que apenas calcula: nao cria pedido e nao
precisa de API key.

    python verificar_catalogo.py              # so confere e relata
    python verificar_catalogo.py --corrigir   # alem de conferir, atualiza precos

O que --corrigir faz: atualiza o PRECO de mapeamentos JA DECLARADOS quando o
cardapio digital mudou de valor. O que ele nao faz, de proposito: decidir qual
produto e qual. O casamento por aproximacao da API erra feio — "Beijinho" casa
com brigadeiro, "Coca-Cola 2L" casa com Refrigerante Lata e "Cento de
Brigadeiros" casa com o MEIO cento, metade da quantidade. Mapear produto
continua sendo decisao humana; o script sugere e voce aplica.
"""

import re
import sys
from pathlib import Path

import httpx

import cardapio_catalogo
import facts

TIMEOUT = 30


def _url() -> str:
    import cardapio
    base = cardapio._get_base_url()
    if not base:
        print("ERRO: CARDÁPIO_API_URL nao configurada.")
        sys.exit(1)
    return f"{base}/functions/calculateOrder"


def consultar(url: str, nome: str, quantidade: int, context: str = "",
              sabores: list | None = None) -> dict:
    """Pergunta a API o que ela entende deste item."""
    item = {"name": nome, "quantity": quantidade}
    if context:
        item["context"] = context
    if sabores:
        item["selected_flavors"] = sabores
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            data = client.post(url, json={"items": [item]}).json()
    except Exception as e:
        return {"erro": str(e)}
    retornado = (data.get("items") or [{}])[0]
    return {
        "valido": bool(retornado.get("valid")),
        "casou_com": retornado.get("matchedName") or "",
        "motivo": retornado.get("reason") or "",
        "subtotal": float(data.get("subtotal") or 0),
        "sabores_oficiais": retornado.get("flavorOptions") or [],
        "max_sabores": retornado.get("maxFlavors"),
        "minimo": retornado.get("minOrder"),
    }


def _casos() -> list[dict]:
    """Um caso de teste por pacote e por item avulso mapeado."""
    casos = []
    for familia, cfg in cardapio_catalogo.LOTES.items():
        # O pacote conferido e o que a propria traducao escolheria para essa
        # quantidade, senao o teste mede um pacote que o agente nunca usaria.
        pacote = cardapio_catalogo._escolher_pacote(familia, cfg["quantidade_teste"])
        casos.append({
            "rotulo": f"[pacote] {familia}",
            "chave": familia,
            "tipo": "lote",
            "nome_api": pacote["nome_base44"],
            "context": cfg.get("context", ""),
            "quantidade": cfg["quantidade_teste"],
            "preco_esperado": cfg["preco_esperado"],
        })
    for chave, destino in sorted(cardapio_catalogo.MAPA.items()):
        casos.append({
            "rotulo": chave,
            "chave": chave,
            "tipo": "unidade",
            "nome_api": destino["nome_base44"],
            "context": destino.get("context", ""),
            "quantidade": destino["quantidade_teste"],
            "preco_esperado": destino["preco_esperado"],
            # Categoria + item e a identidade real de um produto: sem ela nao da
            # para achar a linha certa na planilha quando o nome se repete.
            "categoria": destino.get("categoria", ""),
        })
    return casos


_CONECTIVOS = {"de", "da", "do", "com", "e", "em"}


def _chave_sabor(nome: str) -> frozenset:
    """Sabor reduzido ao conjunto de palavras que importam.

    O mesmo sabor e grafado diferente em cada pacote ("misto de queijo e
    presunto" no de 25, "Misto queijo e presunto" no cento). Comparar palavra
    por palavra acusava um como sabor novo do cardapio.
    """
    palavras = facts.normalize(nome).split()
    return frozenset(p for p in palavras if p not in _CONECTIVOS)


def _conferir_sabores(url: str) -> list[str]:
    """Sabores de SABORES que sumiram do cardapio, e sabores novos que surgiram."""
    achados = []
    for familia, cfg in cardapio_catalogo.LOTES.items():
        # Cada pacote tem a sua lista: churros so existe no cento de fritos e o
        # canudinho de camarao so no meio cento de assados. Conferir contra um
        # pacote so acusava sabor valido como ausente.
        oficiais_norm: dict[str, str] = {}
        for pacote in cfg["pacotes"]:
            r = consultar(url, pacote["nome_base44"], max(pacote["minimo"], 25),
                          cfg.get("context", ""))
            for s in r.get("sabores_oficiais") or []:
                oficiais_norm.setdefault(facts.normalize(s), s)
        if not oficiais_norm:
            continue
        declarados = cardapio_catalogo.SABORES.get(familia, {})

        oficiais_chaves = {_chave_sabor(s): s for s in oficiais_norm.values()}

        for da_planilha, no_cardapio in declarados.items():
            if _chave_sabor(no_cardapio) not in oficiais_chaves:
                achados.append(
                    f"{familia}: '{da_planilha}' aponta para '{no_cardapio}', "
                    f"que nao esta mais na lista do cardapio"
                )
        usados = {_chave_sabor(v) for v in declarados.values()}
        for chave, original in oficiais_chaves.items():
            if chave not in usados:
                achados.append(
                    f"{familia}: o cardapio tem o sabor '{original}', que a "
                    f"planilha nao oferece"
                )
    return achados


PLANILHA = Path(__file__).parent / "PLANILHA CARDAPIO E BAIRROS EDUARDO.xlsx"
ABA_CARDAPIO = "CARDAPIO 1"
LINHA_CABECALHO = 2   # os dados comecam na 3
COL_CATEGORIA, COL_ITEM, COL_PRECO, COL_DISPONIVEL = 1, 2, 4, 5


def _backup_planilha() -> Path:
    """Copia a planilha antes de gravar. E documento do dono."""
    import shutil
    from datetime import datetime
    destino = PLANILHA.parent / "uploads" / "backup"
    destino.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    caminho = destino / f"{PLANILHA.stem}-{carimbo}{PLANILHA.suffix}"
    shutil.copy2(PLANILHA, caminho)
    return caminho


def _achar_linha(ws, categoria: str, item: str) -> int | None:
    """Linha da planilha por categoria + item.

    O nome sozinho nao identifica: "Enroladinho de Salsicha" esta na linha 8
    como sabor de lote e na 25 como salgado grande.
    """
    alvo_cat, alvo_item = facts.normalize(categoria), facts.normalize(item)
    for linha in range(LINHA_CABECALHO + 1, ws.max_row + 1):
        cat = facts.normalize(str(ws.cell(row=linha, column=COL_CATEGORIA).value or ""))
        it = facts.normalize(str(ws.cell(row=linha, column=COL_ITEM).value or ""))
        if cat == alvo_cat and it == alvo_item:
            return linha
    return None


def _mesmo_preco(valor_celula, novo: float) -> bool:
    """A celula ja tem esse preco? Compara numero, nao texto.

    A planilha guarda preco como string ("6.00"), entao comparar direto marcaria
    toda execucao como alteracao.
    """
    try:
        return abs(float(str(valor_celula).replace(",", ".")) - novo) <= 0.001
    except (TypeError, ValueError):
        return False


def _planejar(ws, precos: list[tuple], disponibilidade: list[tuple]):
    """O que precisa mudar na planilha, sem escrever nada ainda.

    Devolve (mudancas, avisos). `mudancas` traz so o que realmente difere:
    celula que ja esta com o valor certo nao entra. E isso que deixa o
    --corrigir seguro de rodar quantas vezes quiser.
    """
    mudancas, avisos = [], []

    for categoria, item, novo in precos:
        linha = _achar_linha(ws, categoria, item)
        if linha is None:
            avisos.append(f"NAO ACHOU na planilha: {categoria} | {item}")
            continue
        celula = ws.cell(row=linha, column=COL_PRECO)
        if _mesmo_preco(celula.value, novo):
            continue
        # A planilha guarda preco como texto ("6.00"); manter o formato.
        valor = f"{novo:.2f}" if isinstance(celula.value, str) else novo
        mudancas.append((
            linha, COL_PRECO, valor,
            f"linha {linha} | {categoria} | {item}: preco {celula.value} -> {valor}",
        ))

    for categoria, item, disponivel in disponibilidade:
        linha = _achar_linha(ws, categoria, item)
        if linha is None:
            avisos.append(f"NAO ACHOU na planilha: {categoria} | {item}")
            continue
        celula = ws.cell(row=linha, column=COL_DISPONIVEL)
        atual = str(celula.value or "").strip().upper()
        valor = "SIM" if disponivel else "NAO"
        if atual == valor:
            continue
        mudancas.append((
            linha, COL_DISPONIVEL, valor,
            f"linha {linha} | {categoria} | {item}: disponivel {atual} -> {valor}",
        ))

    return mudancas, avisos


def aplicar_na_planilha(precos: list[tuple], disponibilidade: list[tuple]) -> list[str]:
    """Grava preco e disponibilidade na planilha e no knowledge.

    `precos`: [(categoria, item, novo_preco)]
    `disponibilidade`: [(categoria, item, True/False)]

    Escrever aqui, e nao num override em codigo, e o que mantem UMA fonte de
    verdade: a tool consultar_preco, a base injetada no prompt e o calculo do
    pedido leem todos desta planilha. Enquanto a correcao vivia so no calculo,
    o agente cotava R$ 6,00 e cobrava R$ 7,00.

    Nada a mudar, nada e gravado: sem backup, sem reescrever o .xlsx e sem
    regerar embeddings. Rodar de novo sem diferenca nao custa nada.
    """
    if not precos and not disponibilidade:
        return []

    import openpyxl
    import knowledge_rag
    import parse_knowledge

    wb = openpyxl.load_workbook(str(PLANILHA))
    ws = wb[ABA_CARDAPIO]
    mudancas, avisos = _planejar(ws, precos, disponibilidade)

    if not mudancas:
        wb.close()
        return avisos

    backup = _backup_planilha()
    for linha, coluna, valor, _descricao in mudancas:
        ws.cell(row=linha, column=coluna).value = valor
    wb.save(str(PLANILHA))
    wb.close()

    # Regenera o texto do knowledge a partir da planilha ja corrigida.
    # save_knowledge limpa os caches, entao tool e base injetada passam a ver o
    # valor novo sem reiniciar o app.
    texto = parse_knowledge.xlsx_to_text(str(PLANILHA))
    descricao = ""
    try:
        import database
        descricao = database.get_knowledge_file_description(PLANILHA.name) or ""
    except Exception:
        pass
    knowledge_rag.save_knowledge(PLANILHA.name, texto, description=descricao)
    facts.invalidate()

    return (
        [f"backup da planilha em {backup.name}"]
        + [texto_mudanca for *_resto, texto_mudanca in mudancas]
        + avisos
        + [f"knowledge regravado a partir da planilha ({len(texto)} chars)"]
    )


def _sugerir(url: str, nome: str) -> list[str]:
    """Candidatos do cardapio para um item sem mapa. Nunca aplica sozinho."""
    variacoes = [
        nome,
        nome.lower(),
        f"{nome} (grande)",
        re.sub(r"\s*\([^)]*\)", "", nome).strip(),
        nome.replace("Rissole", "Rissoles").replace("Pão", "Pao"),
    ]
    vistos, achados = set(), []
    for variacao in variacoes:
        if not variacao or variacao in vistos:
            continue
        vistos.add(variacao)
        r = consultar(url, variacao, 1, "unidade")
        if r.get("valido"):
            achados.append(
                f"'{variacao}' -> '{r['casou_com']}' R$ {r['subtotal']:.2f}"
            )
    return achados


_cache_planilha: dict | None = None


def precos_da_planilha() -> dict:
    """{(categoria, item) normalizados: (preco, disponivel)} lido do .xlsx.

    A comparacao tem de olhar o ARQUIVO, nao o `facts`: o facts le o documento
    do knowledge, que e gerado a partir do .xlsx. Se os dois saem de sincronia,
    comparar pelo facts olha para a fonte errada e a divergencia passa batido -
    enquanto a gravacao, que vem do .xlsx, propaga o valor errado ao knowledge.
    """
    global _cache_planilha
    if _cache_planilha is not None:
        return _cache_planilha

    import openpyxl
    wb = openpyxl.load_workbook(str(PLANILHA), read_only=True, data_only=True)
    ws = wb[ABA_CARDAPIO]
    tabela = {}
    for linha in ws.iter_rows(min_row=LINHA_CABECALHO + 1, values_only=True):
        if not linha or not linha[COL_ITEM - 1]:
            continue
        categoria = facts.normalize(str(linha[COL_CATEGORIA - 1] or ""))
        item = facts.normalize(str(linha[COL_ITEM - 1] or ""))
        try:
            preco = float(str(linha[COL_PRECO - 1]).replace(",", "."))
        except (TypeError, ValueError):
            preco = None
        disponivel = str(linha[COL_DISPONIVEL - 1] or "").strip().upper() != "NAO"
        tabela[(categoria, item)] = (preco, disponivel)
    wb.close()
    _cache_planilha = tabela
    return tabela


def _divergencia_de_preco(caso: dict, subtotal_api: float):
    """(preco_na_planilha, preco_no_cardapio) por unidade da PLANILHA, ou None.

    O `fator` importa: 1 "Cento de Brigadeiros" da planilha equivale a 2 meios
    centos do cardapio, entao o teste usa quantidade 2 e o preco de R$ 130,00
    corresponde a UMA unidade da planilha, nao a duas.
    """
    chave = (facts.normalize(caso["categoria"]), facts.normalize(caso["chave"]))
    na_planilha, _disponivel = precos_da_planilha().get(chave, (None, True))
    if na_planilha is None:
        return None
    destino = cardapio_catalogo.MAPA.get(caso["chave"], {})
    fator = int(destino.get("fator", 1)) or 1
    unidades_planilha = max(caso["quantidade"] / fator, 1)
    no_cardapio = round(subtotal_api / unidades_planilha, 2)
    na_planilha = round(na_planilha, 2)
    if abs(na_planilha - no_cardapio) <= 0.01:
        return None
    return na_planilha, no_cardapio


def main(corrigir: bool = False) -> int:
    url = _url()
    print(f"Conferindo o cardapio digital em {url}")
    print(f"Modo: {'VERIFICAR E CORRIGIR PRECO' if corrigir else 'somente verificar'}\n")

    ok, divergentes = [], []
    precos_a_gravar: list[tuple] = []
    for caso in _casos():
        r = consultar(url, caso["nome_api"], caso["quantidade"], caso["context"])
        if r.get("erro"):
            divergentes.append((caso, f"erro de rede: {r['erro']}"))
            continue
        if not r["valido"]:
            divergentes.append((caso, r["motivo"] or "item invalido"))
            continue
        if abs(r["subtotal"] - caso["preco_esperado"]) > 0.01:
            divergentes.append((
                caso,
                f"preco diferente: cardapio R$ {r['subtotal']:.2f}, "
                f"esperado R$ {caso['preco_esperado']:.2f} "
                f"(casou com '{r['casou_com']}')",
            ))
            continue
        ok.append((caso, r))

        # A planilha diverge do cardapio? Este e o desencontro que fazia o
        # agente cotar um valor e cobrar outro: a tool le a planilha, o pedido
        # usava o preco do cardapio.
        if caso["tipo"] == "unidade" and caso.get("categoria"):
            divergencia = _divergencia_de_preco(caso, r["subtotal"])
            if divergencia is not None:
                na_planilha, no_cardapio = divergencia
                if corrigir:
                    precos_a_gravar.append(
                        (caso["categoria"], caso["chave"], no_cardapio)
                    )
                else:
                    divergentes.append((
                        caso,
                        f"a PLANILHA diz R$ {na_planilha:.2f} e o cardapio cobra "
                        f"R$ {no_cardapio:.2f} - rode com --corrigir",
                    ))

    # Disponibilidade: o que esta em BLOQUEADOS vira NAO na planilha; o resto,
    # SIM. E isto que faz a trava valer para a tool e para a base injetada, e
    # nao so no calculo do pedido.
    disponibilidade: list[tuple] = []
    if corrigir:
        # Efetivos: item que o dono liberou pelo painel nao pode voltar a NAO
        # na planilha so porque ainda esta na lista padrao de bloqueados.
        for nome, (categoria, _motivo) in cardapio_catalogo.bloqueados_efetivos().items():
            disponibilidade.append((categoria, nome, False))
        for nome, destino in cardapio_catalogo.MAPA.items():
            if destino.get("categoria"):
                disponibilidade.append((destino["categoria"], nome, True))

    corrigidos = []
    if corrigir and (precos_a_gravar or disponibilidade):
        corrigidos = aplicar_na_planilha(precos_a_gravar, disponibilidade)

    print(f"=== {len(ok)} item(ns) conferem ===")
    for caso, r in ok:
        ctx = f" context={caso['context']}" if caso["context"] else ""
        print(f"  OK  {caso['rotulo']:36} -> '{caso['nome_api']}' "
              f"q={caso['quantidade']}{ctx}  R$ {r['subtotal']:.2f}")

    if corrigidos:
        print(f"\n=== {len(corrigidos)} preco(s) CORRIGIDO(S) em cardapio_catalogo.py ===")
        for linha in corrigidos:
            print(f"  ~~  {linha}")
        print("  (o produto nao foi alterado, so o valor)")

    if divergentes:
        print(f"\n=== {len(divergentes)} item(ns) DIVERGEM ===")
        for caso, motivo in divergentes:
            print(f"  !!  {caso['rotulo']:36} -> '{caso['nome_api']}'")
            print(f"      {motivo}")
        if not corrigir:
            print("  (rode com --corrigir para acertar diferencas de preco)")

    sabores = _conferir_sabores(url)
    if sabores:
        print(f"\n=== {len(sabores)} aviso(s) sobre sabores ===")
        for linha in sabores:
            print(f"  ..  {linha}")

    _bloqueados = cardapio_catalogo.bloqueados_efetivos()
    if _bloqueados:
        print(f"\n=== {len(_bloqueados)} item(ns) BLOQUEADOS "
              f"(o agente nao fecha pedido com eles) ===")
        for chave, motivo in sorted(_bloqueados.items()):
            print(f"  --  {chave}")
            print(f"      {motivo}")
            for candidato in _sugerir(url, chave):
                print(f"      sugestao: {candidato}")
        print("  As sugestoes NAO sao aplicadas sozinhas: confira quantidade e")
        print("  produto antes de mover um item para o MAPA.")

    faltando = _itens_sem_tratamento()
    if faltando:
        print(f"\n=== {len(faltando)} item(ns) da planilha sem mapa nem bloqueio ===")
        for nome in faltando:
            print(f"  ??  {nome}")

    print()
    if divergentes or faltando:
        print("NAO ligue o modo producao: ha itens divergentes ou sem tratamento.")
        return 1
    print("Todos os itens mapeados conferem com o cardapio digital.")
    if _bloqueados:
        print(f"Os {len(_bloqueados)} bloqueados acima dependem do "
              f"dono cadastrar no cardapio digital.")
    return 0


def _itens_sem_tratamento() -> list[str]:
    """Produtos da planilha sem mapa, sem bloqueio e sem pacote equivalente."""
    conhecidos = {facts.normalize(k) for k in cardapio_catalogo.mapa_efetivo()}
    conhecidos |= {facts.normalize(k) for k in cardapio_catalogo.bloqueados_efetivos()}
    for sabores in cardapio_catalogo.sabores_efetivos().values():
        conhecidos |= {facts.normalize(k) for k in sabores}

    faltando = []
    for p in facts.get()["produtos"]:
        if not p.get("disponivel"):
            continue
        if p["categoria"] in cardapio_catalogo.CATEGORIAS_IGNORADAS:
            continue
        # Linhas de formato (Porcao/Meio cento/Cento) nao sao item de pedido.
        if p["item"] in cardapio_catalogo._FORMATOS:
            continue
        if facts.normalize(p["item"]) not in conhecidos:
            faltando.append(f"{p['categoria']} | {p['item']}")
    return faltando


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)
    sys.exit(main(corrigir="--corrigir" in sys.argv))
