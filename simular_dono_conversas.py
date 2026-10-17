"""simular_dono_conversas.py — conversas inteiras escritas como o Balbino.

Camada 2 do simulador (a 1 e simular_dono.py, sem modelo). Aqui roda o
atendimento completo, COM o modelo: pedido, oferta, entrega, endereco,
pagamento e resumo, do jeito que ele escreve nos testes (caixa alta, erro de
digitacao, varias linhas, resposta curta, 👍). No fim de cada conversa o codigo
confere o que importa para a fabrica:

  - o resumo traz cada item com a quantidade e o tamanho certos;
  - fechou so com "sim" (ou 👍) ao resumo, e fechou quando devia;
  - foi para a equipe so quando devia (desvio, reclamacao, atendente);
  - nenhuma resposta saiu igual 3 vezes seguidas (laco);
  - textos que tinham de aparecer apareceram, e os proibidos nao.

Depois de uma transferencia a conversa para, como em producao (o robo cala).

    python simular_dono_conversas.py       # 1 rodada (centavos de OpenAI)
    python simular_dono_conversas.py 2     # 2 rodadas, para ver oscilacao
"""
import contextlib
import io
import re
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(override=True)

with contextlib.redirect_stdout(io.StringIO()):
    import test_agente as t_ag  # noqa: E402
    import app  # noqa: E402
    import database  # noqa: E402
    import facts  # noqa: E402
    import horario  # noqa: E402
    import memory  # noqa: E402

t_ag._instalar_stubs()
app.cardapio.em_modo_teste = lambda: False
_TRANSF = []
app.perform_transfer = lambda p, reason="", send_message=True: _TRANSF.append(reason) or True
database.get_contact = lambda phone: {"phone": phone, "name": "Balbino", "auto_mode": 1}
RODADAS = int(next((a for a in sys.argv[1:] if a.isdigit()), 1))

# Quarta, 14/10/2026. "amanha" = quinta 15/10.
TARDE = datetime(2026, 10, 14, 15, 0)
NOITE = datetime(2026, 10, 14, 21, 0)
NL = "\n"

# (nome, hora, mensagens, conferencia)
# conferencia: espelho=[(trecho do nome, qtd)], fecha=bool, equipe=bool,
#              contem=[trechos], nao_contem=[trechos]
CONVERSAS = [
    ("cento frito 4 sabores + aceita o refri + entrega em linhas", TARDE,
     ["Bom dia gostaria de um cento de salgado frito", "Coxinha bolinha kibe churros", "Pode ser",
      "coca", "Entrega", "Rua Wilmam maia " + NL + "5994 Igarapé" + NL + "amanha as 17:30", "Pix",
      "Pode sim"],
     {"espelho": [("Coxinha", 25), ("Bolinha", 25), ("Quibe", 25), ("Churros", 25),
                  ("Refrigerante 2 Litros", 1)], "fecha": True, "equipe": False,
      "nao_contem": ["15/11/2026 -", "Igarapé - Igarapé"]}),
    ("50 coxinhas retirada, NAO a oferta, SIM", TARDE,
     ["QUERO 50 COXINHAS PARA AMANHA AS 8 HORAS RETIRADA", "NAO", "SIM"],
     {"espelho": [("Coxinha", 50)], "fecha": True, "equipe": False}),
    ("3 coxinha grande + 25 bolinha ir 25 coxinhas", TARDE,
     ["Bom dia gostaria de 3 coxinha grande", "Eu quero 25 bolinha ir 25 coxinhas", "NAO",
      "ENTREGA NA RUA SALGADO FILHO 2125 BAIRRO OLARIA", "AMANHA AS 18:30 PIX", "Pode"],
     {"espelho": [("Catupiry", 3), ("Bolinha", 25), ("Coxinha frango", 25)], "fecha": True,
      "equipe": False, "nao_contem": ["grande (lanche) ou mini"]}),
    ("40 coxinhas: quantidade quebrada vai para a equipe", TARDE,
     ["QUERO 40 COXINHAS PRA AMANHA AS 10 HORAS"],
     {"equipe": True, "fecha": False}),
    ("empada de queijo no pedido: avisa e segue", TARDE,
     ["Quero 50 coxinhas e 25 empadas de queijo pra amanhã às 10h, retirada", "NAO", "SIM"],
     {"espelho": [("Coxinha", 50)], "fecha": True, "equipe": False,
      "contem": ["Não temos"]}),
    ("100 pasteis e uma coca: pergunta o tamanho", TARDE,
     ["QUERO 100 PASTEIS E UMA COCA COLA PRA AMANHA AS 9 HORAS RETIRADA", "carne", "2 litros", "SIM"],
     {"espelho": [("Pastel carne", 100), ("Refrigerante 2 Litros", 1)], "fecha": True, "equipe": False,
      "contem": ["Qual tamanho", "Pastel queijo"]}),
    ("desconto com o resumo aberto: defesa e SIM fecha", TARDE,
     ["QUERO 50 COXINHAS PARA AMANHA AS 8 HORAS RETIRADA", "NAO",
      "Consegue dar um desconto no valor fechado pagando no Pix?", "SIM"],
     {"fecha": True, "equipe": False, "contem": ["direto de fábrica"]}),
    ("fiado: negativa", TARDE,
     ["Quero 50 coxinhas pra amanhã às 10h, retirada. Anota aí que eu pago semana que vem"],
     {"equipe": False, "contem": ["fiado"]}),
    ("reclamacao de arame: equipe", TARDE,
     ["veio um pedaço de arame dentro do salgado que comprei"],
     {"equipe": True, "fecha": False}),
    ("quero falar com um atendente: equipe", TARDE,
     ["quero falar com um atendente"],
     {"equipe": True, "fecha": False}),
    ("👍 no resumo fecha", TARDE,
     ["QUERO 50 COXINHAS PARA AMANHA AS 8 HORAS RETIRADA", "NAO", "[Reação: 👍🏻]"],
     {"espelho": [("Coxinha", 50)], "fecha": True, "equipe": False}),
    ("tira 50 de 100 e poe bolinha e kibe", TARDE,
     ["Quero agendar 1 cento de coxinha frita para entregar amanhã às 10h na Rua Duque de "
      "Caxias, 1200 - São Cristóvão. Pagamento no Pix.", "NAO",
      "Muda aí antes de confirmar: tira 50 coxinhas e coloca 25 bolinhas de queijo e 25 kibes.",
      "SIM"],
     {"espelho": [("Coxinha", 50), ("Bolinha", 25), ("Quibe", 25)], "fecha": True,
      "equipe": False}),
    ("domingo: oferece segunda e segue", TARDE,
     ["Quero 1 cento de coxinha pra domingo ao meio dia, retirada", "Sim", "NAO", "SIM"],
     {"espelho": [("Coxinha", 100)], "fecha": True, "equipe": False,
      "contem": ["domingos"]}),
    ("dinheiro com troco", TARDE,
     ["Quero 50 coxinhas pra amanhã 15h, entrega na Rua Jamari 2125 Olaria, pago em dinheiro",
      "NAO", "100 REAIS", "SIM"],
     {"espelho": [("Coxinha", 50)], "fecha": True, "equipe": False, "contem": ["troco"]}),
    ("6 porcoes de cada", TARDE,
     ["Quero 6 porções de coxinha e 6 porções de quibe, fritos, pra amanhã às 10h retirada",
      "NAO", "SIM"],
     {"espelho": [("Coxinha", 150), ("Quibe", 150)], "fecha": True, "equipe": False}),
    ("salschisa digitado errado", TARDE,
     ["QUERO 50 COXINHAS PARA AMANHA A8 HORAS 25 SALSCHISA E 50 QUIBE E 50 ESFIRRAS RETIRADA",
      "NAO", "SIM"],
     {"espelho": [("Coxinha", 50), ("Salsicha", 25), ("Quibe", 50), ("Esfirra", 50)],
      "fecha": True, "equipe": False}),
    # Venda 24h (Balbino, 15/10): a noite ela vende; o horario so aparece se
    # o cliente pedir para agora/hoje ou um horario fechado.
    ("loja fechada: so responde o preco", NOITE,
     ["Qual é o valor do cento dos fritos tradicionais?"],
     {"equipe": False, "contem": ["R$ 80,00"], "nao_contem": ["encerrou"]}),
    ("a noite, pedido sem horario segue vendendo", NOITE,
     ["Quero 50 coxinhas", "NAO", "retirada", "amanhã às 10h", "SIM"],
     {"espelho": [("Coxinha", 50)], "fecha": True, "equipe": False, "nao_contem": ["encerrou"]}),
    ("a noite, pedido pra agora avisa e oferece amanha", NOITE,
     ["Quero 50 coxinhas pra entregar agora"],
     {"equipe": False, "contem": ["encerrou", "amanhã"]}),
    ("pergunta de torta e depois o pedido", TARDE,
     ["Vocês têm torta", "Quero 50 coxinhas pra amanhã às 10h, retirada", "NAO", "SIM"],
     {"espelho": [("Coxinha", 50)], "fecha": True, "equipe": False,
      "contem": ["Não temos torta"]}),
    ("bairro digitado errado (IGAPARE)", TARDE,
     ["Quero 50 coxinhas pra amanhã às 15h, entrega, pago no pix", "NAO", "IGAPARE",
      "Rua Wilmam maia 5994", "SIM"],
     {"espelho": [("Coxinha", 50)], "fecha": True, "equipe": False, "contem": ["Igarapé"]}),
    ("pedido com a hora 7:30 nao vira 30 coxinhas", TARDE,
     ["SERIA PARA AMANHA AS 8:30 QUERO UM CENTO COXINHA E MEIO CENTO DE PASTEL E UMA COCA COLA 2 LT"
      " RETIRADA", "carne", "NAO", "SIM"],
     {"espelho": [("Coxinha", 100), ("Pastel", 50), ("Refrigerante 2 Litros", 1)], "fecha": True,
      "equipe": False}),
    ("cento do mini quiche: nao inventa preco", NOITE,
     ["QUERO SABER VALOR DO CENTO DO MINI QUICHE"],
     {"equipe": False, "contem": ["por unidade"], "nao_contem": ["1200", "1.200"]}),
    ("preco do brigadeiro e SIM QUERO", TARDE,
     ["Quanto custa o cento de brigadeiros?", "SIM QUERO"],
     {"equipe": False, "nao_contem": ["grande (lanche)"]}),
    ("sabores do 2L com a coca no pedido", TARDE,
     ["QUERO PARA AMANHA AS 8 HORAS 100 CHURROS E UMA COCA DE 2LT", "QUERO SABER QUAIS OS SABORES DE REFRI"],
     {"equipe": False, "contem": ["Fanta uva"]}),
    ("mini hamburguer sem numero: nao temos", NOITE,
     ["GOSTARIA TANTO DO MINI HAMBURGUE"],
     {"equipe": False, "contem": ["Não temos mini hamburgue"]}),
    ("3 combos de camarao e 1 cento frito: combos no resumo", TARDE,
     ["Quero 3 combos de camarão e 1 cento frito", "Pastel queijo, churro, croquete e quibe",
      "Só os salgados mesmo", "Retirada", "amanhã às 9h", "SIM"],
     {"espelho": [("Combo Mix Camarão", 3), ("Pastel queijo", 25), ("Churros", 25),
                  ("Croquete", 25), ("Quibe", 25)], "fecha": True, "equipe": False}),
    ("cento de pastel: pergunta carne ou queijo", TARDE,
     ["Quero um cento de pastel pra amanhã às 10h retirada", "queijo", "NAO", "SIM"],
     {"espelho": [("Pastel queijo", 100)], "fecha": True, "equipe": False}),
    ("beijinho tem: responde, sem equipe", TARDE,
     ["Beijinhos tem"],
     {"equipe": False, "contem": ["beijinho"]}),
    ("cento com 3 sabores: um em dobro", TARDE,
     ["Quero um cento frito pra amanhã às 10h retirada", "coxinha, quibe e bolinha", "coxinha", "NAO", "SIM"],
     {"espelho": [("Coxinha", 50), ("Quibe", 25), ("Bolinha", 25)], "fecha": True, "equipe": False,
      "contem": ["dobro"]}),
    ("2 combos sem dizer qual, depois um de frango e um de camarao", NOITE,
     ["OLA BOA NOITE QUERO PARA AMANHA 2 COMBOS", "UM DE FRANGO E UM DE CAMARAO"],
     {"equipe": False, "contem": ["Qual combo"], "nao_contem": ["Qual o seu pedido"]}),
    ("25 SAL e depois 25 SALSICHA: sem laco", NOITE,
     ["MARAVILHA 50 COX E 25 SAL E 25 BOLINHA PRA AMANHA 10H RETIRADA",
      "QUERO SOMENTE 50 COXINHAS E 25 SALSICHA E 25 BOLINHAS", "NAO", "SIM"],
     {"espelho": [("Coxinha", 50), ("Salsicha", 25), ("Bolinha", 25)], "fecha": True,
      "equipe": False, "nao_contem": ["25 de sal:"]}),
    ("festa de 100 pessoas e depois quantos refrigerantes", NOITE,
     ["Qual a quantidade recomendada para festa de 100 pessoas?", "E REFRIGERANTE QUANTOS TENHO QUE COMPRAR"],
     {"equipe": False, "contem": ["400 ml", "20 refrigerantes"], "nao_contem": ["cada 5 pessoas"]}),
    ("antecedencia de 100 unidades", NOITE,
     ["Preciso encomendar com quantos dias de antecedência pedido de 100 unidades?"],
     {"equipe": False, "contem": ["40 minutos"]}),
    ("400 salgados pra daqui a pouco: pede 1 hora", TARDE,
     ["Quero 400 coxinhas pra hoje as 15h30 retirada"],
     {"equipe": False, "contem": ["1 hora de antecedência"]}),
    ("# reset com espaco no meio da conversa", TARDE,
     ["QUERO 50 COXINHAS PARA AMANHA AS 8 HORAS RETIRADA", "# reset",
      "Quero 25 quibes pra amanhã às 9h, retirada", "NAO", "SIM"],
     {"espelho": [("Quibe", 25)], "fecha": True, "equipe": False}),
]

_RX_LINHA_ITEM = re.compile(r"•\s*(\d+)\s*un\s*-\s*(.+?)\s+—\s+R\$")


def _itens_do_espelho(texto: str) -> list[tuple[str, int]]:
    return [(m.group(2), int(m.group(1))) for m in _RX_LINHA_ITEM.finditer(texto or "")]


def _rodar(msgs: list[str], quando: datetime) -> tuple[list[str], list[str]]:
    horario.agora = lambda q=quando: q
    t_ag._ficha.clear()
    memory._store.pop(t_ag.TELEFONE, None)
    app._orders_sent.clear()
    _TRANSF.clear()
    respostas = []
    for m in msgs:
        if m.startswith("[Reação"):
            # Reacao chega como item proprio, igual ao webhook.
            t_ag._enviadas.clear()
            with contextlib.redirect_stdout(io.StringIO()):
                app.handle_flush(t_ag.TELEFONE, [{"text": m, "wa_message_id": ""}])
            r = "\n".join(t_ag._enviadas)
        elif app.e_pedido_de_reset(m):
            # O reset de verdade mexe no contato do banco; aqui basta zerar a
            # ficha e o historico, que e o que ele faz com a conversa.
            t_ag._ficha.clear()
            memory._store.pop(t_ag.TELEFONE, None)
            r = "(reset)"
        else:
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                r = t_ag.enviar(m) or ""
            if "no credits" in buf.getvalue():
                raise SystemExit("ABORTADO: a OpenAI respondeu 'no credits remaining'.")
        respostas.append(r)
        if _TRANSF:
            break           # em producao o robo cala depois da transferencia
    return respostas, list(_TRANSF)


def _conferir(respostas: list[str], transf: list[str], conf: dict) -> list[str]:
    erros = []
    todas = "\n".join(respostas)
    espelhos = [r for r in respostas if _RX_LINHA_ITEM.search(r or "")]
    fechou = any("Pedido finalizado" in (r or "") for r in respostas)
    if "espelho" in conf:
        if not espelhos:
            erros.append("nenhum resumo apresentado")
        else:
            itens = _itens_do_espelho(espelhos[-1])
            for trecho, qtd in conf["espelho"]:
                if not any(trecho.lower() in nome.lower() and q == qtd for nome, q in itens):
                    erros.append(f"resumo sem {qtd} x {trecho} (veio {itens})")
            if len(itens) != len(conf["espelho"]):
                erros.append(f"resumo com {len(itens)} linhas, esperado {len(conf['espelho'])}: {itens}")
    if "fecha" in conf and fechou != conf["fecha"]:
        erros.append("fechou o pedido" if fechou else "nao fechou o pedido")
    if "equipe" in conf and bool(transf) != conf["equipe"]:
        erros.append(f"foi para a equipe ({transf[0][:60]})" if transf else "nao foi para a equipe")
    for t in conf.get("contem", []):
        if t.lower() not in todas.lower():
            erros.append(f"faltou {t!r}")
    for t in conf.get("nao_contem", []):
        if t.lower() in todas.lower():
            erros.append(f"apareceu {t!r}")
    for i in range(len(respostas) - 2):
        if respostas[i] and respostas[i] == respostas[i + 1] == respostas[i + 2]:
            erros.append(f"laco: a mesma resposta 3 vezes ({respostas[i][:50]!r})")
            break
    return erros


def main() -> int:
    falhas = []
    for nome, quando, msgs, conf in CONVERSAS:
        resultados = []
        for _ in range(RODADAS):
            respostas, transf = _rodar(msgs, quando)
            resultados.append((_conferir(respostas, transf, conf), respostas, msgs))
        ruins = [r for r in resultados if r[0]]
        print(f"  {'OK   ' if not ruins else 'FALHA'} {RODADAS - len(ruins)}/{RODADAS}  {nome}")
        for erros, respostas, enviadas in ruins[:1]:
            for e in erros:
                print(f"          - {e}")
            for m, r in zip(enviadas, respostas):
                print(f"            > {m[:70]!r}\n              {(r or '(sem resposta)')[:160]!r}")
        if ruins:
            falhas.append(nome)
    print(f"\n{len(CONVERSAS)} conversas x {RODADAS} rodada(s); FALHAS: {falhas or 'nenhuma'}")
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
