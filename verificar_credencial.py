"""
verificar_credencial.py — Confere se o cardapio digital aceita a nossa chave.

Por que existe: ate aqui nao havia como testar a credencial. A primeira vez que
se descobria que a chave estava errada era quando o pedido de um cliente real
falhava no WhatsApp — foi assim que o 401 de 19/09 apareceu, com o cliente ja
tendo confirmado o pedido.

verificar_catalogo.py NAO cobre isto: ele chama so o calculateOrder, que e
publico e passa ate sem chave nenhuma (esta escrito no docstring dele).

Nao cria pedido. Sao tres POSTs no createOrderFromAgent com um corpo que nao
tem nenhum dos campos obrigatorios; muda so o header. Ver
cardapio.verificar_credencial para o porque de serem tres.

    python verificar_credencial.py             # veredito
    python verificar_credencial.py --detalhe   # mostra cada sonda

Exit: 0 aceita | 1 recusada ou nao configurada | 2 indisponivel ou inconclusivo
"""

import sys

import cardapio

_EXIT = {
    "aceita": 0,
    "recusada": 1,
    "nao_configurada": 1,
    "indisponivel": 2,
    "inconclusivo": 2,
}


def main(detalhe: bool = False) -> int:
    r = cardapio.verificar_credencial()

    print("=== Credencial do cardapio digital ===")
    print(f"  modo     : {r['modo']}")
    print(f"  URL      : {r['base_url'] or '(nao configurada)'}")
    print(f"  chave    : {r['chave_mascarada'] or '(nao configurada)'} "
          f"(origem: {r['origem_da_chave']})")

    if detalhe and r["sondas"]:
        print()
        print("  sondas:")
        for s in r["sondas"]:
            status = s["status"] if s["status"] is not None else "erro"
            print(f"    {s['rotulo']:<16} -> {status}  {s['resposta']}")

    print()
    if r["ok"]:
        print("OK  a chave foi aceita pelo cardapio.")
        if r["modo"] == "teste":
            print("~~  modo TESTE ligado: pedido nenhum e enviado de verdade.")
            print("    Ligue o modo producao na aba Cardapio do painel.")
    else:
        for problema in r["problemas"]:
            print(f"!!  {problema}")

    return _EXIT.get(r["estado"], 2)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(override=True)
    sys.exit(main(detalhe="--detalhe" in sys.argv))
