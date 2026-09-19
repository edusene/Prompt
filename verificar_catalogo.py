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
ARQUIVO_CATALOGO = Path(__file__).parent / "cardapio_catalogo.py"


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


def _atualizar_preco(chave: str, tipo: str, novo_preco: float) -> bool:
    """Reescreve preco_esperado de um mapeamento ja declarado, no arquivo.

    Mexe so no numero: a chave e o nome do produto ficam como estao.
    """
    # newline="" nos dois lados: sem isso a reescrita troca a quebra de linha do
    # arquivo inteiro e o diff do git fica ilegivel por causa de dois precos.
    with open(ARQUIVO_CATALOGO, encoding="utf-8", newline="") as fh:
        texto = fh.read()
    # Bloco da entrada: da chave ate a primeira chave de fechamento.
    padrao = re.compile(
        r'("' + re.escape(chave) + r'":\s*\{.*?"preco_esperado":\s*)([\d.]+)',
        re.DOTALL,
    )
    novo_texto, n = padrao.subn(
        lambda m: f"{m.group(1)}{novo_preco:.2f}", texto, count=1
    )
    if not n:
        return False
    # PRECO_CARDAPIO, quando o item estiver la, acompanha o mesmo valor.
    padrao_preco = re.compile(
        r'("' + re.escape(chave) + r'":\s*)([\d.]+)(,\s*#[^\n]*\n)'
    )
    novo_texto = padrao_preco.sub(
        lambda m: f"{m.group(1)}{novo_preco:.2f}{m.group(3)}", novo_texto
    )
    with open(ARQUIVO_CATALOGO, "w", encoding="utf-8", newline="") as fh:
        fh.write(novo_texto)
    return True


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


def main(corrigir: bool = False) -> int:
    url = _url()
    print(f"Conferindo o cardapio digital em {url}")
    print(f"Modo: {'VERIFICAR E CORRIGIR PRECO' if corrigir else 'somente verificar'}\n")

    ok, divergentes, corrigidos = [], [], []
    for caso in _casos():
        r = consultar(url, caso["nome_api"], caso["quantidade"], caso["context"])
        if r.get("erro"):
            divergentes.append((caso, f"erro de rede: {r['erro']}"))
            continue
        if not r["valido"]:
            divergentes.append((caso, r["motivo"] or "item invalido"))
            continue
        if abs(r["subtotal"] - caso["preco_esperado"]) > 0.01:
            if corrigir and _atualizar_preco(caso["chave"], caso["tipo"], r["subtotal"]):
                corrigidos.append(
                    f"{caso['rotulo']}: R$ {caso['preco_esperado']:.2f} -> "
                    f"R$ {r['subtotal']:.2f}"
                )
            else:
                divergentes.append((
                    caso,
                    f"preco diferente: cardapio R$ {r['subtotal']:.2f}, "
                    f"esperado R$ {caso['preco_esperado']:.2f} "
                    f"(casou com '{r['casou_com']}')",
                ))
            continue
        ok.append((caso, r))

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

    if cardapio_catalogo.BLOQUEADOS:
        print(f"\n=== {len(cardapio_catalogo.BLOQUEADOS)} item(ns) BLOQUEADOS "
              f"(o agente nao fecha pedido com eles) ===")
        for chave, motivo in sorted(cardapio_catalogo.BLOQUEADOS.items()):
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
    if cardapio_catalogo.BLOQUEADOS:
        print(f"Os {len(cardapio_catalogo.BLOQUEADOS)} bloqueados acima dependem do "
              f"dono cadastrar no cardapio digital.")
    return 0


def _itens_sem_tratamento() -> list[str]:
    """Produtos da planilha sem mapa, sem bloqueio e sem pacote equivalente."""
    conhecidos = {facts.normalize(k) for k in cardapio_catalogo.MAPA}
    conhecidos |= {facts.normalize(k) for k in cardapio_catalogo.BLOQUEADOS}
    for sabores in cardapio_catalogo.SABORES.values():
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
