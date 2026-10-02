"""conferir_cardapio.py — o cardapio digital que a Sandrinha le, e onde ele diverge.

O cardapio digital (Base44) tem duas copias dos produtos:
  - a INTERNA (InternalMenuItem), que o calculateOrder usa para cobrar - e a
    que a Sandrinha le desde 02/10/2026;
  - a PUBLICA (MenuItem), que o cliente ve na pagina.

Quando elas divergem, a Sandrinha informa (e o pedido cobra) um valor diferente
do que esta na pagina. Este script mostra o que ela esta lendo e as diferencas,
para quem mantem o cardapio sincronizar.

    python conferir_cardapio.py
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

import cardapio_fonte  # noqa: E402


def _titulo(t: str) -> None:
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    bruto = cardapio_fonte.bruto(forcar=True)
    if not bruto:
        print("Cardapio digital indisponivel.")
        return 1
    dados = cardapio_fonte.montar_dados(bruto)

    _titulo("O QUE A SANDRINHA LE")
    print(f"{len(dados['produtos'])} linhas de produto | {len(dados['bairros'])} bairros")
    for p in dados["produtos"]:
        preco = f"R$ {p['preco']:.2f}" if p["preco"] is not None else "incluso no pacote"
        print(f"  {p['categoria']:16} {p['item']:42} {preco:18} "
              f"{'' if p['disponivel'] else 'INDISPONIVEL'}")
    print()
    for h in dados["horarios"]:
        print(f"  {h['chave']:8} {h['valor']:18} {h['obs']}")
    for r in dados["regras"]:
        print(f"  [{r['chave']}] {r['valor'][:110]}")

    _titulo("COPIA INTERNA (cobra) x PUBLICA (pagina do cliente)")
    try:
        publico = {m["name"]: m for m in cardapio_fonte.ler("MenuItem")}
        interno = {m["name"]: m for m in bruto["InternalMenuItem"]}
    except Exception as e:
        print(f"  (nao foi possivel ler a copia publica: {e})")
        return 0
    achou = False
    for nome in sorted(set(publico) | set(interno)):
        p, i = publico.get(nome), interno.get(nome)
        if not p or not i:
            print(f"  {nome:40} so na copia {'publica' if p else 'interna'}")
            achou = True
            continue
        if (p.get("price") != i.get("price")
                or bool(p.get("available")) != bool(i.get("available"))
                or sorted(p.get("flavor_options") or []) != sorted(i.get("flavor_options") or [])):
            print(f"  {nome:40} interna R$ {i.get('price')} {i.get('flavor_options') or ''}"
                  f"\n  {'':40} publica R$ {p.get('price')} {p.get('flavor_options') or ''}")
            achou = True
    try:
        taxas_pub = {t["bairro"]: t["valor"] for t in cardapio_fonte.ler("DeliveryFee")}
        taxas_int = {t["bairro"]: t["valor"] for t in bruto["InternalDeliveryFee"]}
        for b in sorted(set(taxas_pub) | set(taxas_int)):
            if taxas_pub.get(b) != taxas_int.get(b):
                print(f"  bairro {b:33} interna {taxas_int.get(b)} | publica {taxas_pub.get(b)}")
                achou = True
    except Exception as e:
        print(f"  (nao foi possivel comparar as taxas: {e})")
    if not achou:
        print("  As duas copias estao iguais.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
