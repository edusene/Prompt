"""
verificar_catalogo.py — Confere a planilha oficial contra o cardapio digital.

Os dois catalogos sao mantidos separados: a planilha (aba CARDAPIO) e o cadastro
do base44. Quando divergem, o agente fecha pedido com preco que o cardapio nao
reconhece — ou pior, casa com outro produto. Exemplo real encontrado: o meio
cento de fritos (R$ 40,00 na planilha) so casa com "Combo 50 Salgadinhos Fritos
+ Refrigerante", de R$ 50,00.

Este script pergunta a propria API o que ela entende de cada item e mostra onde
os dois lados discordam. Use:

  - antes de desligar o modo teste;
  - toda vez que o cardapio digital mudar;
  - para conferir o MAPA de cardapio_catalogo.py.

A chamada usada e calculateOrder, que apenas calcula: nao cria pedido e nao
precisa de API key.

    python verificar_catalogo.py
"""

import sys

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


def consultar(url: str, nome: str, quantidade: int, context: str = "") -> dict:
    """Pergunta a API o que ela entende deste item."""
    item = {"name": nome, "quantity": quantidade}
    if context:
        item["context"] = context
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
    }


def _linhas_a_conferir() -> list[dict]:
    """Um caso de teste por pacote e por item avulso mapeado."""
    casos = []

    pacotes = dict(cardapio_catalogo.LOTES)
    pacotes["Canudinhos"] = cardapio_catalogo._CANUDINHOS
    for familia, cfg in pacotes.items():
        casos.append({
            "planilha": f"[pacote] {familia}",
            "nome_api": cfg["nome_base44"],
            "context": cfg.get("context", ""),
            "quantidade": cfg["quantidade_teste"],
            "preco_esperado": cfg["preco_esperado"],
        })

    for chave, destino in sorted(cardapio_catalogo.MAPA.items()):
        casos.append({
            "planilha": chave,
            "nome_api": destino["nome_base44"],
            "context": destino.get("context", ""),
            "quantidade": destino["quantidade_teste"],
            "preco_esperado": destino["preco_esperado"],
        })
    return casos


def main() -> int:
    url = _url()
    print(f"Conferindo o cardapio digital em {url}\n")

    ok, divergentes = [], []
    for caso in _linhas_a_conferir():
        r = consultar(url, caso["nome_api"], caso["quantidade"], caso["context"])
        if r.get("erro"):
            divergentes.append((caso, f"erro de rede: {r['erro']}"))
            continue
        if not r["valido"]:
            divergentes.append((caso, r["motivo"] or "item invalido"))
        elif abs(r["subtotal"] - caso["preco_esperado"]) > 0.01:
            divergentes.append((
                caso,
                f"preco diferente: cardapio R$ {r['subtotal']:.2f}, "
                f"planilha R$ {caso['preco_esperado']:.2f} "
                f"(casou com '{r['casou_com']}')",
            ))
        else:
            ok.append((caso, r))

    print(f"=== {len(ok)} item(ns) conferem ===")
    for caso, r in ok:
        ctx = f" context={caso['context']}" if caso["context"] else ""
        print(f"  OK  {caso['planilha']:34} -> '{caso['nome_api']}' "
              f"q={caso['quantidade']}{ctx}  R$ {r['subtotal']:.2f}")

    if divergentes:
        print(f"\n=== {len(divergentes)} item(ns) DIVERGEM ===")
        for caso, motivo in divergentes:
            print(f"  !!  {caso['planilha']:34} -> '{caso['nome_api']}'")
            print(f"      {motivo}")

    if cardapio_catalogo.BLOQUEADOS:
        print(f"\n=== {len(cardapio_catalogo.BLOQUEADOS)} item(ns) BLOQUEADOS "
              f"(o agente recusa fechar pedido com eles) ===")
        for chave, motivo in sorted(cardapio_catalogo.BLOQUEADOS.items()):
            print(f"  --  {chave}")
            print(f"      {motivo}")

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
    return 0


def _itens_sem_tratamento() -> list[str]:
    """Produtos da planilha sem mapa, sem bloqueio e sem pacote equivalente.

    Pega cadastro novo na planilha que ninguem conferiu contra o cardapio.
    """
    conhecidos = {facts.normalize(k) for k in cardapio_catalogo.MAPA}
    conhecidos |= {facts.normalize(k) for k in cardapio_catalogo.BLOQUEADOS}

    faltando = []
    for p in facts.get()["produtos"]:
        if not p.get("disponivel"):
            continue
        if p["categoria"] in cardapio_catalogo.CATEGORIAS_IGNORADAS:
            continue
        # Linhas de formato (Porcao/Meio cento/Cento) nao sao item de pedido:
        # o preco delas ja esta no pacote.
        if p["item"] in cardapio_catalogo._FORMATOS:
            continue
        # Sabor de pacote e coberto pelo mapeamento da familia (LOTES).
        if p["preco"] is None and p["categoria"] in cardapio_catalogo.LOTES:
            continue
        if facts.normalize(p["item"]) not in conhecidos:
            faltando.append(f"{p['categoria']} | {p['item']}")
    return faltando


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)
    sys.exit(main())
