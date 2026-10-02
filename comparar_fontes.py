"""comparar_fontes.py — planilha x cardapio digital: o que diverge.

Antes de virar FONTE_DADOS para "cardapio", o dono precisa ver o que muda para
o cliente: taxa de bairro, preco, produto que entra ou sai, disponibilidade,
horario e regras. O que estiver errado no cardapio se corrige LA (e a fonte
que vai valer); o que estiver certo no cardapio passa a valer automaticamente.

Mostra tambem a diferenca entre as duas copias do proprio cardapio: a interna
(a que o calculateOrder cobra, e a que a Sandrinha vai ler) e a publica (a que
o cliente ve na pagina).

    python comparar_fontes.py
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(override=True)

import cardapio_fonte  # noqa: E402
import facts  # noqa: E402


def _titulo(t: str) -> None:
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def main() -> int:
    with facts.com_fonte("planilha"):
        planilha = facts.get()
    bruto = cardapio_fonte.bruto(forcar=True)
    if not bruto:
        print("Cardapio digital indisponivel - nada a comparar.")
        return 1
    cardapio = cardapio_fonte.montar_dados(bruto)

    # ── Bairros ───────────────────────────────────────────────────────────
    _titulo("BAIRROS E TAXAS DE ENTREGA")
    pb = {facts.normalize(b["nome"]): b for b in planilha["bairros"]}
    cb = {facts.normalize(b["nome"]): b for b in cardapio["bairros"]}
    for b in planilha["bairros"]:
        for a in b["aliases"]:
            pb.setdefault(facts.normalize(a), b)
    for b in cardapio["bairros"]:
        for a in b["aliases"]:
            cb.setdefault(facts.normalize(a), b)
    dif = sorted({(b["nome"], b["taxa"], cb[k]["taxa"]) for k, b in pb.items()
                  if k in cb and abs(b["taxa"] - cb[k]["taxa"]) > 0.001})
    so_card = sorted({b["nome"] for k, b in cb.items() if k not in pb})
    so_plan = sorted({b["nome"] for k, b in pb.items() if k not in cb})
    print(f"Planilha: {len(planilha['bairros'])} bairros | Cardapio: {len(cardapio['bairros'])} bairros")
    print(f"\nTaxa diferente ({len(dif)}):")
    for nome, tp, tc in dif:
        print(f"  {nome:30} planilha R$ {tp:6.2f}  ->  cardapio R$ {tc:6.2f}")
    print(f"\nSo no cardapio ({len(so_card)}): {', '.join(so_card)}")
    print(f"\nSo na planilha ({len(so_plan)}): {', '.join(so_plan)}")

    # ── Produtos ──────────────────────────────────────────────────────────
    _titulo("PRODUTOS (preco e disponibilidade)")
    pp = {(p["categoria"], facts.normalize(p["item"])): p for p in planilha["produtos"]}
    cp = {(p["categoria"], facts.normalize(p["item"])): p for p in cardapio["produtos"]}
    print("\nPreco diferente:")
    for k, p in sorted(pp.items()):
        c = cp.get(k)
        if c and p["preco"] is not None and c["preco"] is not None and abs(p["preco"] - c["preco"]) > 0.001:
            print(f"  {p['categoria']:16} {p['item']:40} planilha R$ {p['preco']:.2f} -> cardapio R$ {c['preco']:.2f}")
    print("\nDisponibilidade diferente:")
    for k, p in sorted(pp.items()):
        c = cp.get(k)
        if c and bool(p.get("disponivel", True)) != bool(c.get("disponivel", True)):
            print(f"  {p['categoria']:16} {p['item']:40} planilha {'SIM' if p['disponivel'] else 'NAO'}"
                  f" -> cardapio {'SIM' if c['disponivel'] else 'NAO'}")
    print("\nSo no cardapio (passam a existir para a Sandrinha):")
    for k, c in sorted(cp.items()):
        if k not in pp:
            preco = f"R$ {c['preco']:.2f}" if c["preco"] is not None else "incluso no pacote"
            print(f"  {c['categoria']:16} {c['item']:40} {preco} {'' if c['disponivel'] else '(indisponivel)'}")
    print("\nSo na planilha (deixam de existir para a Sandrinha):")
    for k, p in sorted(pp.items()):
        if k not in cp:
            print(f"  {p['categoria']:16} {p['item']:40} {'(ja estava indisponivel)' if not p['disponivel'] else ''}")

    # ── Horario e regras ──────────────────────────────────────────────────
    _titulo("HORARIO E REGRAS")
    print("Hoje no codigo/prompt: segunda a sabado 07h40-12h00 e 14h00-20h00; "
          "taxa de retorno R$ 2,00 no cartao E no dinheiro.")
    print("No cardapio:")
    for h in cardapio["horarios"]:
        print(f"  {h['chave']:8} {h['valor']:18} {h['obs']}")
    for r in cardapio["regras"]:
        print(f"  [{r['chave']}] {r['valor'][:110]}")

    # ── Copia interna x publica ───────────────────────────────────────────
    _titulo("CARDAPIO: COPIA INTERNA (cobra) x PUBLICA (pagina do cliente)")
    try:
        publico = {m["name"]: m for m in cardapio_fonte.ler("MenuItem")}
        interno = {m["name"]: m for m in bruto["InternalMenuItem"]}
        achou = False
        for nome in sorted(set(publico) | set(interno)):
            p, i = publico.get(nome), interno.get(nome)
            if not p or not i:
                print(f"  {nome:40} so na copia {'publica' if p else 'interna'}")
                achou = True
                continue
            if p.get("price") != i.get("price") or bool(p.get("available")) != bool(i.get("available")) \
                    or sorted(p.get("flavor_options") or []) != sorted(i.get("flavor_options") or []):
                print(f"  {nome:40} interna R$ {i.get('price')} {i.get('flavor_options') or ''}"
                      f"\n  {'':40} publica R$ {p.get('price')} {p.get('flavor_options') or ''}")
                achou = True
        if not achou:
            print("  As duas copias estao iguais.")
    except Exception as e:
        print(f"  (nao foi possivel ler a copia publica: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
