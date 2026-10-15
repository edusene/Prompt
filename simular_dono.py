"""simular_dono.py — escreve como o Balbino e confere se o codigo entende igual.

Camada 1 do simulador: sem modelo, sem custo de OpenAI. Cada frase-modelo
vem dos testes reais dele (chat.txt de 23/09 a 14/10) e ganha variacoes no
estilo dele: caixa alta, sem acento, plural/singular trocado, "e" digitado
como "ir"/"i", data/endereco/pagamento em varias linhas, sem virgula,
"amanah", "AS 8 HRS"... Para cada variacao o codigo tem de ler o MESMO pedido
da frase original: itens, quantidades, tamanho, endereco, bairro, horario,
pagamento. Tambem confere perguntas frequentes, o #reset e sequencias de
mensagens (grande + mini, tira N).

    python simular_dono.py            # roda tudo
    python simular_dono.py -v         # mostra cada variacao que falhou

Foi o tipo de erro do teste de 14/10: "25 bolinha ir 25 coxinhas" (a bolinha
sumia), endereco com a data dentro, "# reset" com espaco, e as 3 coxinhas
grandes apagadas pelas 25 mini.
"""
import contextlib
import io
import itertools
import re
import sys
import unicodedata
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

with contextlib.redirect_stdout(io.StringIO()):
    import app  # noqa: E402
    import conversation_memory as memoria  # noqa: E402
    import facts  # noqa: E402
    import horario  # noqa: E402
    from agents.conversation import ConversationState, _extract_data_from_message  # noqa: E402

VERBOSO = "-v" in sys.argv
# Quarta-feira, 14/10 as 15h: "amanha" e quinta, 15/10.
horario.agora = lambda: datetime(2026, 10, 14, 15, 0, 0)


# ── Variacoes no estilo do Balbino ─────────────────────────────────────────
def _sem_acento(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


_PLURAL = (("coxinhas", "coxinha"), ("bolinhas", "bolinha"), ("quibes", "kibes"),
           ("pasteis", "pateis"), ("pastéis", "pasteis"), ("rissoles", "rissole"),
           ("esfirras", "esfiras"), ("empadinhas", "empadinha"), ("enroladinhos", "enroladinho"))


def _trocas_de_palavra(t: str) -> list[str]:
    saida = []
    for a, b in _PLURAL:
        if re.search(rf"\b{a}\b", t, re.I):
            saida.append(re.sub(rf"\b{a}\b", b, t, flags=re.I))
    return saida


def _variacoes(frase: str) -> list[str]:
    """Ate ~40 jeitos de o Balbino escrever a mesma coisa."""
    base = {frase}
    # Texto: caixa alta, minuscula, sem acento.
    base |= {frase.upper(), frase.lower(), _sem_acento(frase), _sem_acento(frase).upper()}
    # Plural/singular e grafias.
    for t in list(base):
        base |= set(_trocas_de_palavra(t))
    # "e" entre itens digitado errado.
    for t in list(base)[:12]:
        for erro in (" ir ", " i "):
            novo = re.sub(r"(\d+\s+[a-zà-ú]+)\s+e\s+(\d)", lambda m: m.group(1) + erro + m.group(2),
                          t, flags=re.I)
            if novo != t:
                base.add(novo)
    # Datas e horas do jeito dele.
    for t in list(base)[:12]:
        for a, b in ((r"\bamanhã\b", "amanah"), (r"\bamanha\b", "amnha"), (r"\bàs\b", "as"),
                     (r"\bhoras\b", "hrs"), (r",", "")):
            novo = re.sub(a, b, t, flags=re.I)
            if novo != t:
                base.add(novo)
    # Tudo em linhas: endereco, data e pagamento separados.
    for t in list(base)[:8]:
        novo = re.sub(r"\s+(?=(?:rua|na rua|pra|para|vou pagar|pagamento|bairro)\b)", "\n", t,
                      flags=re.I)
        if novo != t:
            base.add(novo)
    return sorted(base)[:40]


# ── O que o codigo leu ─────────────────────────────────────────────────────
def _leitura(texto: str) -> dict:
    with contextlib.redirect_stdout(io.StringIO()):
        r = _extract_data_from_message(texto, ConversationState(phone="sim"))
    itens = sorted((facts.normalize(i["name"]), int(i["quantity"]), i.get("tamanho") or "")
                   for i in r.get("items") or [])
    end = facts.normalize(r.get("endereco") or "")
    return {
        "itens": itens,
        "endereco": " ".join(re.sub(r"[^a-z0-9 ]", " ", end).split()),
        "bairro": facts.normalize(r.get("bairro") or ""),
        "horario": r.get("horario") or "",
        "pagamento": r.get("payment_method") or "",
        "modalidade": r.get("modality") or "",
        "volume": r.get("volume_pendente") or 0,
    }


# ── Frases-modelo (dos testes reais dele) e o que tem de sair ──────────────
# "esperado": so os campos que importam para a frase. O resto nao e conferido.
PEDIDOS = [
    ("QUERO 50 COXINHAS PARA AMANHA AS 8 HORAS RETIRADA",
     {"itens": [("coxinha", 50, "")], "horario": "8h", "modalidade": "retirada"}),
    ("Quero 50 coxinhas, 25 quibes e 25 enroladinhos de salsicha com catupiry para as 17h.",
     {"itens": [("coxinha", 50, ""), ("enroladinho de salsicha com catupiry", 25, ""),
                ("quibe", 25, "")], "horario": "17h"}),
    ("Eu quero 25 bolinhas e 25 coxinhas",
     {"itens": [("bolinha de queijo", 25, ""), ("coxinha", 25, "")]}),
    ("quero 200 coxinhas e 100 rissoles e 150 churros e 200 pasteis",
     {"itens": [("churros", 150, ""), ("coxinha", 200, ""), ("pastel", 200, ""),
                ("rissole de carne", 100, "")]}),
    ("Bom dia gostaria de 3 coxinha grande",
     {"itens": [("coxinha", 3, "lanche")]}),
    ("Oi, quero 35 coxinhas e 15 kibes pra entregar amanhã às 10h na Rua Duque de Caxias, 1200, "
     "Olaria. Vou pagar no Pix.",
     {"itens": [("coxinha", 35, ""), ("quibe", 15, "")], "horario": "10h",
      "bairro": "olaria", "pagamento": "pix", "endereco": "rua duque de caxias 1200"}),
    ("Rua Wilmam maia 5994 Igarapé 15/11/2026 As 17:30",
     {"endereco": "rua wilmam maia 5994", "bairro": "igarape", "horario": "17h30"}),
    ("RUA SALGADO FILHO 2125 BAIRRO OLARIA PAGAMENTO PIX",
     {"endereco": "rua salgado filho 2125", "bairro": "olaria", "pagamento": "pix"}),
    ("quero 1 cento de coxinha", {"itens": [("coxinha", 100, "")]}),
    ("Quero 6 porções de coxinha e 6 porções de quibe, fritos.",
     {"itens": [("coxinha", 150, ""), ("quibe", 150, "")]}),
    ("Queria um centro de canudinho de frango",
     {"itens": [("canudinho de frango", 100, "")], "bairro": ""}),
    ("QUERO 50 COXINHAS E 25 SALSCHISA E 50 QUIBE E 50 ESFIRRAS",
     {"itens": [("coxinha", 50, ""), ("enroladinho de salsicha", 25, ""),
                ("esfirra de carne", 50, ""), ("quibe", 50, "")]}),
    ("quero 100 coxinhas", {"itens": [("coxinha", 100, "")]}),
    ("Quero 1 cento de coxinha pra amanhã ao meio dia, retirada",
     {"itens": [("coxinha", 100, "")], "horario": "12h", "modalidade": "retirada"}),
    ("Quero 50 coxinhas pra amanhã às 15h, entrega, pago no pix",
     {"itens": [("coxinha", 50, "")], "horario": "15h", "modalidade": "entrega",
      "pagamento": "pix"}),
    ("IGAPARE", {"bairro": "igarape"}),
    ("SERIA PARA AMANHA AS 7:30 QUERO UM CENTO COXINHA E MEIO CENTO DE PASTEL E UMA COCA COLA 2 LT",
     {"itens": [("coxinha", 100, ""), ("pastel", 50, "")], "horario": "07h30"}),
]

# Perguntas e o nome da resposta do codigo (balcao / perguntas frequentes).
PERGUNTAS = [
    ("Vocês têm torta?", "produto inexistente"),
    ("As esfirras são de que sabor?", "esfirra sabor"),
    ("O kibe de vocês leva requeijão?", "kibe requeijao"),
    ("Qual a quantidade de salgado para festa de 100 pessoas?", "festa pessoas"),
    ("Vocês emitem nota fiscal?", "nota fiscal"),
    ("Vocês fazem entrega?", "faz entrega"),
    ("Quais refrigerantes vocês têm?", "lista de refri"),
    ("Está tendo alguma promoção?", "promocao"),
    ("Com quantos dias de antecedência preciso encomendar?", "prazo confirmar"),
    ("Tentei pedir pelo site e travou, meu pedido entrou?", "link travou"),
    ("Tem salgado congelado?", "congelado"),
    ("Posso pagar na maquininha na entrega?", "maquininha"),
]

RESETS = ["#reset", "#RESET", "# reset", "# RESET", "#Reset", " #reset "]

# Sequencias de mensagens: a ficha no fim (so a fusao do codigo, sem o modelo).
SEQUENCIAS = [
    (["Bom dia gostaria de 3 coxinha grande", "Eu quero 25 bolinha ir 25 coxinhas"],
     [("bolinha de queijo", 25, ""), ("coxinha", 3, "lanche"), ("coxinha", 25, "")]),
    (["quero 100 coxinhas", "tira 50 coxinhas e coloca 25 bolinhas e 25 kibes"],
     [("bolinha de queijo", 25, ""), ("coxinha", 50, ""), ("quibe", 25, "")]),
    (["quero 50 coxinhas e 25 quibes", "tira os quibes"],
     [("coxinha", 50, "")]),
    (["quero 50 coxinhas e um bolo de milho", "tira o bolo de milho"],
     [("coxinha", 50, "")]),
]


def _confere(leitura: dict, esperado: dict) -> list[str]:
    erros = []
    for campo, valor in esperado.items():
        obtido = leitura.get(campo)
        if campo == "itens":
            if obtido != sorted(valor):
                erros.append(f"itens {obtido} (esperado {sorted(valor)})")
        elif campo == "endereco":
            if obtido != valor:
                erros.append(f"endereco {obtido!r} (esperado {valor!r})")
        elif obtido != valor:
            erros.append(f"{campo} {obtido!r} (esperado {valor!r})")
    return erros


def main() -> int:
    falhas_total, testes_total = 0, 0
    print("== Pedidos (variacoes de escrita)")
    for frase, esperado in PEDIDOS:
        variacoes = _variacoes(frase)
        falhas = []
        for v in variacoes:
            erros = _confere(_leitura(v), esperado)
            if erros:
                falhas.append((v, erros))
        testes_total += len(variacoes)
        falhas_total += len(falhas)
        marca = "OK " if not falhas else "FALHA"
        print(f"  {marca} {len(variacoes) - len(falhas):>2}/{len(variacoes):<2} {frase[:70]!r}")
        for v, erros in (falhas if VERBOSO else falhas[:2]):
            print(f"        {v!r}\n          -> {'; '.join(erros)}")

    print("== Perguntas (resposta do codigo)")
    for pergunta, nome in PERGUNTAS:
        variacoes = sorted({pergunta, pergunta.upper(), _sem_acento(pergunta).lower(),
                            pergunta.rstrip("?"), _sem_acento(pergunta).upper().rstrip("?")})
        falhas = []
        for v in variacoes:
            with contextlib.redirect_stdout(io.StringIO()):
                r = app._resposta_do_balcao(v, {})
            if not r or r[0] != nome:
                falhas.append((v, r[0] if r else None))
        testes_total += len(variacoes)
        falhas_total += len(falhas)
        marca = "OK " if not falhas else "FALHA"
        print(f"  {marca} {len(variacoes) - len(falhas)}/{len(variacoes)} {pergunta!r} -> {nome}")
        for v, got in (falhas if VERBOSO else falhas[:2]):
            print(f"        {v!r} -> {got}")

    print("== #reset")
    falhas = [r for r in RESETS if not app.e_pedido_de_reset(r)]
    testes_total += len(RESETS)
    falhas_total += len(falhas)
    print(f"  {'OK ' if not falhas else 'FALHA'} {len(RESETS) - len(falhas)}/{len(RESETS)}"
          + (f"  nao reiniciou: {falhas}" if falhas else ""))

    print("== Sequencias de mensagens")
    for msgs, esperado in SEQUENCIAS:
        ficha = memoria._empty()
        with contextlib.redirect_stdout(io.StringIO()):
            for m in msgs:
                novo = _extract_data_from_message(m, ConversationState(phone="sim"))
                memoria.merge(ficha, {}, novo, texto=m)
        obtido = sorted((facts.normalize(i["name"]), int(i["quantity"]), i.get("tamanho") or "")
                        for i in ficha.get("items") or [])
        ok = obtido == sorted(esperado)
        testes_total += 1
        falhas_total += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FALHA'} {' -> '.join(msgs)[:80]!r}"
              + ("" if ok else f"\n          -> {obtido} (esperado {sorted(esperado)})"))

    print(f"\n{testes_total} variacoes conferidas; FALHAS: {falhas_total or 'nenhuma'}")
    return 1 if falhas_total else 0


if __name__ == "__main__":
    sys.exit(main())
