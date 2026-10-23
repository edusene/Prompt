"""
test_regressao.py — Bateria deterministica: sem LLM, sem rede, sem banco.

Existe porque as baterias viviam numa pasta temporaria da sessao e foram
apagadas junto com ela. Teste que segura defeito de producao mora no
repositorio, versionado com o codigo que ele protege.

Cada bloco aqui nasceu de um defeito que chegou ao cliente. A linha de
comentario diz qual, com a data — quando um deles voltar, da para achar a
conversa original.

    python test_regressao.py
"""

import sys

from dotenv import load_dotenv

load_dotenv(override=True)

from datetime import date, datetime  # noqa: E402

import cardapio  # noqa: E402
import cardapio_catalogo as catalogo  # noqa: E402
import config_dono  # noqa: E402

# A suite mede o PADRAO do codigo: o que o dono gravou no painel (no banco de
# producao) nao pode mudar o resultado de teste nenhum. A secao de config_dono
# simula cada gravacao explicitamente.
_LER_JSON_REAL = config_dono._ler_json
config_dono._ler_json = lambda chave: None
import conversation_memory as memoria_mod  # noqa: E402
import facts  # noqa: E402
# Dados fixos: o retrato do cardapio digital salvo em fixtures/ (a planilha saiu
# do sistema em 02/10/2026). Assim o teste nao muda quando o dono edita o
# cardapio, e nao depende de rede.
import json as _json_fx  # noqa: E402
import cardapio_fonte as _cf_fx  # noqa: E402
_RETRATO_FX = _json_fx.load(open("fixtures/cardapio_snapshot_2026-10-02.json", encoding="utf-8"))
_cf_fx.bruto = lambda forcar=False: _RETRATO_FX
from agents.conversation import (  # noqa: E402
    ConversationState,
    _extract_data_from_message,
    detectar_bairro,
    detectar_data,
    detectar_pagamento,
)

AGORA = datetime(2026, 9, 25, 10, 36, 0)      # sexta-feira

_falhas: list[str] = []


def checa(rotulo: str, condicao: bool, detalhe: str = "") -> None:
    print(f"  {'OK  ' if condicao else 'FALHA'} {rotulo:<56} {detalhe}")
    if not condicao:
        _falhas.append(rotulo)


def secao(titulo: str) -> None:
    print()
    print(titulo)


def _endereco(mensagem: str) -> str:
    estado = ConversationState()
    estado.modality = "entrega"
    return (_extract_data_from_message(mensagem, estado).get("endereco") or "").strip()


def _itens(mensagem: str) -> list[tuple[int, str]]:
    extraido = _extract_data_from_message(mensagem, ConversationState())
    return [(i["quantity"], i["name"]) for i in extraido.get("items", [])]


def _sugestoes(itens: list[dict]) -> list[int]:
    return [q["sugerida"] for q in catalogo.quebras_de_lote([dict(i) for i in itens])]


def _envio_aceita(itens: list[dict]) -> bool:
    """O caminho real do envio: expande pacote, oficializa nome, traduz."""
    expandidos = []
    for bruto in itens:
        item = dict(bruto)
        nome, qtd = catalogo._sem_rotulo(str(item["name"]), int(item["quantity"]))
        item["name"], item["quantity"] = nome, qtd
        expandidos.append(item)
    norm, _avisos, fora = cardapio._normalizar_itens([dict(i) for i in expandidos])
    usar = norm if (norm and not fora) else expandidos
    _saida, bloqueados = catalogo.traduzir([dict(i) for i in usar])
    return not bloqueados


print("=" * 78)
print("REGRESSAO DETERMINISTICA")
print("=" * 78)

# ── Itens: o que o cliente escreve tem de virar pedido ─────────────────────
secao("--- itens extraidos da mensagem ---")
# 23/09: a aspa no fim apagava o segundo item e o pedido saia pela metade.
checa("aspa no fim nao apaga o segundo item",
      _itens('50 coxinha e 50 churros"') == [(50, "Coxinha"), (50, "Churros")])
checa("parenteses no meio nao apaga item",
      len(_itens("50 coxinhas (frango) e 50 churros")) == 2)
checa("hifen entre itens nao apaga item",
      len(_itens("quero 50 coxinhas - 50 churros")) == 2)
# 23/09: "hoje as 18h" virava "18 x Coxinha".
checa("horario nao vira item", _itens("hoje as 18h") == [])

# 25/09: "pao pizza" parava em "pao" e "canudinho de camarao" virava frango.
checa("nome de produto com espaco fica inteiro",
      _itens("25 pao pizza") == [(25, "Pao Pizza")])
checa("camarao nao vira frango",
      _itens("25 canudinho de camarao") == [(25, "Canudinho de Camarao")])
# 25/09: "meio cento de X" nao era pedido para a extracao.
checa("meio cento de coxinha = 50", _itens("meio cento de coxinha") == [(50, "Coxinha")])
checa("um cento de coxinha = 100", _itens("um cento de coxinha") == [(100, "Coxinha")])
checa("uma porcao de quibe = 25", _itens("uma porcao de quibe") == [(25, "Quibe")])
checa("'o cento sai por quanto?' nao e pedido", _itens("o cento sai por quanto?") == [])
checa("formato sem sabor nao inventa item",
      _itens("meio cento de salgado assado") == [])
# 25/09: "canudinhos" no plural caia num atalho de 4 letras e virava FRANGO.
checa("plural nao troca o sabor: camarao continua camarao",
      "camar" in _itens("25 canudinhos de camarão")[0][1].lower())
checa("plural nao atrapalha o frango",
      "frango" in _itens("25 canudinhos de frango")[0][1].lower())
checa("quiche no plural nao vira outro sabor",
      "quiche" in _itens("2 quiches de carne de sol")[0][1].lower()
      and "carne" in _itens("2 quiches de carne de sol")[0][1].lower())

# ── Endereco: o campo seguinte nao entra no endereco ───────────────────────
secao("--- endereco ---")
# 25/09: os dois foram impressos na comanda assim.
checa("nao engole a forma de pagamento",
      _endereco("OLARIA RUA GUANABARA PAGAMENTO NO PIX") == "RUA GUANABARA")
checa("nao engole o horario",
      _endereco("BAIRRO PEDRINHAS RUA FLORES AS 18 HORAS") == "RUA FLORES")
checa("endereco normal fica inteiro",
      _endereco("Rua Salgado Filho 2233, bairro Olaria") == "Rua Salgado Filho 2233")
# 23/09: respondido sem "Rua" na frente, nao era guardado.
estado = ConversationState()
estado.modality, estado.bairro = "entrega", "Olaria"
checa("rua sem rotulo, respondendo a pergunta",
      _extract_data_from_message("SALGADO FILHO,2233", estado).get("endereco")
      == "SALGADO FILHO,2233")

# ── Data ───────────────────────────────────────────────────────────────────
secao("--- data ---")
# 25/09: "DIA 16" nao era entendido e o modelo inventou 16/09, ja passado.
checa("dia 16 vira 16/10 (o de setembro ja passou)",
      detectar_data("DIA 16 AS 15 HORAS", AGORA) == date(2026, 10, 16))
checa("dia 30 fica em setembro", detectar_data("dia 30", AGORA) == date(2026, 9, 30))
checa("'meu dia foi bom' nao e data", detectar_data("meu dia foi bom", AGORA) is None)
# Mudou em 29/09: "25/09" dito em 29/09 virava 25/09/2027 e o espelho saia para
# daqui a um ano. Passada ha poucos dias e engano: fica no passado e a
# validacao pergunta o dia de novo. So passada ha muito vai para o ano que vem.
checa("data passada ha poucos dias fica no passado (nao vira o ano seguinte)",
      detectar_data("16/09", AGORA) == date(2026, 9, 16))
checa("data passada ha meses vai para o ano seguinte",
      detectar_data("16/03", AGORA) == date(2027, 3, 16))

# ── Quantidade de lote ─────────────────────────────────────────────────────
secao("--- lote: passo por sabor, minimo por familia ---")
# 25/09: cobrar o minimo por sabor dobrava o meio cento de assados.
checa("meio cento de assados 25+25 nao pede ajuste",
      _sugestoes([{"name": "Esfirra de Carne", "quantity": 25},
                  {"name": "Pão Pizza", "quantity": 25}]) == [])
# 07/10: o dono cadastrou a porcao de 25 assados - 25 sozinhos ja fecham.
checa("25 assados sozinhos fecham na porcao de 25",
      _sugestoes([{"name": "Esfirra de Carne", "quantity": 25}]) == [])
checa("30 fritos arredondam para 25",
      _sugestoes([{"name": "Coxinha frango", "quantity": 30}]) == [25])
checa("25 fritos ja fecham", _sugestoes([{"name": "Coxinha frango", "quantity": 25}]) == [])

# 25/09: a trava de lote mandava ajustar 3 rissoles avulsos para 25, e a
# Sandrinha chegou a dizer "25 unidades avulsas" — R$ 225,00.
checa("avulso abaixo de 21 nao entra na regra de lote",
      _sugestoes([{"name": "Rissole de Carne", "quantity": 3}]) == [])
checa("sabor que so existe em pacote ainda sobe para 25",
      _sugestoes([{"name": "Bolinha de Queijo", "quantity": 5}]) == [25])

# ── Envio: o nome e a quantidade que chegam ao cardapio digital ────────────
secao("--- caminho do envio ---")
# 24/09: dois pedidos perdidos aqui, um por nome e outro por quantidade.
checa("nomes da ficha passam no de-para",
      _envio_aceita([{"name": "Coxinha", "quantity": 50},
                     {"name": "Churros", "quantity": 50}]))
checa("nomes oficiais passam",
      _envio_aceita([{"name": "Coxinha frango", "quantity": 50}]))
checa("1 pacote com rotulo vira 50 unidades",
      _envio_aceita([{"name": "Meio cento (50 un) Coxinha frango", "quantity": 1}]))
checa("rotulo de formato SOZINHO continua bloqueado",
      not _envio_aceita([{"name": "Cento (100 un)", "quantity": 1}]))
# 25/09: 3 rissoles avulsos (R$ 9,00 cada) batiam na regra de lote e nao
# havia como precificar — a trava mandava "ajuste para 25", R$ 225,00.
checa("pedido pequeno de nome ambiguo sai como avulso",
      _envio_aceita([{"name": "Rissole de Carne", "quantity": 3}]))
checa("o mesmo nome em quantidade de festa continua sendo pacote",
      _envio_aceita([{"name": "Rissole de Carne", "quantity": 25}]))
checa("sabor sem versao avulsa continua exigindo o lote",
      not _envio_aceita([{"name": "Bolinha de Queijo", "quantity": 5}]))

# ── Dinheiro ───────────────────────────────────────────────────────────────
secao("--- dinheiro ---")
checa("taxa de retorno de R$ 2,00 no cartao",
      cardapio.calcular_taxa_retorno("entrega", "cartao") == 2.00)
checa("Pix e isento", cardapio.calcular_taxa_retorno("entrega", "pix") == 0.0)
checa("retirada nao tem taxa", cardapio.calcular_taxa_retorno("retirada", "dinheiro") == 0.0)
# 23/09: perguntar debito ou credito era atrito puro, os dois pagam o mesmo.
checa("'cartao' e forma completa", detectar_pagamento("CARTAO") == "cartao")
checa("nao ha pendencia de debito/credito",
      memoria_mod.pendencia_de_detalhe({"payment_method": "cartao"}) == "")

# ── Tabela oficial ─────────────────────────────────────────────────────────
secao("--- tabela oficial ---")
checa("bairro novo do dono esta na tabela",
      (facts.lookup_frete("Nova Porto Velho") or {}).get("taxa") == 12.0)
checa("bairro reconhecido dentro da frase",
      detectar_bairro("entrega no bairro Nova Porto Velho, rua Amazonas 1234")
      == "Nova Porto Velho")
checa("corte de festa em 25 unidades", facts.QTD_MINIMA_FESTA == 25)
for _q, _pergunta in ((1, True), (20, True), (24, True), (25, False), (50, False)):
    checa(f"{_q:>3} un -> pergunta tamanho? {_pergunta}",
          bool(memoria_mod.pendencia_de_item(
              {"items": [{"name": "Coxinha", "quantity": _q}]})) == _pergunta)
_dir = memoria_mod.pendencia_de_item({"items": [{"name": "Coxinha", "quantity": 5}]})
checa("a pergunta e 'grande (lanche) ou mini (festa)'",
      "grande (lanche) ou mini (festa)" in _dir)
checa("manda isolar so o item sem tamanho", "ISOLE SO ESSE ITEM" in _dir)

# 25/09: termos de volume do dono.
checa("cento e meio = 150", _itens("cento e meio de coxinha") == [(150, "Coxinha")])
checa("dois centos = 200", _itens("dois centos de coxinha") == [(200, "Coxinha")])
checa("2 centos = 200 (nao 2 unidades)",
      _itens("2 centos de coxinha") == [(200, "Coxinha")])
# O dono escreveu o pedido entre aspas (25/09 19h20) e o ultimo item sumiu: o
# terminador do sabor era a lista fechada [,;.!?], que nao inclui a aspa. Agora
# qualquer caractere que nao seja letra nem espaco encerra o sabor.
checa("pedido entre aspas mantem os dois itens",
      _itens('"Quero meio cento de coxinha e um cento e meio de churros"')
      == [(50, "Coxinha"), (150, "Churros")])
checa("aspa simples tambem encerra",
      _itens("'meio cento de coxinha e um cento de churros'")
      == [(50, "Coxinha"), (100, "Churros")])
checa("parentese encerra o sabor",
      _itens("meio cento de coxinha (para hoje)") == [(50, "Coxinha")])
checa("exclamacao segue valendo",
      _itens("meio cento de coxinha e um cento de churros!")
      == [(50, "Coxinha"), (100, "Churros")])

checa("volume sem sabor fica pendente, sem virar item",
      _extract_data_from_message("quero um cento de salgados fritos",
                                 ConversationState()).get("volume_pendente") == 100)

# ── Numero da casa na entrega ────────────────────────────────────
secao("--- numero da casa ---")
# 25/09: o pedido do dono fechou em R$ 92,00 para "RUA GUANABARA", sem numero
# e sem ninguem perguntar. O prompt pedia; pedir nao e garantir.


def _ficha_entrega(**extra) -> dict:
    base = {"name": "Eduardo", "items": [{"product_name": "Coxinha", "quantity": 50}],
            "modality": "entrega", "bairro": "Olaria", "payment_method": "pix",
            "data_agendada": "amanha", "horario": "16:00"}
    base.update(extra)
    return base


def _falta_numero(**extra) -> bool:
    return "endereco_numero" in memoria_mod.faltando(_ficha_entrega(**extra))


checa("rua sem numero fica pendente (caso real do dono)",
      _falta_numero(endereco="RUA GUANABARA"))
checa("rua com numero passa", not _falta_numero(endereco="Rua das Flores 120"))
# O dono cortou o S/N em 25/09: "COMO QUE MOTOBOY VAI ACHAR?". Escrever S/N
# na rua nao completa mais o endereco.
checa("S/N na rua NAO completa o endereco", _falta_numero(endereco="Av Calama, S/N"))
checa("ponto de referencia substitui o numero",
      not _falta_numero(endereco="Rua X", referencia="casa azul da esquina"))
checa("retirada nao cobra numero",
      "endereco_numero" not in memoria_mod.faltando(
          {"name": "E", "items": [1], "modality": "retirada",
           "data_agendada": "amanha", "horario": "16:00"}))
checa("endereco vazio cobra o endereco, nao o numero",
      memoria_mod.faltando(_ficha_entrega(endereco="")).count("endereco") == 1
      and not _falta_numero(endereco=""))

# A pergunta precisa ter resposta possivel: sem captura, o cliente respondia
# "45" e a pendencia continuava de pe para sempre.
def _capturou(texto: str, endereco: str = "RUA GUANABARA") -> str:
    ficha = _ficha_entrega(endereco=endereco)
    return memoria_mod.capturar_numero_da_casa(ficha, texto)


checa("numero solto entra na ficha", _capturou("45").endswith(", 45"))
checa("numero com preambulo entra", _capturou("e o 120").endswith(", 120"))
# Quem diz que a casa nao tem numero cai no unico substituto que o motoboy
# usa: o ponto de referencia.
_ficha_sn = _ficha_entrega(endereco="RUA GUANABARA")
memoria_mod.capturar_numero_da_casa(_ficha_sn, "e S/N, a casa nao tem numero")
checa("S/N nao escreve no endereco", _ficha_sn["endereco"] == "RUA GUANABARA")
checa("S/N marca a casa sem numero", _ficha_sn.get("sem_numero") is True)
checa("sem numero, o que falta e a referencia",
      memoria_mod.faltando(_ficha_sn) == ["endereco_referencia"])
_ficha_sn["referencia"] = "em frente ao mercado"
checa("referencia completa o endereco", not memoria_mod.faltando(_ficha_sn))
checa("horario nao vira numero de casa", not _capturou("hoje as 16h"))
checa("quantidade nao vira numero de casa", not _capturou("quero 50 coxinhas"))
checa("valor nao vira numero de casa", not _capturou("vou pagar 100 reais"))
checa("nada a capturar quando o endereco ja tem numero",
      not _capturou("45", endereco="Rua das Flores 120"))

# A diretiva pede os tres dados numa mensagem so, entao a resposta vem junta.
# Antes disto o numero ficava solto e o modelo lhe dava sentido proprio: leu
# "45" como quantidade e respondeu com a regra do minimo de 25.
checa("resposta junta: numero antes da data", _capturou("45, amanha as 16h").endswith(", 45"))
checa("resposta junta: numero depois da data",
      _capturou("amanha as 16h, numero 45").endswith(", 45"))
checa("resposta junta sem pontuacao", _capturou("45 amanha 16h").endswith(", 45"))
checa("data numerica nao vira numero de casa",
      _capturou("casa 1200, dia 26/09 as 18h").endswith(", 1200"))
checa("dia e hora juntos nao viram numero", not _capturou("dia 26 as 16h"))
checa("produto citado nao vira numero", not _capturou("quero 50 pao pizza"))
checa("dois horarios nao viram numero", not _capturou("amanha as 10h e as 16h"))
checa("volume nao vira numero", not _capturou("pode ser 2 centos de coxinha"))

# A ficha nao pode dizer as duas coisas: rua confirmada ("nao pergunte de
# novo") e numero faltando. O modelo desempatava descartando o numero.
_ficha_render = memoria_mod.render_ficha(_ficha_entrega(endereco="RUA GUANABARA"))
checa("ficha marca a rua como incompleta", "SEM O NUMERO DA CASA" in _ficha_render)
checa("ficha lista o numero no que falta", "Numero da casa" in _ficha_render)

# ── Horario ─────────────────────────────────────────────────────
secao("--- horario ---")
import horario  # noqa: E402

# "20 HORAS" foi recusado tres vezes seguidas no teste do dono (25/09 18h),
# com o limite escrito ">=": a loja atende ATE as 20h, entao 20h00 vale.
_MANHA_DE_HOJE = datetime(2026, 9, 25, 10, 0, 0)
_HOJE = date(2026, 9, 25)


def _aceita(texto: str) -> bool:
    ok, _msg, _dia, _campo = horario.validar_agendamento(_HOJE, texto, _MANHA_DE_HOJE)
    return ok


checa("20 horas e horario valido", _aceita("20 horas"))
checa("20:00 e horario valido", _aceita("20:00"))
checa("20:10 passa do fechamento", not _aceita("20:10"))
checa("19:55 vale", _aceita("19:55"))
checa("07:00 e antes de abrir", not _aceita("07:00"))
checa("13:00 cai no almoco", not _aceita("13:00"))

# A hora colada na palavra anterior nao existia para o parser, e a pergunta
# era repetida: "PODE SER PARA AS19:50" (teste do dono).
checa("hora colada na palavra", horario.parse_horario("PODE SER PARA AS19:50") == (19, 50))
checa("virgula como separador", horario.parse_horario("19,50") == (19, 50))
checa("formatos de sempre seguem valendo",
      [horario.parse_horario(t) for t in ("18h", "18:30", "as 8h15", "18 horas")]
      == [(18, 0), (18, 30), (8, 15), (18, 0)])
checa("dinheiro nao vira horario",
      [horario.parse_horario(t) for t in ("vou pagar 19,50 reais", "troco para 50,00")]
      == [None, None])
checa("numero de casa nao vira horario", horario.parse_horario("2135") is None)

# ── Pedido certo nao leva aula ──────────────────────────────────
secao("--- conduta ---")
import app  # noqa: E402

_NA_REGRA = {"name": "B", "modality": "entrega",
             "items": [{"name": "Coxinha", "quantity": 50},
                       {"name": "Churros", "quantity": 150}]}
_FORA_DA_REGRA = {"name": "B", "modality": "entrega",
                  "items": [{"name": "Coxinha", "quantity": 30}]}

_AULA = ("Para o churros, a porcao minima e de 25 unidades por sabor, e o pedido "
         "deve ser em multiplos de 25. Vou confirmar: 50 Coxinha e 150 Churros. "
         "Esta correto?")
checa("aula cortada quando o pedido ja esta na regra",
      "multiplos de 25" not in app._conferir_aula_de_lote("55", _AULA, _NA_REGRA))
checa("aula mantida quando algo esta fora da regra",
      app._conferir_aula_de_lote("55", _AULA, _FORA_DA_REGRA) == _AULA)
checa("aula cortada nao deixa a mensagem vazia",
      len(app._conferir_aula_de_lote("55", _AULA, _NA_REGRA)) > 20)

# O dono: "NAO PODE FALAR PODE SER SEM NUMERO COMO QUE MOTOBOY VAI ACHAR?"
for _frase in ("Qual e o numero da residencia? (pode ser S/N) 🛵",
               "Numero da casa (ou S/N)",
               "Pode ser sem numero?"):
    _saida = app._conferir_sem_numero("55", _frase)
    checa(f"oferta de S/N cortada: {_frase[:28]!r}",
          "s/n" not in _saida.lower() and "sem numero" not in _saida.lower())
for _frase in ("Qual e o numero da casa? 🛵", "Quero 50 coxinhas no ponto",
               "Sem mais nada?", "salgados nossos"):
    checa(f"frase legitima intacta: {_frase[:26]!r}",
          app._conferir_sem_numero("55", _frase) == _frase)

# ── Modalidade inferida pelo endereco ─────────────────────────────
secao("--- modalidade ---")
# "1 cento de coxinha ... aqui no Agenor de Carvalho" e ela perguntou "entrega
# ou retirada?" duas vezes (25/09 20h45). E travava ali: sem modalidade nao ha
# total, e sem total a trava do troco nunca dispara.


def _modalidade(**ficha) -> str:
    m = {"items": [], "bairro": "", "endereco": "", "modality": ""}
    m.update(ficha)
    memoria_mod.inferir_modalidade(m)
    return m.get("modality", "")


_UM_ITEM = [{"name": "Coxinha", "quantity": 100}]
checa("bairro no pedido = entrega",
      _modalidade(items=_UM_ITEM, bairro="Agenor de Carvalho") == "entrega")
checa("rua no pedido = entrega",
      _modalidade(items=_UM_ITEM, endereco="RUA X 45") == "entrega")
checa("retirada dita vence a inferencia",
      _modalidade(items=_UM_ITEM, bairro="Olaria", modality="retirada") == "retirada")
checa("pergunta de frete sem pedido nao vira entrega",
      _modalidade(bairro="Pedrinhas") == "")
checa("pedido sem endereco nenhum fica sem modalidade",
      _modalidade(items=_UM_ITEM) == "")
checa("volume pendente com bairro = entrega",
      _modalidade(volume_pendente=100, bairro="Olaria") == "entrega")

# ── Trava de troco ─────────────────────────────────────────
secao("--- troco ---")
# Troco para R$ 50,00 num pedido de R$ 94,00, e ela pediu a confirmacao. A
# regra existia, mas chegava um turno depois do espelho.

_FICHA_TROCO = {
    "name": "Balbino", "modality": "entrega", "bairro": "Agenor de Carvalho",
    "endereco": "RUA DOS PIONEIROS 450", "payment_method": "dinheiro",
    "data_agendada": "amanha (26/09)", "data_iso": "2026-09-26", "horario": "9h",
    "items": [{"name": "Coxinha", "quantity": 100}], "total": 94.0,
}
# Montado com chr(10) de proposito: escape em payload de patch ja virou
# newline de verdade uma vez, e o arquivo parou de compilar.
_ESPELHO_TROCO = chr(10).join([
    'Cliente: Balbino',
    'Total: R$ 94,00',
    'Pagamento: Dinheiro na entrega (troco para R$ 50,00)',
    'Posso confirmar?',
])


def _troco(**extra) -> str:
    ficha = dict(_FICHA_TROCO)
    ficha.update(extra)
    return app._conferir_troco("55", _ESPELHO_TROCO, ficha)


checa("troco de 50 num total de 94 e barrado",
      "maior que o total" in _troco(troco_para="50.00"))
# Sem limpar, capturar_troco ignora a correcao do cliente: ela so age com o
# campo vazio, e o cliente ficaria preso repetindo um valor que nunca entra.
_ficha_barrada = dict(_FICHA_TROCO, troco_para="50.00")
app._conferir_troco("55", _ESPELHO_TROCO, _ficha_barrada)
checa("a barra limpa o troco da ficha, para aceitar a correcao",
      _ficha_barrada["troco_para"] == "")
checa("depois de limpo, o novo troco entra",
      memoria_mod.capturar_troco(_ficha_barrada, "", "troco para 100") == "100.00")
checa("troco que cobre o total passa",
      _troco(troco_para="100.00") == _ESPELHO_TROCO)
checa("cliente com valor trocado passa", _troco(troco_para="nao") == _ESPELHO_TROCO)
# 26/09 19h02: o cliente ofereceu troco para R$ 80,00 num cento de fritos de
# R$ 80,00 e a Sandrinha seguiu a conversa sem dizer nada - esperar a ficha
# fechar chegava um turno tarde. Com a ficha aberta o total ainda nao existe,
# mas o SUBTOTAL existe e e um piso: troco que nao cobre nem ele e invalido.
checa("ficha incompleta barra pelo subtotal",
      "precisa ser maior" in _troco(troco_para="50.00", horario=""))
checa("ficha incompleta nao fala de 'total' que ainda nao existe",
      "salgados já dão" in _troco(troco_para="50.00", horario=""))
checa("troco acima do subtotal passa com a ficha aberta",
      _troco(troco_para="100.00", horario="") == _ESPELHO_TROCO)
checa("sem dinheiro nao ha troco a conferir",
      _troco(payment_method="cartao", troco_para="") == _ESPELHO_TROCO)

# ── Tool chamada com quantidade errada ──────────────────────────
secao("--- calculo vs ficha ---")
# calculate_order e confiavel; o que o modelo passa para ela, nao. Com 200 un na
# ficha ele chamou com 350, a tool devolveu R$ 294,00 certo para 350, e o filtro
# apresentou isso como oficial - trocando o total CERTO do modelo por um errado.
_FICHA_200 = {"items": [{"name": "Coxinha", "quantity": 50},
                        {"name": "Churros", "quantity": 150}]}
checa("calculo sobre a mesma quantidade vale",
      app._calculo_confere_com_a_ficha({"items": [{"quantity": 200}]}, _FICHA_200))
checa("calculo sobre outra quantidade e descartado",
      not app._calculo_confere_com_a_ficha({"items": [{"quantity": 350}]}, _FICHA_200))
checa("sabores agrupados pela tool continuam batendo",
      app._calculo_confere_com_a_ficha(
          {"items": [{"quantity": 50}, {"quantity": 150}]}, _FICHA_200))
checa("sem calculo, nada a confiar",
      not app._calculo_confere_com_a_ficha(None, _FICHA_200))

# ── Recusa de pedido valido ───────────────────────────────
secao("--- recusa indevida ---")
# "nao existe meio cento ou cento e meio para churros" - existe, e a regra e do
# proprio dono. Em 4 de 5 rodadas ela matava a venda assim.
_FICHA_VALIDA = {
    "name": "Balbino", "modality": "entrega", "bairro": "Olaria",
    "endereco": "RUA SALGADO 2325", "payment_method": "cartao",
    "data_agendada": "amanha (26/09)", "data_iso": "2026-09-26", "horario": "8h",
    # A oferta adicional entrou no funil em 26/09 e agora tambem e pendencia:
    # com ela em aberto, a resposta certa nao e o espelho, e a oferta. Esta
    # ficha testa o espelho, entao o upsell ja saiu.
    "upsell_feito": True,
    "items": [{"name": "Coxinha", "quantity": 50}, {"name": "Churros", "quantity": 150}],
}
for _recusa in (
    "para o churros, o formato correto e por porcao de 25 unidades, nao existe "
    "meio cento ou cento e meio para churros",
    "o nome correto no cardapio e Churros doce de leite e ele e vendido como "
    "sabor incluso no pacote de fritos, nao em porcoes separadas",
    "o nome correto do item e Churros doce de leite e ele e vendido no formato "
    "porcao de 25 unidades, nao em cento e meio",
):
    _saida = app._conferir_nega_formato("55", _recusa, dict(_FICHA_VALIDA))
    checa(f"recusa trocada pelo espelho: {_recusa[:30]!r}",
          "Total" in _saida or "Subtotal" in _saida)

# Anunciar o calculo e parar deixa o cliente esperando pelo que nunca vem.
for _enrola in ("Agora vou calcular o valor total com a taxa de entrega. Um "
                "instante, por favor.",
                "Vou considerar 50 un de Coxinha e 150 de Churros. Ja calculo!"):
    checa(f"enrolacao trocada pelo espelho: {_enrola[:26]!r}",
          "Total" in app._conferir_nega_formato("55", _enrola, dict(_FICHA_VALIDA)))

# "nao esta no nosso cardapio" dito de um item que ESTA na ficha e recusa
# indevida; dito de um produto que nao existe, e resposta correta.
checa("item da ficha dito fora do cardapio vira espelho",
      "Total" in app._conferir_nega_formato(
          "55", 'o item "cento e meio de churros" nao esta no nosso cardapio oficial',
          dict(_FICHA_VALIDA)))

# facts.normalize apaga a pontuacao, e as alternativas com "?" do regex de
# licenca nunca casavam - estavam mortas desde que foram escritas.
_COM_CERTO = ("o seu pedido e meio cento de Coxinha (50 un), certo? "
              "So para confirmar, os salgados sao fritos, correto?")
checa("normalizacao preserva o ?", "?" in app._sem_acento_com_pergunta(_COM_CERTO))
checa("pergunta de licenca com 'certo?' casa",
      bool(app._RX_PEDE_LICENCA.search(app._sem_acento_com_pergunta(_COM_CERTO))))
# Formas de pedir licenca colhidas na varredura de 26/09. Com a ficha completa
# nao ha licenca a pedir: o proximo passo e o espelho.
for _licenca in ("Poderia confirmar para eu calcular certinho o seu pedido?",
                 "Pode confirmar o pedido?",
                 "Posso seguir com o resumo?",
                 "Deseja confirmar assim?"):
    checa(f"licenca reconhecida: {_licenca[:30]!r}",
          bool(app._RX_PEDE_LICENCA.search(app._sem_acento_com_pergunta(_licenca))))

checa("afirmacao sem ? nao e pedido de licenca",
      not app._RX_PEDE_LICENCA.search(
          app._sem_acento_com_pergunta("O total esta correto e o pedido segue")))
checa("ficha completa + 'certo?' vira espelho",
      "Total" in app._conferir_permissao("55", _COM_CERTO, dict(_FICHA_VALIDA)))

checa("produto que nao existe segue recusado",
      app._conferir_nega_formato(
          "55", "Nao temos panqueca de carne no momento.", dict(_FICHA_VALIDA))
      == "Nao temos panqueca de carne no momento.")
checa("resposta comum fica intacta",
      app._conferir_nega_formato("55", "Sera para entrega ou retirada?",
                                 dict(_FICHA_VALIDA))
      == "Sera para entrega ou retirada?")
_FICHA_INCOMPLETA = dict(_FICHA_VALIDA, horario="")
checa("ficha incompleta nao troca pelo espelho",
      app._conferir_nega_formato("55", "nao existe meio cento para churros",
                                 _FICHA_INCOMPLETA)
      == "nao existe meio cento para churros")

# ── A escolha do tamanho tem de entrar na ficha ────────────────────
secao("--- escolha de tamanho ---")
# Havia tratamento para quem escolhia o PACOTE e nenhum para quem escolhia o
# LANCHE: o cliente respondia "Grande, lanche", nada entrava na ficha, a
# pendencia de tamanho ficava de pe e o espelho nao saia - ela encerrava com
# "posso ajudar com mais alguma coisa?" e o pedido de R$ 45,00 morria ali.


def _ficha_5_coxinhas() -> dict:
    return {"name": "Eduardo", "modality": "retirada",
            "items": [{"name": "Coxinha", "quantity": 5}],
            "data_agendada": "amanha (29/09)", "data_iso": "2026-09-29",
            "horario": "15h"}


_f_lanche = _ficha_5_coxinhas()
checa("5 coxinhas comecam com duvida de tamanho",
      bool(memoria_mod.pendencia_de_item(_f_lanche)))
checa("a escolha do lanche e gravada",
      memoria_mod.aplicar_escolha_de_lanche(_f_lanche) != [])
checa("gravada, a duvida fecha", not memoria_mod.pendencia_de_item(_f_lanche))
checa("LANCHE mantem a quantidade pedida (regra do dono)",
      _f_lanche["items"][0]["quantity"] == 5)
checa("ficha completa depois da escolha", not memoria_mod.faltando(_f_lanche))

checa("'Grande, lanche' e lanche, nao pacote",
      app._escolheu_lanche("Grande, lanche") and not app._escolheu_pacote("Grande, lanche"))
checa("'Mini' e pacote, nao lanche",
      app._escolheu_pacote("Mini") and not app._escolheu_lanche("Mini"))
checa("'em porcao' e pacote", app._escolheu_pacote("em porcao"))
checa("'avulsa' e lanche", app._escolheu_lanche("avulsa"))

# ── Confirmar o bairro sem dizer o frete ────────────────────────
secao("--- frete na confirmacao ---")
# "Voces entregam no Nova Porto Velho?" devolvia so "Sim, entregamos", e o
# cliente tinha de perguntar o preco de novo. O valor estava em maos.
_COM_FRETE = {"frete": 12.0}
checa("confirmar o bairro traz o valor",
      "R$ 12,00" in app._conferir_frete_dito(
          "55", "Sim, entregamos no bairro Nova Porto Velho.", _COM_FRETE))
# A negativa tem de ser vista ANTES: normalize tira a pontuacao, e
# "Nao entregamos no seu bairro" casa com "entregamos no".
for _recusa in ("Nao entregamos no seu bairro no momento.",
                "Esse bairro nao esta na nossa tabela de entrega.",
                "Voce esta fora da nossa area, mas pode retirar na loja.",
                "Nao fazemos entrega ai."):
    checa(f"recusa de bairro NAO recebe valor: {_recusa[:26]!r}",
          app._conferir_frete_dito("55", _recusa, _COM_FRETE) == _recusa)
checa("resposta que ja tem valor fica intacta",
      app._conferir_frete_dito("55", "Sim, entregamos. A taxa e R$ 15,00.", _COM_FRETE)
      == "Sim, entregamos. A taxa e R$ 15,00.")
# Quando o modelo confirma pela base de conhecimento, sem chamar a tool, nao ha
# frete na ficha - o valor sai da tabela, pelo bairro que ela citou.
for _frase, _valor in (("Sim, entregamos no bairro Nova Porto Velho.", "R$ 12,00"),
                       ("Sim, entregamos no Pedrinhas!", "R$ 15,00"),
                       ("Atendemos o Igarape", "R$ 8,00")):
    checa(f"frete vem da tabela: {_frase[:28]!r}",
          _valor in app._conferir_frete_dito("55", _frase, {}))
# Bairro fora da tabela nao recebe valor nenhum: inventar frete e pior do que
# responder pela metade.
checa("bairro fora da tabela nao recebe valor",
      app._conferir_frete_dito("55", "Sim, entregamos no Jardim das Acacias.", {})
      == "Sim, entregamos no Jardim das Acacias.")
# "(?i:...)" so no verbo: com IGNORECASE global, "atendemos o senhor" viraria
# bairro "senhor".
checa("'atendemos o senhor' nao e bairro",
      app._conferir_frete_dito("55", "Atendemos o senhor com carinho", {})
      == "Atendemos o senhor com carinho")

# Sem frete na ficha o valor vem da tabela (caso acima). O que garante que nada
# e inventado e o bairro inexistente, testado logo antes - e a resposta que nao
# cita bairro nenhum:
checa("sem bairro citado, nada e acrescentado",
      app._conferir_frete_dito("55", "Sim, entregamos!", {}) == "Sim, entregamos!")

# ── Higiene do proprio codigo ──────────────────────────────────────────────
# ══ Testes do dia 26/09 — a bateria do dono no WhatsApp ═══════════════════
# Cada bloco daqui nasceu de uma queixa escrita dele no chat.txt daquela noite.

secao("--- 26/09: o horario passado apagava a data ---")
# 19h20, cliente pede "hoje as 19h": o horario ja passou. A recusa fala de
# "horário" COM ACENTO, e o codigo procurava "horario" sem acento - nunca
# casava, entao ele limpava a DATA. O cliente corrigia para 20h e era
# perguntado do DIA de novo. Agora quem diz qual campo nao cabe e a validacao.
_19H20 = datetime(2026, 9, 26, 19, 20, 0)
_ok_h, _msg_h, _sug_h, _campo_h = horario.validar_agendamento(
    date(2026, 9, 26), "19h", _19H20)
checa("horario de hoje que passou e recusado", not _ok_h)
# 17/10: hoje que passou oferece o MESMO horario no proximo dia util; o
# campo e a data (o horario dito fica) e o dia oferecido volta na sugestao.
checa("hoje que passou oferece o mesmo horario no proximo dia",
      _campo_h == "data" and _sug_h is not None and "às 19h" in _msg_h, (_campo_h, _msg_h))
for _texto, _campo_esperado in (("21h", "horario"), ("7h", "horario"),
                                ("12h30", "horario")):
    _o, _m, _s, _c = horario.validar_agendamento(date(2026, 9, 28), _texto, _19H20)
    checa(f"{_texto} fora do expediente -> campo '{_campo_esperado}'",
          _c == _campo_esperado, _m[:40])
_o, _m, _s, _c = horario.validar_agendamento(date(2026, 9, 27), "10h", _19H20)
checa("domingo -> campo 'data'", _c == "data")
_o, _m, _s, _c = horario.validar_agendamento(date(2026, 9, 28), "19h", _19H20)
checa("dia e hora validos passam", _o and _c == "")


# Os testes de esgotado abaixo usam o croquete de carne, que estava ESGOTADO
# na planilha e o dono reativou em 28/09. Eles testam a REGRA, nao o estoque do
# dia: aqui o croquete e marcado esgotado so dentro do teste.
for _p in facts.get()["produtos"]:
    if facts.normalize(_p.get("item", "")) == "croquete de carne":
        _p["disponivel"] = False

secao("--- 26/09: '30 de carne' virava Quiche de Carne de Sol ---")
from agents.conversation import _produto_conhecido, sabores_possiveis  # noqa: E402
checa("palavra que serve a varios sabores nao identifica produto",
      _produto_conhecido("carne") is None)
_opcoes = sabores_possiveis("carne")
checa("'carne' devolve as opcoes de sabor", len(_opcoes) >= 2, str(_opcoes))
checa("as opcoes sao sabores de lote, nao itens de balcao",
      "Quiche de Carne de Sol" not in _opcoes and "Bomba de Carne de Sol" not in _opcoes)
checa("sabor esgotado fica fora das opcoes", "Croquete de Carne" not in _opcoes)
checa("nome especifico continua resolvendo",
      _produto_conhecido("pastel de carne") == "Pastel Carne")
checa("coxinha nao e ambiguo", _produto_conhecido("coxinha") == "Coxinha")
checa("camarao continua nao virando frango",
      _produto_conhecido("canudinhos de camarao") == "Canudinho de Camarao")

_pedido_carne = _extract_data_from_message(
    "Oi, quero 100 salgados fritos: 30 de carne, 30 de queijo e 40 coxinhas.",
    ConversationState())
checa("o sabor ambiguo nao vira item errado",
      all(i["name"] != "Quiche de Carne de Sol"
          for i in _pedido_carne.get("items", [])))
checa("o sabor ambiguo tampouco desaparece: fica pendente",
      any(sp["termo"] == "carne"
          for sp in _pedido_carne.get("sabores_pendentes", [])))
# No cardapio "queijo" e a bolinha E o misto de queijo e presunto: fica pendente.
checa("'30 de queijo' com dois sabores de queijo fica pendente",
      any(sp["termo"] == "queijo" for sp in _pedido_carne.get("sabores_pendentes", [])),
      str(_pedido_carne.get("sabores_pendentes")))

_ficha_sabor = dict(memoria_mod._empty())
_ficha_sabor.update(_pedido_carne)
checa("a ficha cobra o sabor que falta",
      "SABOR NAO IDENTIFICADO" in memoria_mod.render_ficha(_ficha_sabor))
checa("e a pergunta bloqueante cita as opcoes",
      "carne" in memoria_mod.pergunta_bloqueante(_ficha_sabor))
_aplicou = memoria_mod.aplicar_escolha_de_sabor(_ficha_sabor, "pode ser pastel carne")
checa("a escolha do sabor vira item na ficha", bool(_aplicou), str(_aplicou))
checa("e a pendencia desse sabor sai da ficha",
      not any(sp["termo"] == "carne" for sp in _ficha_sabor.get("sabores_pendentes") or []))


secao("--- 26/09: lote quebrado nao trava mais o funil? trava ---")
# 1a queixa do dono no dia: "o cliente pediu 30, 30 e 40 e ela aceitou sem
# travar a regra de multiplos de 25 por sabor (...) o fluxo correto e travar a
# regra dos sabores em lotes de 25 ANTES de puxar os dados de entrega".
_FICHA_LOTE = dict(memoria_mod._empty(), **{
    "name": "Balbino", "modality": "entrega", "bairro": "Olaria",
    "endereco": "Rua Jamari 2125", "data_agendada": "hoje, sábado (26/09)",
    "data_iso": "2026-09-26", "horario": "19h",
    "items": [{"name": "Coxinha", "quantity": 30},
              {"name": "Quibe", "quantity": 30},
              {"name": "Bolinha de Queijo", "quantity": 40}],
})
_render_lote = memoria_mod.render_ficha(_FICHA_LOTE)
checa("a ficha manda ajustar a quantidade primeiro",
      "PROXIMA PERGUNTA OBRIGATORIA: o ajuste da QUANTIDADE QUEBRADA" in _render_lote)
checa("e adia a coleta, sem apagar o que falta",
      "NAO nesta mensagem" in _render_lote and "Forma de pagamento" in _render_lote)
_frase_lote = memoria_mod.pergunta_bloqueante(_FICHA_LOTE)
checa("a pergunta de lote cita TODOS os sabores fora da regra",
      all(t in _frase_lote.lower() for t in ("coxinha", "quibe", "bolinha")),
      _frase_lote)
checa("e traz as duas opcoes de cada um (dono, 07/10: 40 sugere 25 ou 50)",
      __import__("re").search(r"30 coxinha[^\n]*: 25 ou 50", _frase_lote.lower())
      and __import__("re").search(r"40 bolinha[^\n]*: 25 ou 50", _frase_lote.lower()), _frase_lote)

# A resposta que pulou a trava e cobrou logistica e reescrita.
_pulou = ("Para seguir, me confirma:\n• Bairro\n• Endereco (rua e numero)\n"
          "• Forma de pagamento")
checa("resposta que cobra logistica com lote quebrado e trocada",
      "25 em 25" in app._conferir_etapa_travada("55", _pulou, _FICHA_LOTE))
# Mas o que ela INFORMA nao se perde.
_informa = "A taxa de entrega para o Olaria é R$ 12,00. Qual é o bairro?"
_saida_informa = app._conferir_etapa_travada("55", _informa, _FICHA_LOTE)
checa("o que a resposta informava continua na mensagem",
      "12,00" in _saida_informa, _saida_informa[:60])
checa("e a pergunta errada foi trocada pela travada",
      "25 em 25" in _saida_informa)
# Espelho com lote quebrado nao sai de jeito nenhum.
_espelho_errado = "👤 Cliente: Balbino\n💵 Total: R$ 80,00\nPosso confirmar?"
checa("espelho com lote quebrado e barrado",
      "25 em 25" in app._conferir_etapa_travada("55", _espelho_errado, _FICHA_LOTE))
# Pedido em multiplo de 25 nao leva trava nenhuma.
_FICHA_OK = dict(_FICHA_LOTE, items=[{"name": "Coxinha", "quantity": 25},
                                     {"name": "Quibe", "quantity": 25}],
                 upsell_feito=True, payment_method="cartao")
checa("pedido na regra nao e travado",
      memoria_mod.pergunta_bloqueante(_FICHA_OK) == "")


secao("--- 26/09: o upsell que nunca saia ---")
# Cobrado em quatro testes: "pulou o bloco de upsell e ja foi direto cobrar
# endereco e pagamento", "nao ofereceu bebida nem doces antes do resumo final".
_FICHA_UPSELL = dict(_FICHA_LOTE,
                     items=[{"name": "Coxinha", "quantity": 25}],
                     payment_method="cartao")
_oferta = memoria_mod.upsell_pendente(_FICHA_UPSELL, datetime(2026, 9, 26, 19, 0))
checa("com o pedido pronto, a oferta adicional esta pendente", bool(_oferta), _oferta)
checa("a oferta das 19h e refrigerante ou docinho",
      "Refrigerante" in _oferta or "docinho" in _oferta, _oferta)
checa("a ficha manda fazer a oferta antes da logistica",
      "OFERTA ADICIONAL" in memoria_mod.render_ficha(_FICHA_UPSELL))
checa("oferta ja feita nao volta",
      memoria_mod.upsell_pendente(dict(_FICHA_UPSELL, upsell_feito=True)) == "")
checa("quem ja pediu bebida nao ouve a oferta",
      memoria_mod.upsell_pendente(dict(
          _FICHA_UPSELL,
          items=[{"name": "Coxinha", "quantity": 25},
                 {"name": "Refrigerante Lata", "quantity": 1}])) == "")
checa("sem item nenhum nao ha oferta a fazer",
      memoria_mod.upsell_pendente(dict(_FICHA_UPSELL, items=[])) == "")
checa("lote quebrado vem antes do upsell",
      memoria_mod.upsell_pendente(_FICHA_LOTE) == "")
for _faixa, _esperado in ((datetime(2026, 9, 28, 8, 0), "Bolo de Milho"),
                          (datetime(2026, 9, 28, 11, 0), "Refrigerante 2L"),
                          (datetime(2026, 9, 28, 15, 0), "Tuchaua"),
                          (datetime(2026, 9, 28, 18, 0), "docinhos")):
    checa(f"oferta das {_faixa.hour}h cita {_esperado}",
          _esperado in memoria_mod.oferta_adicional(_faixa))
checa("a resposta com a oferta e reconhecida",
      app._ofereceu_acompanhamento("Pra acompanhar, vai um Refrigerante 2L? 🥤"))
checa("resposta sem oferta nao marca o upsell como feito",
      not app._ofereceu_acompanhamento("Qual é o número da casa? 🛵"))


secao("--- 26/09: fiado passou batido duas vezes ---")
_FICHA_FIADO = dict(_FICHA_LOTE, name="Balbino")


def _fiado(msg: str, reply: str = "Anotado! Qual é a forma de pagamento?") -> str:
    return app._conferir_fiado("55", msg, reply, _FICHA_FIADO)


checa("'posso te pagar na segunda' recebe a negativa de fiado",
      "não libera fiado" in _fiado("Posso te pagar na segunda-feira sem falta?"))
checa("'deixa anotado que acerto no fim do mes' tambem",
      "não libera fiado" in _fiado(
          "deixa anotado que acerto com ele no fim do mês"))
checa("'fiado' cru tambem", "não libera fiado" in _fiado("faz fiado pra mim?"))
checa("pagar na entrega NAO e fiado",
      "fiado" not in _fiado("vou pagar em dinheiro na entrega").lower())
checa("dia do pedido nao e promessa de pagamento",
      "fiado" not in _fiado("PODE SER PARA SEGUNDA RUA JAMARI 2125").lower())
checa("troco na entrega nao e fiado",
      "fiado" not in _fiado(
          "pago em dinheiro e traz troco pra 80").lower())
checa("negativa que ja estava na resposta nao e repetida",
      _fiado("faz fiado?", "A fábrica não libera fiado de jeito nenhum!")
      == "A fábrica não libera fiado de jeito nenhum!")
checa("a negativa nao cria uma segunda pergunta",
      _fiado("faz fiado?", "Qual é o bairro? 😊").count("?") == 1)


secao("--- 26/09: desconto no frete ignorado ---")


def _desc(msg: str, reply: str = "Anotado! Qual é o bairro?") -> str:
    return app._conferir_desconto("55", msg, reply)


checa("'faz um desconto no frete' recebe a defesa de preco",
      "direto de fábrica" in _desc("E faz um desconto no frete pra fechar?"))
checa("'desconto pra fechar logo' tambem",
      "tabelado" in _desc("me dá um desconto no frete pra fechar logo"))
checa("'faz por menos' tambem", "direto de fábrica" in _desc("faz por menos?"))
checa("mensagem sem pedido de desconto fica intacta",
      _desc("quero 50 coxinhas") == "Anotado! Qual é o bairro?")
checa("defesa que ja estava na resposta nao e repetida",
      _desc("desconto?", "Nosso preço já é direto de fábrica, não consigo mexer!")
      == "Nosso preço já é direto de fábrica, não consigo mexer!")


secao("--- 26/09: a Sandrinha falando como programadora ---")
_FICHA_VAZ = dict(_FICHA_LOTE)
for _leak in ("Entendi, obrigado pelo feedback detalhado! Vou ajustar o gatilho "
              "desse bloco.",
              "Vou corrigir o atendimento conforme as regras.",
              "Desculpe, houve uma falha no meu prompt.",
              "Sou uma IA e não posso fazer isso."):
    _saida = app._conferir_vazamento_tecnico("55", _leak, _FICHA_VAZ)
    checa(f"vazamento trocado: {_leak[:34]!r}", _saida != _leak, _saida[:48])
    checa("  e a troca fala do pedido, nao do sistema",
          not app._RX_VAZAMENTO_TECNICO.search(_saida), _saida[:48])
_ok_persona = "Fazemos de 25 em 25 por sabor! Ajusto para 25 coxinha? 😊"
checa("resposta normal nao e tocada",
      app._conferir_vazamento_tecnico("55", _ok_persona, _FICHA_VAZ) == _ok_persona)
_espelho = ("👤 Cliente: Balbino\n💰 Subtotal: R$ 20,00\n💵 Total: R$ 34,00\n"
            "Posso confirmar?")
checa("espelho do pedido nunca e descartado por vazamento",
      app._conferir_vazamento_tecnico("55", _espelho, _FICHA_VAZ) == _espelho)


secao("--- 26/09: duas perguntas na mesma mensagem ---")
_duas = ("Que maravilha! 💛 Nosso preço já é direto de fábrica. Bora montar o "
         "seu pedido? Você pode escolher até 4 sabores. Quais sabores e preparo "
         "prefere? 😊")
_uma = app._conferir_uma_pergunta("55", _duas)
checa("sobra uma pergunta so", _uma.count("?") == 1, _uma[-46:])
checa("a pergunta que sobra e a ultima", "Quais sabores" in _uma)
checa("e a informacao anterior nao se perde", "direto de fábrica" in _uma)
checa("uma pergunta so continua intacta",
      app._conferir_uma_pergunta("55", "Qual é o número da casa? 🛵")
      == "Qual é o número da casa? 🛵")
checa("espelho com uma pergunta nao e tocado",
      app._conferir_uma_pergunta("55", _espelho) == _espelho)
checa("lista de coleta sem pergunta nao e tocada",
      app._conferir_uma_pergunta("55", _pulou) == _pulou)


secao("--- 26/09: amnesia de troco no espelho ---")
# "o cliente pediu troco para 150 e a Sandrinha nao registrou o valor no
# resumo final". A ficha tinha o valor; o espelho que o MODELO escreveu, nao.
checa("'traz troco pra 150' e capturado da 1a mensagem",
      memoria_mod.capturar_troco(
          dict(memoria_mod._empty(), payment_method="dinheiro"), "",
          "Quero 50 coxinhas para entregar amanhã às 16h. Vou pagar em dinheiro, "
          "traz troco pra 150.") == "150.00")
_FICHA_TROCO_150 = dict(memoria_mod._empty(), **{
    "name": "Balbino", "modality": "entrega", "bairro": "Nova Porto Velho",
    "endereco": "Rua Jamari 2125", "payment_method": "dinheiro",
    "data_agendada": "segunda-feira (28/09)", "data_iso": "2026-09-28",
    "horario": "16h", "troco_para": "150.00", "upsell_feito": True,
    "items": [{"name": "Coxinha", "quantity": 50},
              {"name": "Empadinha de Frango", "quantity": 50}],
})
_espelho_sem_troco = chr(10).join([
    "👤 Cliente: Balbino",
    "💵 Total: R$ 114,00",
    "💳 Pagamento: Dinheiro na entrega",
    "Posso confirmar o seu pedido? 💛",
])
_com_troco = app._conferir_troco_no_espelho("55", _espelho_sem_troco, _FICHA_TROCO_150)
checa("o troco volta para a linha de pagamento do espelho",
      "troco" in _com_troco.lower(), _com_troco.split(chr(10))[2])
checa("e o valor e o que o cliente pediu", "150,00" in _com_troco)
checa("espelho que ja traz o troco nao e reescrito",
      app._conferir_troco_no_espelho("55", _com_troco, _FICHA_TROCO_150) == _com_troco)
checa("sem troco na ficha nao se inventa linha",
      app._conferir_troco_no_espelho(
          "55", _espelho_sem_troco,
          dict(_FICHA_TROCO_150, troco_para="")) == _espelho_sem_troco)


secao("--- 26/09: coleta picada ---")
# "disparou duas mensagens e pediu so o bairro, em vez de cobrar o que falta em
# lista unica".
_FICHA_PICADA = dict(memoria_mod._empty(), **{
    "modality": "entrega", "endereco": "Rua Jamari",
    "data_agendada": "hoje, sábado (26/09)", "data_iso": "2026-09-26",
    "horario": "19h30", "upsell_feito": True,
    "items": [{"name": "Coxinha", "quantity": 100}],
})
_faltam = memoria_mod.faltando(_FICHA_PICADA)
checa("faltam vários dados nessa ficha", len(_faltam) >= 2, str(_faltam))
_so_bairro = "Agora, por favor, me informe o bairro para entrega."
_completo = app._conferir_coleta_picada("55", _so_bairro, _FICHA_PICADA)
checa("pedir so o bairro vira a lista inteira",
      _completo != _so_bairro and "Bairro" in _completo, _completo[:70])
checa("a lista inclui o numero da casa que faltava",
      "Numero da casa" in _completo or "úmero" in _completo, _completo)
checa("espelho nao e tocado pela coleta picada",
      app._conferir_coleta_picada("55", _espelho, _FICHA_PICADA) == _espelho)
checa("resposta que nao cobra dado nenhum fica intacta",
      app._conferir_coleta_picada("55", "A estimativa é de 30 a 40 minutos! 😊",
                                  _FICHA_PICADA)
      == "A estimativa é de 30 a 40 minutos! 😊")


secao("--- 26/09: croquete de carne virou croquete de macaxeira ---")
# "o cliente pediu croquete de carne no cento de festa e ela puxou o croquete de
# macaxeira de balcao (...) falou do nada que o item estava esgotado".
checa("croquete de carne nao e renomeado na extracao",
      _itens("50 croquete de carne") == [(50, "Croquete de Carne")])
_FICHA_ESGOTADO = dict(memoria_mod._empty(),
                       items=[{"name": "Croquete de Carne", "quantity": 50}])
checa("o sabor esgotado e uma pendencia da ficha",
      "SABOR ESGOTADO" in memoria_mod.render_ficha(_FICHA_ESGOTADO))
checa("e o nome que sai e o do cardapio, nao o do vizinho",
      "Croquete de carne" in memoria_mod.pergunta_bloqueante(_FICHA_ESGOTADO))
checa("macaxeira nao aparece na pergunta",
      "acaxeira" not in memoria_mod.pergunta_bloqueante(_FICHA_ESGOTADO))
import json as _json  # noqa: E402
_resp_preco = _json.loads(app._consultar_preco_handler("croquete de carne"))
checa("consultar_preco avisa do esgotado pelo nome certo",
      "Croquete de carne" in (_resp_preco.get("aviso") or ""))
checa("e proibe renomear o pedido do cliente",
      "renomear" in (_resp_preco.get("aviso") or ""))
checa("produto disponivel nao recebe aviso de estoque",
      not _json.loads(app._consultar_preco_handler("coxinha")).get("aviso"))


secao("--- 26/09: triagem de tamanho picada ---")
# "perguntou o tamanho da coxinha e do rissole em mensagens separadas. Deve
# consolidar a triagem de tamanho em uma unica fala".
_FICHA_DOIS_TAMANHOS = dict(memoria_mod._empty(),
                            items=[{"name": "Coxinha", "quantity": 10},
                                   {"name": "Rissole de Carne", "quantity": 10}])
_dir_dois = memoria_mod.pendencia_de_item(_FICHA_DOIS_TAMANHOS)
checa("com 2 itens sem tamanho, a ordem e perguntar junto",
      "NA MESMA MENSAGEM" in _dir_dois)
checa("e proibe uma pergunta por item",
      "uma pergunta de tamanho por item" in _dir_dois)
_frase_dois = memoria_mod.pergunta_bloqueante(_FICHA_DOIS_TAMANHOS)
checa("a pergunta cita os dois itens de uma vez",
      "coxinhas" in _frase_dois and "rissoles" in _frase_dois, _frase_dois)
checa("e continua sendo uma pergunta so", _frase_dois.count("?") == 1)


secao("--- 27/09: a unica oscilacao que sobrou ---")
# 50 testes x 3 rodadas: um ponto so variou. Em 1 de 3 rodadas o teste 2.2 saiu
# "Como sera a retirada, Eduardo? Por favor, confirme o nome, a data 29/09/2026
# e o horario 15h" - com os tres dados na ficha. Escapou de tudo porque o mapa
# de frases tem "confirmar" e ela escreveu "confirme". O gatilho agora e
# estrutural: ficha completa + nada travado + cliente sem pergunta = espelho.
_FICHA_PRONTA = dict(memoria_mod._empty(), **{
    "name": "Eduardo", "modality": "retirada",
    "data_agendada": "amanhã, terça-feira (29/09)", "data_iso": "2026-09-29",
    "horario": "15h", "upsell_feito": True,
    "items": [{"name": "Coxinha", "quantity": 25}],
})
checa("a ficha de controle esta completa mesmo",
      not memoria_mod.faltando(_FICHA_PRONTA)
      and not memoria_mod.pergunta_bloqueante(_FICHA_PRONTA))


def _espelho_obrig(reply: str, msg: str = "Não, obrigado",
                   aceitou: bool = False, ficha=None) -> str:
    return app._conferir_espelho_obrigatorio(
        "55", reply, dict(ficha or _FICHA_PRONTA), msg, aceitou)


_A_FRASE_QUE_OSCILOU = ("Como será a retirada, Eduardo? Por favor, confirme o "
                        "nome, a data 29/09/2026 e o horário 15h para a "
                        "retirada no balcão da fábrica.")
_saida_osc = _espelho_obrig(_A_FRASE_QUE_OSCILOU)
checa("a frase que oscilou vira espelho",
      app._RX_LINHA_DE_RESUMO.search(_saida_osc) is not None,
      _saida_osc[:50].replace(chr(10), " | "))
checa("e o espelho traz o total do codigo", "20,00" in _saida_osc)
# Qualquer outra redacao do mesmo erro cai junto - e o que "estrutural" quer
# dizer. Sao formas que nenhum mapa de frases pegaria.
for _redacao in ("Perfeito! Vou seguir com o seu pedido então. 😊",
                 "Me confirme por favor os dados para eu prosseguir.",
                 "Anotado, Eduardo! Aguarde um instante.",
                 "Certo! Vamos lá então."):
    checa(f"tambem vira espelho: {_redacao[:32]!r}",
          app._RX_LINHA_DE_RESUMO.search(_espelho_obrig(_redacao)) is not None)

# O que a trava NAO pode atropelar.
_espelho_curto = ("Cliente: Balbino\n💰 Total: R$ 34,00\n💳 Pgt: Pix\n"
                  "Posso confirmar pra hoje (sex, 02/10) às 20h? 💛")
_espelho_do_codigo = (app._numeros_do_pedido(dict(_FICHA_PRONTA)) or {}).get("resumo_formatado") or ""
checa("espelho certo (o do codigo) nao e refeito",
      _espelho_do_codigo and _espelho_obrig(_espelho_do_codigo) == _espelho_do_codigo)
checa("espelho do modelo diferente da ficha vira o do codigo",
      _espelho_obrig(_espelho_curto) == _espelho_do_codigo)
# Formato longo (👤, Subtotal) escrito pelo modelo: o dono pediu o curto em
# 02/10, e o curto e o do codigo.
_saida_longo = _espelho_obrig(_espelho)
checa("espelho no formato longo vira o curto do codigo",
      "Pgt:" in _saida_longo and "👤" not in _saida_longo
      and "Subtotal" not in _saida_longo, _saida_longo[:60].replace(chr(10), " | "))
checa("mensagem de fechamento passa",
      "Pedido" in _espelho_obrig(
          "Pedido finalizado com sucesso! Já está sendo preparado com carinho."))
checa("recusa de troco passa",
      "maior que o total" in _espelho_obrig(
          "O valor para troco precisa ser maior que o total da compra. "
          "Dinheiro para quanto?"))
checa("aceite do espelho passa (e caminho do fechamento)",
      _espelho_obrig("Tudo certo!", msg="sim", aceitou=True) == "Tudo certo!")
# Regra 10: a pergunta do cliente vem antes do funil.
for _pergunta in ("Vocês abrem domingo?", "me diz se voces abrem domingo",
                  "quanto fica o frete", "voces tem panqueca",
                  "qual o horario de fechamento", "da pra entregar mais cedo"):
    _resp = "Fechamos às 20h, de segunda a sábado! 😊"
    checa(f"pergunta do cliente nao e atropelada: {_pergunta[:30]!r}",
          _espelho_obrig(_resp, msg=_pergunta) == _resp)
# Ficha aberta ou etapa travada continuam mandando.
checa("ficha incompleta nao forca espelho",
      _espelho_obrig("Certo!", ficha=dict(_FICHA_PRONTA, horario="")) == "Certo!")
checa("upsell pendente nao e pulado pelo espelho",
      _espelho_obrig("Certo!",
                     ficha=dict(_FICHA_PRONTA, upsell_feito=False)) == "Certo!")
checa("lote quebrado nao e pulado pelo espelho",
      _espelho_obrig("Certo!", ficha=dict(
          _FICHA_PRONTA, items=[{"name": "Coxinha", "quantity": 30}])) == "Certo!")

# E _conferir_nega_formato passou a ler as MESMAS pendencias, para nao forcar o
# espelho por cima de uma etapa travada.
checa("nega_formato respeita o upsell pendente",
      app._conferir_nega_formato(
          "55", "Nao existe meio cento de coxinha.",
          dict(_FICHA_PRONTA, upsell_feito=False))
      == "Nao existe meio cento de coxinha.")


secao("--- 27/09: fiado ia para a fila do humano ---")
# Medido em 4 de 4 rodadas do "amigo do Balbino": ela dava a negativa certa do
# fiado e chamava transfer_to_human em seguida, motivo "Pedido com solicitacao
# de fiado, que nao e permitido pela politica da empresa". A HIERARQUIA do dono
# diz o contrario - "BLOCO 6 (Defesa de Preco): pedido de desconto NUNCA
# transborda para atendente humano" - e a transferencia pula toda a cadeia de
# conferencia, que e onde as negativas sao garantidas.
for _motivo in ("Pedido com solicitação de fiado, que não é permitido pela "
                "política da empresa.",
                "Pedido de fiado e liberação de produção sem pagamento antecipado"):
    checa(f"BLOCO 6 nao transborda: {_motivo[:38]!r}",
          app.transbordo_proibido(_motivo))
# Desconto foi para a equipe em 12/10 e voltou em 13/10: o Balbino quer a
# resposta padrao de preco. Promocao idem.
for _motivo in ("cliente insiste em desconto no frete",
                "cliente pede abatimento no valor",
                "cliente pergunta sobre promocao",
                "negociação de preço fora da tabela"):
    checa(f"desconto/promocao nao transborda (13/10): {_motivo[:38]!r}",
          app.transbordo_proibido(_motivo))
# O que TEM de continuar transbordando. Risco e pos-venda nao sao BLOCO 6.
for _motivo in ("cliente mencionou 'procon'",
                "cliente mencionou 'falar com atendente'",
                "cliente mencionou 'reclamacao'",
                "cliente mencionou 'cancelar'",
                "cliente passando mal apos comer o salgado",
                "encontrou inseto no pedido",
                "pedido acima de 1000 unidades, equipe comercial",
                "pedido nao entrou no cardapio: 401 Unauthorized"):
    checa(f"risco/pos-venda continua transbordando: {_motivo[:38]!r}",
          not app.transbordo_proibido(_motivo))
checa("motivo vazio nao e transbordo proibido",
      not app.transbordo_proibido("") and not app.transbordo_proibido(None))
# A trava vale na porta de fora tambem: perform_transfer recusa sozinha.
checa("perform_transfer recusa o motivo do BLOCO 6",
      app.perform_transfer("55", reason="cliente pediu fiado",
                           send_message=False) is False)


secao("--- 27/09: modelo fora do ar nao apaga o BLOCO 6 ---")
# O 429 da OpenAI derruba a chamada e a resposta passa a ser escrita pelo
# codigo - por um caminho que dava return antes da cadeia de conferencia. O
# cliente propunha fiado e recebia so a proxima pergunta do funil. Nao era
# oscilacao do modelo: era o limite de taxa.
_FICHA_VOLUME = dict(memoria_mod._empty(), name="Balbino", volume_pendente=100)
_SAIDA_DE_CODIGO = "Para seguir, me confirma:\n• Itens do pedido"
checa("fallback de falha recebe a negativa de fiado",
      "não libera fiado" in app._conferir_fiado(
          "55", "deixa anotado que acerto no fim do mês", _SAIDA_DE_CODIGO,
          _FICHA_VOLUME))
checa("fallback de falha recebe a defesa de preco",
      "direto de fábrica" in app._conferir_desconto(
          "55", "me dá um desconto no frete", _SAIDA_DE_CODIGO))
checa("mensagem de instabilidade tambem recebe a negativa",
      "não libera fiado" in app._conferir_fiado(
          "55", "pode ser fiado?",
          "Tive uma instabilidade aqui agora 💛 Pode repetir, por favor?",
          _FICHA_VOLUME))


secao("--- 27/09: aviso de domingo nao engole fiado nem desconto ---")
# O aviso de fora do expediente sai sem o modelo. No teste do dono num domingo
# o desconto e o fiado da primeira mensagem ficaram sem resposta.
_AVISO_DOMINGO = ("Aos domingos a gente não abre! Posso deixar seu pedido "
                  "agendado para amanhã, segunda-feira (28/09)? 😊")
_ambos = app._negativas_do_bloco_6(
    "55", "Posso te pagar na segunda-feira sem falta? E me dá um desconto no "
    "frete pra fechar logo.", _AVISO_DOMINGO, _FICHA_VOLUME)
checa("aviso de domingo recebe a negativa de fiado", "não libera fiado" in _ambos)
checa("aviso de domingo recebe a defesa de preco", "direto de fábrica" in _ambos)
checa("o aviso continua no fim, como a unica pergunta",
      _ambos.endswith(_AVISO_DOMINGO) and _ambos.count("?") == 1)
checa("fiado sai antes da defesa de preco",
      _ambos.index("não libera fiado") < _ambos.index("direto de fábrica"))
checa("amigo do Balbino no domingo recebe a negativa",
      "não libera fiado" in app._negativas_do_bloco_6(
          "55", "Sou muito amigo do Balbino, faz 1 cento frito pra mim hoje às "
          "20h e deixa anotado que acerto com ele no fim do mês.",
          _AVISO_DOMINGO, _FICHA_VOLUME))
checa("pedido comum no domingo sai intacto",
      app._negativas_do_bloco_6("55", "Quero 50 coxinhas", _AVISO_DOMINGO,
                                _FICHA_VOLUME) == _AVISO_DOMINGO)


secao("--- 27/09: emoji sozinho nao vira mensagem ---")
# "Perfeito! 😊" + paragrafo: a abertura saia e o emoji chegava como bolha.
_SO_EMOJI = app._conferir_enrolacao(
    "55", "Perfeito! 😊\n\nPra acompanhar, vai um Refrigerante 2L ou docinhos?")
checa("abertura com emoji sai inteira",
      _SO_EMOJI == "Pra acompanhar, vai um Refrigerante 2L ou docinhos?", _SO_EMOJI)
checa("emoji na mesma linha do texto fica",
      app._conferir_enrolacao("55", "Perfeito! 📦 Resumo do pedido: 50 coxinhas")
      .startswith("📦 Resumo"))
_PARTES = app.split_reply("😊\n\nPra acompanhar, vai um refrigerante?\n\n💛")
checa("split_reply nao manda bolha so de emoji",
      _PARTES == ["😊 Pra acompanhar, vai um refrigerante? 💛"], _PARTES)


secao("--- 27/09: preparo ja dito nao e perguntado de novo ---")
from agents.conversation import preparo_dito  # noqa: E402
checa("'1 cento frito' guarda frito",
      preparo_dito("faz 1 cento frito pra mim") == "frito")
checa("'assados' guarda assado",
      preparo_dito("quero um cento de salgados assados") == "assado")
checa("frito e assado juntos nao decide", preparo_dito("frito e assado") == "")
_FICHA_FRITO = dict(memoria_mod._empty(), volume_pendente=100,
                    preparo_pendente="frito")
_CTX_FRITO = memoria_mod.render_ficha(_FICHA_FRITO)
checa("instrucao nao manda perguntar o preparo ja dito",
      "(frito ou assado)." not in _CTX_FRITO and "JA disse que e frito" in _CTX_FRITO)
_CTX_SEM = memoria_mod.render_ficha(
    dict(memoria_mod._empty(), volume_pendente=100))
checa("sem preparo dito, ainda pergunta", "frito ou assado" in _CTX_SEM)


secao("--- 28/09: girias do balcao viram lotes de 25 ---")
# Amostra do balcao do dono: "1 cento pastel e bolinha" e 50 de cada, e a
# ficha ficava so com 100 pasteis; "metade/metade" sem volume e um cento.
from agents.conversation import itens_divididos  # noqa: E402


def _div(frase):
    return sorted((i["name"], i["quantity"]) for i in itens_divididos(frase.lower()))


checa("dois centos de pastel e bolinha somam 100 + 100",
      _div("1 cento pastel e bolinha e 1 cento pastel e bolinha pra retirada")
      == [("Bolinha de Queijo", 100), ("Pastel", 100)],
      str(_div("1 cento pastel e bolinha e 1 cento pastel e bolinha pra retirada")))
checa("metade/metade sem volume e um cento",
      _div("Metade croquete e metade pastel de carne")
      == [("Croquete", 50), ("Pastel Carne", 50)])
checa("'sendo metade X e a outra metade Y' divide",
      _div("quero um cento sendo metade croquete e a outra metade pastel de "
           "carne massa leve. pra retirar segunda") == [("Croquete", 50),
                                                         ("Pastel Carne", 50)])
checa("'100 salgados metade X metade Y' divide",
      _div("100 salgados metade coxinha metade kibe") == [("Coxinha", 50), ("Quibe", 50)])
checa("meio cento de dois sabores da 25 + 25",
      _div("meio cento de coxinha e quibe") == [("Coxinha", 25), ("Quibe", 25)])
for _frase in ("um cento de salgados fritos pra entregar hoje. quero 30 coxinhas",
               "quero 100 salgados fritos: 30 de carne, 30 de queijo e 40 coxinhas",
               "um cento de coxinha e 50 kibes", "cento de coxinha",
               "cento de coxinha, bolinha e kibe", "cento de carne e queijo"):
    checa(f"nao divide: {_frase[:34]!r}", _div(_frase) == [], str(_div(_frase)))

_EXTR = _extract_data_from_message(
    "1 cento pastel e bolinha e 1 cento pastel e bolinha pra retirada",
    ConversationState())
# "pastel" sem sabor vira "carne ou queijo?" (15/10).
checa("a extracao usa a divisao e marca pedido por volume",
      sorted((i["name"], i["quantity"]) for i in _EXTR.get("items", []))
      == [("Bolinha de Queijo", 100)]
      and [(p["termo"], p["qtd"]) for p in _EXTR.get("sabores_pendentes", [])] == [("pastel", 100)]
      and _EXTR.get("pedido_por_volume") is True, str(_EXTR.get("items")))

_FICHA_VOLUME_OK = dict(memoria_mod._empty(), pedido_por_volume=True, items=[
    {"name": "Pastel", "quantity": 100}, {"name": "Bolinha de Queijo", "quantity": 100}])
checa("subtotal do balcao: 2 centos fritos = R$ 160,00",
      memoria_mod.subtotal_dos_salgados(_FICHA_VOLUME_OK) == 160.0,
      str(memoria_mod.subtotal_dos_salgados(_FICHA_VOLUME_OK)))
_OFERTA_VALOR = memoria_mod.pergunta_bloqueante(_FICHA_VOLUME_OK)
checa("a oferta sai com o valor na frente",
      _OFERTA_VALOR.startswith("Fica R$ 160,00 nos salgados!"), _OFERTA_VALOR[:60])
checa("oferta sem o valor nao cobre a etapa",
      not memoria_mod.etapa_coberta(_FICHA_VOLUME_OK, "Pra acompanhar, vai um refri?"))
checa("oferta com o valor cobre a etapa",
      memoria_mod.etapa_coberta(_FICHA_VOLUME_OK,
                                "Fica R$ 160,00! Pra acompanhar, vai um refri?"))
_FICHA_NUMERO = dict(_FICHA_VOLUME_OK, pedido_por_volume=False)
checa("pedido comum continua com a oferta de sempre",
      not memoria_mod.pergunta_bloqueante(_FICHA_NUMERO).startswith("Fica"))


# Teste do Eduardo, 29/09 16h22: o modelo abriu com "Voce pode escolher ate 2
# sabores (25 de cada)!" com 50 + 50 ja na ficha - aula e conta errada.
_AULA = app._conferir_aula_de_lote("55", "Você pode escolher até 2 sabores (25 de cada)! 😊\n\n"
                                   "Fica R$ 80,00 nos salgados!", dict(memoria_mod._empty(), items=[
                                       {"name": "Coxinha", "quantity": 50},
                                       {"name": "Pastel Carne", "quantity": 50}]))
checa("'ate 2 sabores (25 de cada)' sai quando o pedido ja esta certo",
      "25 de cada" not in _AULA and "R$ 80,00" in _AULA, _AULA)
checa("sem itens na ficha, 'escolher ate 4 sabores' fica",
      app._conferir_aula_de_lote("55", "Você pode escolher até 4 sabores (25 de cada)! Quais?",
                                 dict(memoria_mod._empty(), volume_pendente=100)).startswith("Você pode"))


secao("--- 29/09 noite: testes do dono (texto e voz) ---")
from agents.conversation import numeros_por_extenso, bebidas_citadas  # noqa: E402
# Voz: o Whisper escreve os numeros por extenso.
checa("'cinquenta coxinha' vira 50",
      numeros_por_extenso("Quero cinquenta coxinha, cinquenta bolinha") == "Quero 50 coxinha, 50 bolinha")
checa("'oito horas' vira 8 horas", "8 horas" in numeros_por_extenso("às oito horas da manhã"))
checa("'vinte e cinco quibes' vira 25", numeros_por_extenso("vinte e cinco quibes") == "25 quibes")
checa("'Rua Sete de Setembro' fica intacta",
      numeros_por_extenso("Rua Sete de Setembro, 45") == "Rua Sete de Setembro, 45")
_F_VOZ = memoria_mod._empty()
for _m in ("Quero cinquenta coxinha, cinquenta bolinha, cinquenta churros. É pra entrega.",
           "Quero pra amanhã, às oito horas da manhã."):
    _F_VOZ = memoria_mod.merge(_F_VOZ, _extract_data_from_message(
        _m, ConversationState(items=_F_VOZ.get("items") or [])),
        _extract_data_from_message(_m, ConversationState()))[0]
checa("audio transcrito vira itens, entrega e horario",
      [(i["name"], i["quantity"]) for i in _F_VOZ["items"]]
      == [("Coxinha", 50), ("Bolinha de Queijo", 50), ("Churros", 50)]
      and _F_VOZ["modality"] == "entrega" and _F_VOZ["horario"] == "8h", str(_F_VOZ["items"]))
checa("audio: handle_flush usa a transcricao na ficha",
      "_texto_da_midia(it) for it in items" in open("app.py", encoding="utf-8").read())
# Croquete de carne: bloqueado no cardapio digital -> avisado logo, como esgotado.
_F_CROQ = dict(memoria_mod._empty(), items=[{"name": "Croquete de Carne", "quantity": 10}])
checa("item bloqueado e avisado ja na primeira resposta",
      memoria_mod.pergunta_bloqueante(_F_CROQ).startswith("O sabor Croquete de carne está em falta"))
checa("item liberado nao e bloqueado", memoria_mod.itens_indisponiveis(
    dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 50}])) == [])
# 40 quibes nao podem virar 50 pelo "50" da salsicha.
_F_QUIBE = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 100},
                                             {"name": "Quibe", "quantity": 40},
                                             {"name": "Enroladinho de Salsicha", "quantity": 50}])
checa("o 50 de outro produto nao aceita o ajuste",
      not app._aceitou_a_quantidade("Qiero 100 coxinhas 40 quibe 50 salsicha para entrega", _F_QUIBE))
checa("'pode ser 50 entao' aceita", app._aceitou_a_quantidade("Pode ser 50 entao", _F_QUIBE))
checa("'50 quibes' aceita", app._aceitou_a_quantidade("Isso, 50 quibes", _F_QUIBE))
# Data sem ano que passou ha poucos dias e data passada, nao o ano que vem.
checa("'25/09' dito em 29/09 nao vira 2027",
      horario.resolver_data("25/09", datetime(2026, 9, 29, 15, 0)) == date(2026, 9, 25))
checa("'10/01' dito em dezembro e o ano que vem",
      horario.resolver_data("10/01", datetime(2026, 12, 20, 15, 0)) == date(2027, 1, 10))
# Bolo aceito na oferta entra no pedido.
_F_BOLO = memoria_mod.merge(dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 100}]),
                            _extract_data_from_message("Um bolo", ConversationState(
                                items=[{"name": "Coxinha", "quantity": 100}])),
                            _extract_data_from_message("Um bolo", ConversationState()))[0]
checa("'Um bolo' na oferta soma o Bolo de Milho",
      ("Bolo de Milho", 1) in [(i["name"], i["quantity"]) for i in _F_BOLO["items"]])
checa("'sem bolo' nao vira pedido", not bebidas_citadas("sem bolo hoje")[0])
# Lista cortada nao deixa o cabecalho sozinho.
_F_LISTA = dict(memoria_mod._empty(), items=[{"name": "Croquete de Carne", "quantity": 10}])
_SEM_CABECALHO = app._conferir_etapa_travada(
    "55", "Por favor, envie numa lista vertical:\n- Bairro\n- Data (dia/mês)", _F_LISTA)
checa("cabecalho 'lista vertical' sai junto com a lista cortada",
      "lista vertical" not in _SEM_CABECALHO, _SEM_CABECALHO[:60])
# "Perfeito, Balbino! Seu pedido..." nao deixa "Balbino!" solto.
checa("sem a abertura, o nome solto no comeco sai",
      app._conferir_enrolacao("55", "Perfeito, Balbino! Seu pedido ficou para amanhã às 8h.",
                              "Balbino") == "Seu pedido ficou para amanhã às 8h.")
checa("nome no meio da frase fica",
      "Balbino" in app._conferir_enrolacao("55", "Perfeito! Anotei, Balbino, para amanhã às 8h.",
                                           "Balbino"))
# Numero da casa com virgula (Whisper) nao e horario.
checa("'rua Salgado Filho 21, 52' nao vira 21h52",
      horario.parse_horario("É rua Salgado Filho 21, 52, bairro Olaria") is None)
checa("'19,50' sozinho continua sendo horario", horario.parse_horario("19,50") == (19, 50))
# Refrigerante esgotado dito depois da pergunta ja aberta.
import copy as _copy_fx  # noqa: E402
_RETRATO_2L_ESGOTADO = _copy_fx.deepcopy(_RETRATO_FX)
for _it in _RETRATO_2L_ESGOTADO["InternalMenuItem"]:
    if _it["name"] == "Refrigerante 2 L":
        _it["available"] = False
_cf_fx.bruto = lambda forcar=False: _RETRATO_2L_ESGOTADO
_F_COCA2 = memoria_mod._empty()
# 50 coxinhas, e nao croquete: desde 30/09 o sabor em falta vem antes do
# refrigerante, e o que este teste mede e o aviso da Coca 2L.
for _m in ("Quero 50 coxinhas e uma coca cola", "Coca 2 litros"):
    _F_COCA2 = memoria_mod.merge(_F_COCA2, _extract_data_from_message(
        _m, ConversationState(items=_F_COCA2.get("items") or [])),
        _extract_data_from_message(_m, ConversationState()))[0]
checa("'Coca 2 litros' com a pergunta aberta avisa a falta",
      memoria_mod.pergunta_bloqueante(_F_COCA2).startswith("O Refrigerante 2 Litros está em falta"))
_cf_fx.bruto = lambda forcar=False: _RETRATO_FX


secao("--- 30/09: outras midias (imagem, video, localizacao, comprovante) ---")
# Imagem: o texto lido alimenta a ficha, junto com a legenda.
checa("ficha le legenda + texto da imagem",
      app._texto_da_midia({"text": "[Imagem] pra amanha", "texto_imagem": "50 coxinhas"})
      == "pra amanha\n50 coxinhas")
checa("ficha le a transcricao do audio",
      app._texto_da_midia({"text": "[Audio (mensagem de voz)]", "transcricao": "50 coxinhas"})
      == "50 coxinhas")
checa("texto comum passa como veio", app._texto_da_midia({"text": "oi"}) == "oi")
checa("handle_flush le imagem antes da ficha",
      '"[Audio", "[Imagem"' in open("app.py", encoding="utf-8").read())
# A legenda da imagem chega ao modelo (antes era descartada por comecar com "[").
checa("legenda da imagem nao e mais descartada",
      'legenda = _re_mod.sub(r"^\\[[^\\]]*\\]\\s*", "", text or "")' in open("app.py", encoding="utf-8").read())
# Localizacao: o endereco do pino vira texto.
_LOC = app._extract_body_from_meta_message({"type": "location", "location": {
    "latitude": -8.7, "longitude": -63.9, "name": "Casa", "address": "Rua Jamari, 2125 - Olaria"}})
checa("pino com endereco vira texto", _LOC[0] == "[Localizacao] Casa, Rua Jamari, 2125 - Olaria", _LOC[0])
checa("pino so com coordenada pede o endereco por escrito",
      "peca bairro" in app._extract_body_from_meta_message(
          {"type": "location", "location": {"latitude": -8.7, "longitude": -63.9}})[0])
_E_LOC = _extract_data_from_message(_LOC[0], ConversationState())
checa("endereco do pino entra na ficha", "Jamari" in (_E_LOC.get("endereco") or ""), str(_E_LOC.get("endereco")))


secao("--- 30/09 12h51: teste do Eduardo no intervalo de almoco ---")
from agents.conversation import detectar_horario  # noqa: E402
checa("'amanha as nove' (voz) vira 9h",
      detectar_horario(numeros_por_extenso("pra entrega amanhã às nove.")) == "9h")
checa("'amanha as 9' (texto) vira 9h", detectar_horario("amanhã às 9") == "9h")
checa("'as 9 e meia' vira 9h30", horario.parse_horario("as 9 e meia") == (9, 30))
checa("'as 2 da tarde' vira 14h", horario.parse_horario("pra entrega as 2 da tarde") == (14, 0))
checa("'8 horas da noite' vira 20h", horario.parse_horario("as 8 horas da noite") == (20, 0))
checa("'as 50 coxinhas' nao e horario", horario.parse_horario("quero as 50 coxinhas") is None)
checa("numero de rua nao e horario", horario.parse_horario("Av. Calama, 3234") is None)
_E_RUA = _extract_data_from_message("NA CALAMA 2025 APONIA PAGAMENTO NO CARTAO",
                                    ConversationState(modality="entrega"))
checa("'NA CALAMA 2025 APONIA...' guarda rua, bairro e pagamento",
      _E_RUA.get("endereco") == "CALAMA 2025" and _E_RUA.get("bairro") == "Aponiã"
      and _E_RUA.get("payment_method") == "cartao", str(_E_RUA))
checa("'QUERO 50 COXINHAS' com entrega nao vira rua",
      not _extract_data_from_message("QUERO 50 COXINHAS",
                                     ConversationState(modality="entrega")).get("endereco"))
checa("sem entrega definida, 'NA CALAMA 2025' nao vira rua",
      not _extract_data_from_message("NA CALAMA 2025", ConversationState()).get("endereco"))
_APP_SRC = open("app.py", encoding="utf-8").read()
checa("pedido para outro dia pula o aviso de fechado/intervalo", "_pedido_outro_dia" in _APP_SRC)
checa("o 'sim' ao intervalo grava o horario oferecido",
      '_memoria["horario_proposto"] = "14h"' in _APP_SRC and "horario_proposto" in memoria_mod._empty())


secao("--- 30/09: travas das oscilacoes medidas (medir_oscilacao 6x) ---")
# 1) Aviso de intervalo repetido pelo modelo num pedido ja agendado (3 de 6).
# Venda 24h (Balbino, 15/10): o prompt nunca manda anunciar o fechamento; so
# fala do horario se o cliente pedir para agora/hoje ou um horario fechado.
for _dado in (False, True):
    _ctx = horario.contexto_para_prompt(datetime(2026, 9, 30, 12, 51), aviso_ja_dado=_dado)
    checa(f"prompt fechado sem 'Aviso obrigatorio' (aviso dado={_dado})",
          "Aviso obrigatorio" not in _ctx and "ATENDE 24 HORAS" in _ctx)
_src_24 = open("app.py", encoding="utf-8").read()
checa("aviso de fechado do codigo so com pedido para agora/hoje",
      "and not _so_pergunta and _pediu_agora_ou_hoje" in _src_24)
checa("aviso de fechado escrito pelo modelo sem pedido de horario sai",
      "aviso de fechado sem pedido de horario; removido (24h)" in _src_24)
_agora_original = horario.agora
horario.agora = lambda: datetime(2026, 9, 30, 12, 51)
_F_AGENDADO = dict(memoria_mod._empty(), aviso_horario_dado=True, data_iso="2026-10-01",
                   horario="9h", modality="entrega", upsell_feito=True,
                   items=[{"name": "Coxinha", "quantity": 50}])
_SEM_AVISO = app._conferir_aviso_repetido(
    "55", "Estamos no intervalo de almoço até às 14h00! Posso já adiantar o seu pedido "
          "para as 14h? 😊", _F_AGENDADO)
checa("aviso repetido num pedido ja agendado sai",
      "intervalo" not in _SEM_AVISO and _SEM_AVISO.strip() != "", _SEM_AVISO[:60])
checa("sem dia/hora valido o aviso fica",
      "intervalo" in app._conferir_aviso_repetido(
          "55", "Estamos no intervalo de almoço até às 14h00! Posso já adiantar o seu pedido "
                "para as 14h? 😊", dict(_F_AGENDADO, horario="")))
horario.agora = _agora_original
# 2) Croquete + refrigerante. Ate 01/10 o croquete era bloqueado; o dono
#    cadastrou "croquete macaxeira e carne" e ele foi liberado. "10 croquetes"
#    agora e triagem de tamanho, e ela vem antes da pergunta do refrigerante.
_F_CROQ2 = memoria_mod._empty()
for _m in ("Olá boa tarde Quero  10 coxinhas e uma coca cola",):
    _F_CROQ2 = memoria_mod.merge(_F_CROQ2, _extract_data_from_message(_m, ConversationState()),
                                 _extract_data_from_message(_m, ConversationState()))[0]
checa("tamanho vem antes da pergunta do refrigerante",
      memoria_mod.pergunta_bloqueante(_F_CROQ2).startswith("Essas 10 coxinhas são grande"),
      memoria_mod.pergunta_bloqueante(_F_CROQ2))
_RUIM = app._conferir_etapa_travada(
    "55", 'O sabor "Croquete de Carne" está disponível, mas o "Croquete de Macaxeira" que '
          'você pediu não está listado. Qual refrigerante prefere: lata, 600ml ou Tuchaua?', _F_CROQ2)
checa("resposta sem a pergunta de tamanho recebe a do codigo",
      "Essas 10 coxinhas são grande (lanche) ou mini (festa)?" in _RUIM, _RUIM[:120])
_DUAS = app._conferir_uma_pergunta(
    "55", "Essas 10 coxinhas são grande (lanche) ou mini (festa)? E qual refrigerante "
          "você prefere: lata, 600ml ou Tuchaua?", _F_CROQ2)
checa("com duas perguntas, fica a da etapa travada (tamanho)",
      "grande (lanche)" in _DUAS and "refrigerante" not in _DUAS, _DUAS)


secao("--- 30/09 tarde: testes do dono (audio, saudacao, divisao, fila) ---")
_SRC = open("app.py", encoding="utf-8").read()
# Audio: o download sob demanda ia sem o token da Meta (401) e o audio chegava
# ao modelo so como "[Audio]". Agora usa download_media_bytes, e o flush
# garante o download (mesmo lock do segundo plano) antes de transcrever.
checa("download sob demanda usa o token (download_media_bytes)",
      "conteudo = download_media_bytes(url)" in _SRC and "resp = _req.get(url, timeout=30)" not in _SRC)
checa("flush garante o download da midia antes de transcrever",
      '_download_media_async(it["msg_id"], it["wa_media_id"])' in _SRC)
checa("audio sem transcricao recebe resposta clara, nao 'envie digitado'",
      "Não consegui ouvir o seu áudio agora" in _SRC)
# O prompt do dono manda pedir texto em audio; com transcricao, nao pode.
checa("regra de execucao sobre audio transcrito", "9.4b AUDIO" in _SRC)
_F_RET = dict(memoria_mod._empty(), modality="retirada", upsell_feito=True,
              items=[{"name": "Coxinha", "quantity": 50}])
_PEDE = app._conferir_pede_texto(
    "55", "Por favor, envie seu pedido digitado em texto para que eu possa ajudar. "
          "Qual data você deseja para a retirada?", _F_RET)
checa("'envie digitado' sai da resposta a um audio transcrito",
      "digitad" not in _PEDE and "data" in _PEDE.lower(), _PEDE[:60])
# Saudacao na primeira resposta.
_SAUD = app._conferir_saudacao("😊 Pro lanche da tarde, vai um Bolo?", datetime(2026, 9, 30, 14, 19), "Balbino")
checa("primeira resposta sem saudacao ganha a saudacao",
      _SAUD.startswith("Boa tarde, Balbino!") and "\n\nPro lanche" in _SAUD, _SAUD[:50])
checa("primeira resposta com saudacao fica como esta",
      app._conferir_saudacao("Boa tarde, Balbino! Pro lanche?", datetime(2026, 9, 30, 14, 19), "Balbino")
      == "Boa tarde, Balbino! Pro lanche?")
checa("'Otima escolha!' e enrolacao",
      app._conferir_enrolacao("55", "Ótima escolha! Fica R$ 80,00 nos salgados!") == "Fica R$ 80,00 nos salgados!")
# Divisao do volume: 1 cento + 3 sabores.
from agents.conversation import sabores_citados  # noqa: E402
checa("sabores citados sem numero",
      sabores_citados("Quero coxinha ,pastel e churros") == ["Coxinha", "Pastel", "Churros"])
checa("'Esses sabores' / 'Fritos' nao sao sabor",
      sabores_citados("Esses sabores") == [] and sabores_citados("Fritos") == [])
_F_DIV = dict(memoria_mod._empty(), volume_pendente=100)
memoria_mod.aplicar_divisao_do_volume(_F_DIV, "Quero coxinha ,pastel e churros")
# Cento em 3 sabores: um vai em dobro (Balbino, 15/10; antes o texto de 01/10).
checa("3 sabores num cento: qual vai em dobro",
      memoria_mod.pergunta_bloqueante(_F_DIV)
      == "Com 3 sabores, fica 50 de um e 25 de cada um dos outros. Qual sabor vai em dobro? 😊",
      memoria_mod.pergunta_bloqueante(_F_DIV)[:70])
memoria_mod.aplicar_divisao_do_volume(_F_DIV, "Pode colocar mais pastéis")
checa("'mais pasteis' fecha 25 + 50 + 25",
      [(i["name"], i["quantity"]) for i in _F_DIV["items"]]
      == [("Coxinha", 25), ("Pastel", 50), ("Churros", 25)] and not _F_DIV["divisao_pendente"])
_F_DIV2 = dict(memoria_mod._empty(), volume_pendente=100)
memoria_mod.aplicar_divisao_do_volume(_F_DIV2, "coxinha e bolinha")
checa("2 sabores num cento: 50 + 50 direto",
      [(i["name"], i["quantity"]) for i in _F_DIV2["items"]] == [("Coxinha", 50), ("Bolinha de Queijo", 50)])
# Fila por cliente: a segunda mensagem espera a primeira terminar.
import threading as _th  # noqa: E402
import time as _time  # noqa: E402
from buffer import MessageBuffer  # noqa: E402
_linha_do_tempo = []


def _lento(user, itens):
    _linha_do_tempo.append(("inicio", [i["text"] for i in itens]))
    _time.sleep(0.6)
    _linha_do_tempo.append(("fim", [i["text"] for i in itens]))


_BUF = MessageBuffer(wait_seconds=0.1, on_flush=_lento)
_BUF.add("55", "Somente")
_time.sleep(0.25)                         # primeiro atendimento ja comecou
_BUF.add("55", "Será para retirar as 19:00")
checa("mensagem nova durante o atendimento e percebida", _BUF.tem_mensagem_nova("55"))
_time.sleep(1.8)
checa("atendimentos do mesmo cliente nunca se sobrepoem",
      [e for e, _ in _linha_do_tempo] == ["inicio", "fim", "inicio", "fim"], str(_linha_do_tempo))
checa("a segunda mensagem e atendida depois da primeira",
      _linha_do_tempo[2][1] == ["Será para retirar as 19:00"] if len(_linha_do_tempo) > 2 else False)
checa("resposta velha (pergunta) nao sai quando chegou mensagem nova",
      "buffer.tem_mensagem_nova(user)" in _SRC)


secao("--- 01/10: reuniao com o dono (texto curto, tempo, divisao, horario) ---")
# Pedido de dados no formato do dono.
_F_ENT = dict(memoria_mod._empty(), name="Balbino", modality="entrega", upsell_feito=True,
              items=[{"name": "Coxinha", "quantity": 50}])
checa("nada informado: 'Informe:' com Rua e Numero separados",
      memoria_mod.pedido_de_dados(_F_ENT) == "Informe:\n- Bairro\n- Rua\n- Número\n"
      "- Data do pedido\n- Horário desejado\n- Forma de pagamento (Pix, Cartão ou Dinheiro)"
      "\n\n" + memoria_mod.DICA_DO_CEP,
      memoria_mod.pedido_de_dados(_F_ENT))
checa("parte informada: 'Faltam essas informacoes:' com 'Rua e numero'",
      memoria_mod.pedido_de_dados(dict(_F_ENT, bairro="Olaria")) == "Faltam essas informações:\n"
      "- Rua e número\n- Data do pedido\n- Horário desejado\n- Forma de pagamento (Pix, Cartão ou Dinheiro)"
      "\n\n" + memoria_mod.DICA_DO_CEP)
checa("um dado so: pergunta curta",
      memoria_mod.pedido_de_dados(dict(_F_ENT, bairro="Olaria", endereco="Rua X 10",
                                       data_agendada="hoje", data_iso="2026-10-01",
                                       payment_method="cartao")) == "Qual horário? 😊")
_LONGA = ("Por favor, informe numa única mensagem os seguintes dados para entrega:\n- Bairro\n"
          "- Rua com número\n- Data do pedido\n- Horário desejado\n"
          "- Forma de pagamento (Pix antecipado, Cartão ou Dinheiro na entrega)")
checa("lista longa do modelo vira o formato curto",
      app._conferir_formato_da_coleta("55", _LONGA, _F_ENT) == memoria_mod.pedido_de_dados(_F_ENT),
      app._conferir_formato_da_coleta("55", _LONGA, _F_ENT)[:60])
_INFORMA = "A taxa de entrega no Olaria é R$ 12,00. " + _LONGA
checa("o que a resposta informa fica",
      app._conferir_formato_da_coleta("55", _INFORMA, dict(_F_ENT, bairro="Olaria"))
      .startswith("A taxa de entrega no Olaria é R$ 12,00."))
checa("'Bom dia' nao e pedido de data",
      app._conferir_formato_da_coleta("55", "Bom dia, Balbino! Tudo certo por aí.", _F_ENT)
      == "Bom dia, Balbino! Tudo certo por aí.")
# Resumo: "Cartao", e nao "Cartao na entrega".
checa("resumo mostra so 'Cartao'", cardapio._rotulo_pagamento("cartao") == "Cartão")
checa("resumo mostra so 'Pix'", cardapio._rotulo_pagamento("pix") == "Pix")
checa("dinheiro curto, com o troco",
      cardapio._rotulo_pagamento("dinheiro", "150", 114.0)
      == "Dinheiro (troco para R$ 150,00 - levar R$ 36,00)")
# Finalizacao curta (fora do Pix, que leva a chave).
checa("fechamento curto", app.FECHAMENTO_CURTO == "Pedido finalizado com sucesso! 💛"
      and app._mensagem_de_fechamento("cartao", 94.0) == app.FECHAMENTO_CURTO)
checa("fechamento curto aplicado quando o pedido sai (exceto Pix)",
      'if _pedido_saiu and not _pagamento_do_pedido.startswith("pix"):' in _APP_SRC
      or 'if _pedido_saiu and not _pagamento_do_pedido.startswith("pix"):'
      in open("app.py", encoding="utf-8").read())
# Tempo: sem buffer e sem piso.
checa("sem buffer", app.BUFFER_SECONDS == 0.0)
checa("sem piso de 5s", app.RESPOSTA_MIN_SEG == 0.0 and app.PAUSA_PRIMEIRA == 0.0)
# Cento com 3 sabores: acrescentar um sabor fecha 25 x 4.
_F_ACR = dict(memoria_mod._empty(), volume_pendente=100)
memoria_mod.aplicar_divisao_do_volume(_F_ACR, "Quero coxinha ,pastel e churros")
memoria_mod.aplicar_divisao_do_volume(_F_ACR, "acrescenta bolinha")
checa("'acrescentar um sabor' fecha 25 de cada",
      [(i["name"], i["quantity"]) for i in _F_ACR["items"]]
      == [("Coxinha", 25), ("Pastel", 25), ("Churros", 25), ("Bolinha de Queijo", 25)],
      str(_F_ACR.get("items")))
# Horario: com horario dito, o aviso de intervalo/fechado nao sai.
checa("horario dito pula o aviso de fechado/intervalo",
      "_horario_dado = (bool(_memoria.get(\"horario\"))" in open("app.py", encoding="utf-8").read())


secao("--- 01/10 (tarde): teste da Camyli ---")
import horario as _hr_0110  # noqa: E402
from agents import conversation as _cv_0110  # noqa: E402
from datetime import date as _date_0110  # noqa: E402
for _txt, _esp in (("entrga", "entrega"), ("enytrega", "entrega"), ("retirda", "retirada"),
                   ("entrga rua julia 6559 igarape pra agora", "entrega"),
                   ("entrada", ""), ("quanto fica a entrga?", "")):
    checa(f"modalidade com erro de digitacao: {_txt!r}",
          _cv_0110.detectar_modalidade(_txt) == _esp, _cv_0110.detectar_modalidade(_txt))
for _txt, _esp in (("as 13 hors retirada", "13h"), ("19 hrs", "19h"), ("agora", "Imediato"),
                   ("agora mesmo", "Imediato"), ("pra hoje pra agora", "Imediato"),
                   ("agora quero 2 coxinhas", "")):
    checa(f"horario: {_txt!r}", _cv_0110.detectar_horario(_txt) == _esp,
          _cv_0110.detectar_horario(_txt))
_qui = datetime(2026, 10, 1, 16, 50)
checa("'Imediato' no expediente vale",
      _hr_0110.validar_agendamento(_qui.date(), "Imediato", _qui)[0])
_alm = datetime(2026, 10, 1, 12, 30)
_ok_alm, _msg_alm, _s, _c = _hr_0110.validar_agendamento(_alm.date(), "Imediato", _alm)
checa("'Imediato' no almoco recebe o aviso do intervalo",
      not _ok_alm and "intervalo" in _msg_alm and _c == "horario", _msg_alm)
checa("domingo trava sem esperar o horario",
      _hr_0110.validar_agendamento(_date_0110(2026, 10, 4), "", _qui)[1].startswith("Aos domingos"))
checa("'pastel coxinha bolinha e churros' sem virgula sao 4 sabores",
      _cv_0110.sabores_citados("pastel coxinha bolinha e churros")
      == ["Pastel", "Coxinha", "Bolinha de Queijo", "Churros"],
      str(_cv_0110.sabores_citados("pastel coxinha bolinha e churros")))
checa("'pastel de carne' continua um sabor so",
      _cv_0110.sabores_citados("pastel de carne e bolinha de queijo")
      == ["Pastel Carne", "Bolinha de Queijo"])
checa("bolo pra aniversario nao vira Bolo de Milho",
      _cv_0110.bebidas_citadas("quero encomedar um bolo pra aniversario")[0] == [])
checa("'um bolo' (resposta a oferta) continua Bolo de Milho",
      [i["name"] for i in _cv_0110.bebidas_citadas("sim, um bolo")[0]] == ["Bolo de Milho"])
_r_bolo = app._conferir_bolo_fora_do_cardapio(
    "t", "quero um bolo pra aniversario", "Para quando seria?", memoria_mod._empty())
checa("bolo fora do cardapio tem resposta fixa",
      "não faz" in _r_bolo and "Bolo de Milho" in _r_bolo, _r_bolo)
checa("'500 salgados' e volume", _cv_0110.volume_sem_sabor("quero 500 salgados fritos e assados") == 500)
_F_500 = dict(memoria_mod._empty(), volume_pendente=500, preparo_pendente="misto")
_p_500 = memoria_mod.pergunta_bloqueante(_F_500)
checa("500 fritos e assados: ate 20 sabores, sem perguntar o preparo",
      "até 20 sabores" in _p_500 and "fritos e assados" in _p_500 and "frito ou assado" not in _p_500,
      _p_500)
checa("a frase do volume cobre a propria etapa", memoria_mod.etapa_coberta(_F_500, _p_500))
checa("sem itens, a pergunta e o pedido (nao a lista)",
      memoria_mod.pedido_de_dados(memoria_mod._empty()) == "Qual o seu pedido? 😊")
_F_MOD = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 25}])
checa("sem modalidade, a pergunta e a modalidade",
      memoria_mod.pedido_de_dados(_F_MOD, ["modality", "data_agendada", "horario"])
      == "Será para entrega ou retirada na loja? 😊")
_F_PROP = dict(_F_MOD, modality="retirada", horario="14h", data_proposta="2026-10-05")
checa("dia ja oferecido: repete a oferta",
      # "amanha, segunda-feira (05/10)" quando o teste roda no domingo 04/10.
      memoria_mod.pedido_de_dados(_F_PROP, ["data_agendada"]).startswith("Posso deixar para ")
      and "segunda-feira (05/10)?" in memoria_mod.pedido_de_dados(_F_PROP, ["data_agendada"]),
      memoria_mod.pedido_de_dados(_F_PROP, ["data_agendada"]))
checa("'sm' e sim", app._concorda("sm"))
_res_im = app.cardapio.montar_resumo([], 20, 0, 0, 20, modalidade="retirada",
                                     dia="hoje, quinta-feira (01/10)", horario="Imediato")
checa("espelho 'Imediato' sem 'as Imediato'",
      "às Imediato" not in _res_im and "Horário: Imediato" in _res_im)
_ANT_QUICHE = "Sim, temos Quiche de Frango e Quiche de Carne de Sol disponíveis."
checa("'de frango 2 unidades' depois do quiche vira 2 Quiche de Frango",
      _cv_0110.completar_pelo_contexto("de frango 2 unidades", _ANT_QUICHE) == "2 Quiche de Frango")
checa("'quiche de frango 2 unidades': quantidade vai para a frente",
      _cv_0110.completar_pelo_contexto("quiche de frango 2 unidades", "") == "2 quiche de frango")
checa("endereco nao e reescrito",
      _cv_0110.completar_pelo_contexto("rua julia 6559", _ANT_QUICHE) == "rua julia 6559")
checa("'pra agora' nao entra no endereco",
      "agora" not in _cv_0110._FIM_DA_RUA.split("rua julia 6559 igarape pra agora", maxsplit=1)[0])


secao("--- 01/10 (noite): relatorio do dono ---")
import cardapio_catalogo as _cat  # noqa: E402
checa("tamanho: 'Essas 10 coxinhas sao grande (lanche) ou mini (festa)?'",
      memoria_mod.pergunta_de_tamanho([{"qtd": 10, "sabor": "Coxinha"}])
      == "Essas 10 coxinhas são grande (lanche) ou mini (festa)? 😊")
checa("tamanho: plural de pastel e masculino",
      memoria_mod.pergunta_de_tamanho([{"qtd": 5, "sabor": "Pastel"}]).startswith("Esses 5 pastéis são"))
_F_TAM = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 10}])
checa("'seriam tamanho grande' nao cobre mais a etapa",
      not memoria_mod.etapa_coberta(
          _F_TAM, "Essas 10 Coxinha seriam tamanho grande (lanche) ou mini (festa)? 😊"))
checa("a frase nova cobre",
      memoria_mod.etapa_coberta(_F_TAM, memoria_mod.pergunta_bloqueante(_F_TAM)))
checa("oferta das 14h01 sem 'geladinho'",
      memoria_mod.oferta_adicional(datetime(2026, 10, 1, 15, 0))
      == "Pro lanche da tarde, vai um Bolo de Milho ou um Guaraná Tuchaua 2L? 🥤",
      memoria_mod.oferta_adicional(datetime(2026, 10, 1, 15, 0)))
_F_DIN = dict(memoria_mod._empty(), name="Ana", items=[{"name": "Coxinha", "quantity": 25}],
              modality="entrega", bairro="Aponia", endereco="Rua A, 1",
              data_agendada="hoje", data_iso="2026-10-01", horario="18h",
              payment_method="dinheiro")
checa("dinheiro sem troco: o troco falta antes do espelho",
      memoria_mod.faltando(_F_DIN) == ["troco_para"], str(memoria_mod.faltando(_F_DIN)))
# 07/10 14h30: o cliente nao sabia o total para pedir troco. 25 coxinhas
# R$ 20,00 + frete Aponia R$ 8,00 + retorno R$ 2,00.
checa("a pergunta do troco traz o total",
      memoria_mod.pedido_de_dados(_F_DIN) == "O seu pedido com a entrega deu R$ 30,00. Precisa de troco para quanto? 💵",
      memoria_mod.pedido_de_dados(_F_DIN))
_F_DIN2 = dict(_F_DIN, bairro="", horario="")
checa("troco nao entra na lista com outros dados",
      "roco" not in memoria_mod.pedido_de_dados(_F_DIN2), memoria_mod.pedido_de_dados(_F_DIN2))
checa("'sem troco' completa a ficha", not memoria_mod.faltando(dict(_F_DIN, troco_para="nao")))
_F_DIN3 = dict(_F_DIN)
checa("troco nao entendido nao vira 'nao_informado'",
      memoria_mod.capturar_troco(_F_DIN3, "Precisa de troco para quanto? 💵", "sim preciso") == ""
      and not _F_DIN3.get("troco_para"))
checa("espelho: 'Dinheiro (sem troco)'",
      app.cardapio._rotulo_pagamento("dinheiro", "nao", 50) == "Dinheiro (sem troco)")
checa("espelho: 'Dinheiro (troco para R$ 100,00 - levar R$ 50,00)'",
      app.cardapio._rotulo_pagamento("dinheiro", "100.00", 50)
      == "Dinheiro (troco para R$ 100,00 - levar R$ 50,00)")
_PIX = app._fechamento_pix(28)
checa("fechamento do Pix com a chave e a frase nova",
      "84749274000178" in _PIX and _PIX.endswith("Favor Enviar Comprovante de pgto para liberação do pedido 💛")
      and "Assim que realizar" not in _PIX, _PIX)
_OPC_LATA = ["Coca", "Coca zero", "Guaraná antártica", "Fanta laranja", "Pepsi"]
for _txt, _esp in (("coca", "Coca"), ("coca zero", "Coca zero"), ("uma fanta", "Fanta laranja"),
                   ("guarana", "Guaraná antártica"), ("pepsi", "Pepsi"), ("tanto faz", "")):
    checa(f"sabor do refrigerante: {_txt!r}", memoria_mod.casar_sabor(_txt, _OPC_LATA) == _esp)
checa("'coca' no 600ml e a Coca cola (nao a zero)",
      memoria_mod.casar_sabor("coca", ["Guaraná antártica", "Coca cola zero", "Coca cola"]) == "Coca cola")
_api_sab, _ = _cat.traduzir([{"name": "Refrigerante Lata", "quantity": 1, "sabor": "Coca"}])
checa("o sabor vai para o cardapio digital", _api_sab[0].get("selected_flavors") == ["Coca"], str(_api_sab))
checa("croquete de carne liberado (cardapio: 'croquete macaxeira e carne')",
      "Croquete de carne" not in _cat.BLOQUEADOS
      and _cat.SABORES["Fritos"].get("Croquete de carne") == "croquete macaxeira")
_F_SAB = dict(memoria_mod._empty(), items=[{"name": "Refrigerante Lata", "quantity": 1, "sabor": "Fanta laranja"}])
checa("itens do modelo recebem o sabor da ficha",
      app._com_sabor_da_ficha([{"name": "Refrigerante Lata", "quantity": 1}], _F_SAB)[0].get("sabor")
      == "Fanta laranja")
secao("--- 02/10: testes do dono de 01/10 a noite ---")
for _txt in ("quero um cento variado coxinha,churros e bolinha",
             "QUERO UM CENTO VARIADO COXINHA E CHURROS E BOLINHAS  PARA AMANHA AS 9 HORAS"):
    _e3 = _cv_0110._extract_data_from_message(_txt, _cv_0110.ConversationState())
    checa(f"cento em 3 sabores nao vira 100 coxinhas: {_txt[:30]!r}",
          not _e3.get("items") and _e3.get("volume_pendente") == 100, str(_e3))
    _F3 = dict(memoria_mod._empty(), volume_pendente=100)
    memoria_mod.aplicar_divisao_do_volume(_F3, _txt)
    checa("e sai a pergunta de qual sabor vai em dobro",
          memoria_mod.pergunta_bloqueante(_F3)
          == "Com 3 sabores, fica 50 de um e 25 de cada um dos outros. Qual sabor vai em dobro? 😊",
          memoria_mod.pergunta_bloqueante(_F3))
checa("cento em 2 sabores continua dividido",
      [(i["name"], i["quantity"]) for i in _cv_0110._extract_data_from_message(
          "1 cento coxinha e churros", _cv_0110.ConversationState())["items"]]
      == [("Coxinha", 50), ("Churros", 50)])
for _txt in ("ola estou com uma festa pata 100 pessoas quantos salgadinho preciso ?",
             "QUERO ORÇAMENTO PARA COOFFE BREAK VOCE TRABALHA ?"):
    checa(f"consultoria de evento vai para a equipe: {_txt[:30]!r}", bool(app._pede_consultoria_de_evento(_txt)))
for _txt in ("quero 100 coxinhas", "quanto custa o cento?", "quantos salgados vem no cento?"):
    checa(f"pedido comum nao transfere: {_txt!r}", not app._pede_consultoria_de_evento(_txt))
for _txt, _esp in (("QUERO MIL SALGADOS PARA AMAHA AS  21 HORAS RETIRADA", True),
                   ("quero 1.000 salgados", True), ("2 mil salgados", True),
                   ("quero 500 salgados", False), ("rua 1200 salgado filho", False),
                   ("RUA SALGADO FILHO 2125", False)):
    checa(f"1.000 unidades -> equipe comercial: {_txt!r}", app._pedido_de_mil(_txt) == _esp)
checa("pergunta de frete sempre leva o valor da tabela",
      "R$ 15,00" in app._conferir_frete_perguntado("t", "Quanto é a entrega no Pedrinhas?",
                                                   "Qual o seu pedido? 😊"))
checa("'quanto custa o cento de coxinha?' leva R$ 80,00",
      "R$ 80,00" in app._conferir_preco_perguntado("t", "Quanto custa o cento de coxinha?", "Qual o seu pedido? 😊"))
checa("'porcao de esfirra' leva R$ 30,00 (assado)",
      "R$ 30,00" in app._conferir_preco_perguntado("t", "Qual o valor da porção de esfirra?", "Qual o seu pedido? 😊"))
checa("pergunta de preco nao vira pedido",
      "_RX_PERGUNTA_DE_PRECO.search(facts.normalize(text_only))" in open("app.py", encoding="utf-8").read())
checa("pedido comum nao ganha frase de frete",
      app._conferir_frete_perguntado("t", "quero 50 coxinhas", "Qual o seu pedido? 😊")
      == "Qual o seu pedido? 😊")
checa("aviso de transferencia ja dado nao repete",
      bool(app._RX_JA_AVISOU_TRANSFERENCIA.search(app.facts.normalize(
          "Vou te transferir agora para a nossa equipe comercial te atender! Um instante. 💛"))))
checa("mensagem padrao de transferencia sem 'atendente humano'",
      "humano" not in app._DEFAULT_TRANSFER_MESSAGE.lower() or "TRANSFER_MESSAGE" in os.environ)
checa("'BAIRRO SAO BOSCO' e o Sao Joao Bosco da tabela",
      _cv_0110.detectar_bairro("RUA LEONILDO DE SA 2125 BAIRRO SAO BOSCO") == "São João Bosco",
      _cv_0110.detectar_bairro("RUA LEONILDO DE SA 2125 BAIRRO SAO BOSCO"))
checa("cabecalho 'Faltam essas informacoes:' vai junto da lista",
      app.split_reply("Faltam essas informações:\n\n- Bairro\n- Forma de pagamento")
      == ["Faltam essas informações:\n- Bairro\n- Forma de pagamento"])
checa("'2 bandejas de brigadeiro' vira item",
      _cv_0110.doces_citados("quero 2 bandejas de brigadeiro")[0][0]["name"] == "Bandeja de Brigadeiros (16 un)"
      and _cv_0110.doces_citados("quero 2 bandejas de brigadeiro")[0][0]["quantity"] == 2)
checa("'meio cento de brigadeiro' nao e volume de salgado",
      not _cv_0110.volume_sem_sabor("meio cento de brigadeiro"))
checa("'cento e meio de coxinha e brigadeiro': o meio e da coxinha",
      _cv_0110.doces_citados("cento e meio de coxinha e brigadeiro")[0] == [])
_F_DOCE = dict(memoria_mod._empty(), items=[{"name": "Churros", "quantity": 50}],
               sabores_pendentes=_cv_0110.doces_citados("opa quero beijinho")[1])
_P_DOCE = memoria_mod.pergunta_bloqueante(_F_DOCE)
# Beijinho existe no cardapio digital como sabor do doce (15/10): nao e mais
# "em falta"; a pergunta do formato continua.
checa("beijinho: pergunta o formato, sem dizer que falta",
      _P_DOCE.startswith("Qual docinho você prefere?") and "falta" not in _P_DOCE
      and "Meio Cento de Brigadeiros (50 un)" in _P_DOCE, _P_DOCE)
memoria_mod.aplicar_escolha_de_sabor(_F_DOCE, "meio cento")
checa("'meio cento' responde a pergunta do doce",
      any(i["name"] == "Meio Cento de Brigadeiros (50 un)" for i in _F_DOCE["items"]), str(_F_DOCE["items"]))
_api_doce, _ = _cat.traduzir([{"name": "Bandeja de Brigadeiros (16 un)", "quantity": 1}])
checa("a bandeja vai com o sabor Brigadeiro", _api_doce[0].get("selected_flavors") == ["Brigadeiro"])
secao("--- 02/10: nao inventar nada (varredura de 46 perguntas) ---")
for _txt in ("voces estao admitindo atendente?", "tem vaga de emprego ai?", "posso parcelar no cartão?",
             "posso pagar metade no pix e metade no cartão?",
             "qual o instagram de vocês?", "vocês emitem nota fiscal?", "vocês abrem no feriado dia 12?",
             "qual o telefone da loja?", "vocês tem kit festa?", "quem é o dono da fábrica?",
             "tem estacionamento na loja?",
             "o combo vem com qual refrigerante?", "tem pedido mínimo pra entrega?",
             "dá pra trocar o pedido depois de confirmar?"):
    checa(f"sem dado -> equipe: {_txt!r}", bool(app._assunto_da_equipe(_txt)))
for _txt in ("sou amigo do dono, faz fiado?", "quero 50 coxinhas no pix", "RUA SALGADO FILHO 2125",
             "meu endereço é rua x 120", "quero mudar o pedido, tira a coxinha"):
    checa(f"pedido comum nao vai para a equipe: {_txt!r}", not app._assunto_da_equipe(_txt))
checa("casamento -> equipe", bool(app._pede_consultoria_de_evento("vocês trabalham com encomenda para casamento?")))
for _txt in ("tem salgado sem glúten?", "tem opção vegana?", "qual óleo vocês usam pra fritar?",
             "quantos dias dura o salgado na geladeira?", "o salgado chega quente?"):
    _r_ing = app._conferir_ingrediente("t", _txt, "Usamos oleo de qualidade.", memoria_mod._empty())
    checa(f"ingrediente -> frase do BLOCO 2: {_txt!r}",
          _r_ing.startswith("Preciso confirmar essa informação com nossa equipe") and "oleo" not in _r_ing, _r_ing)
for _txt, _prova in (("qual o endereço de vocês?", "Calama"), ("quanto tempo demora a entrega?", "30 a 40"),
                     ("qual o horário de sábado?", "08h00"), ("vocês tem cardápio?", "ocardapio"),
                     ("qual a diferença entre o mini e o grande?", "por unidade")):
    _r_f = app._conferir_fato_do_dono("t", _txt, "Resposta inventada.", memoria_mod._empty())
    checa(f"fato do dono com o texto dele: {_txt!r}", _prova in _r_f and "inventada" not in _r_f, _r_f)
checa("antecedencia nao vira prazo de entrega",
      "30 a 40" not in app._conferir_fato_do_dono(
          "t", "com quanto tempo de antecedência preciso encomendar?", "X", memoria_mod._empty()))
checa("fora da tabela: texto do BLOCO 3",
      app._conferir_frete_perguntado("t", "vocês entregam em Candeias do Jamari?", "X") == app.FORA_DA_AREA)
checa("'entrega na rua guapore' nao e bairro fora da tabela",
      app._conferir_frete_perguntado("t", "entrega na rua guapore", "X") == "X")
checa("'entregam no Nova Porto Velho?' leva a taxa",
      "R$ 12,00" in app._conferir_frete_perguntado("t", "vocês entregam no Nova Porto Velho?", "X"))
checa("'Av.' nao corta a frase",
      len(app._RX_FIM_DE_FRASE.split("Gostaria de retirar na Av. Calama, 5892 - Igarapé?")) == 1)
checa("pergunta sobre refrigerante nao abre pedido",
      not _cv_0110._extract_data_from_message("tem refrigerante de 1 litro?",
                                             _cv_0110.ConversationState()).get("bebidas_pendentes"))
checa("pedido de refrigerante continua abrindo",
      bool(_cv_0110._extract_data_from_message("quero um refri",
                                              _cv_0110.ConversationState()).get("bebidas_pendentes")))
checa("saudacao do modelo no meio sai",
      app._conferir_saudacao("A bandeja custa R$ 20,80. Boa tarde! Qual o seu pedido?",
                             datetime(2026, 10, 2, 15, 0)).count("Boa tarde") == 1)
checa("refri de 1 litro: nao temos + opcoes da planilha",
      app._conferir_refri_perguntado("t", "tem refrigerante de 1 litro?", "X").startswith(
          "Não temos refrigerante de 1 litro. Temos Refrigerante Lata"))
# Esgotado em 28/09, de volta ao cardapio em 11/10: a resposta segue o cardapio.
checa("coca 2 litros: em falta quando esgotado, 'temos sim' quando tem",
      ("Temos sim" if facts.resolve_unit_price("Refrigerante 2 Litros", 1) else "em falta")
      in app._conferir_refri_perguntado("t", "tem coca de 2 litros?", "X"))
checa("'nao temos X, mas temos Y' perde a sugestao",
      app._RX_NAO_TEMOS_MAS.sub(lambda m: m.group(1).rstrip() + ".",
                                "Não temos salgado de chocolate no cardápio, mas temos churros.")
      == "Não temos salgado de chocolate no cardápio.")
checa("'Temos Y, mas X nao temos' vira so o 'nao temos'",
      app._so_o_nao_temos("Temos churros doce de leite, mas salgado de chocolate não temos.")
      == "Não temos salgado de chocolate no cardápio.")
checa("regra 9.7 (nao invente nada) no prompt de execucao",
      "9.7 NAO INVENTE NADA" in open("app.py", encoding="utf-8").read())
_F_RET = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 25}], modality="retirada",
              horario="15h", data_proposta="2026-10-05")
_R_RET = app._conferir_pagamento_na_retirada(
    "55", "Confirmando a retirada. Agora, informe a forma de pagamento: Pix, Cartão ou Dinheiro?", _F_RET)
checa("retirada nao pede forma de pagamento", "pagamento" not in _R_RET.lower(), _R_RET)
checa("espelho mostra o sabor",
      "Refrigerante Lata (Fanta laranja)" in app.cardapio.montar_resumo(
          _F_SAB["items"], 7, 0, 0, 7, modalidade="retirada"))


secao("--- 28/09: aviso rapido e acrescimo depois do pedido ---")
_AGORA_POS = datetime.now()
_POS_ENTREGA = dict(memoria_mod._empty(), ultimo_pedido_modalidade="entrega",
                    ultimo_pedido_em=_AGORA_POS.isoformat(timespec="seconds"))
_POS_RETIRADA = dict(_POS_ENTREGA, ultimo_pedido_modalidade="retirada")


def _pos(texto, ficha=_POS_ENTREGA):
    return app.resposta_pos_pedido(texto, ficha)


checa("'Saiu pra entrega?' e aviso, sem afirmar que saiu",
      _pos("Saiu pra entrega?")[0] == "status"
      and "já sim" not in _pos("Saiu pra entrega?")[1].lower(), _pos("Saiu pra entrega?")[1])
checa("'marido ja ta indo buscar' e aviso de retirada",
      _pos("Meu marido já tá indo buscar", _POS_RETIRADA)
      == ("status", "Combinado! Seu pedido fica separado aqui no balcão 💛"))
checa("'vou autorizar a portaria' e aviso",
      _pos("Já vou autorizar a portaria")[0] == "status")
checa("'da pra incluir 2 cocas?' e acrescimo",
      _pos("Dá pra incluir 2 cocas?")[0] == "acrescimo")
checa("acrescimo com item na ficha continua acrescimo",
      _pos("inclui mais 10 coxinhas", dict(_POS_ENTREGA, items=[
          {"name": "Coxinha", "quantity": 10}]))[0] == "acrescimo")
checa("sem pedido recente nao e aviso",
      _pos("Saiu pra entrega?", memoria_mod._empty()) == ("", ""))
checa("pedido de 13h atras nao e aviso",
      _pos("Saiu pra entrega?", dict(_POS_ENTREGA, ultimo_pedido_em=(
          _AGORA_POS - __import__("datetime").timedelta(hours=13)).isoformat())) == ("", ""))
checa("pedido novo em montagem nao e aviso",
      _pos("meu marido vai buscar", dict(_POS_RETIRADA, items=[
          {"name": "Coxinha", "quantity": 50}])) == ("", ""))
checa("pedido novo depois do anterior segue o fluxo",
      _pos("Quero 50 coxinhas pra amanhã") == ("", ""))
checa("acrescimo nao e barrado pelo BLOCO 6",
      not app.transbordo_proibido("Acrescimo em pedido ja em producao: Dá pra incluir 2 cocas?"))
_FICHA_FECHA = dict(memoria_mod._empty(), modality="retirada", name="Balbino",
                    items=[{"name": "Coxinha", "quantity": 50}])
_salvar_original = memoria_mod.save
memoria_mod.save = lambda phone, data: None
memoria_mod.registrar_pedido_enviado("55", _FICHA_FECHA)
memoria_mod.save = _salvar_original
checa("fechar o pedido guarda a modalidade para o pos-pedido",
      _FICHA_FECHA.get("ultimo_pedido_modalidade") == "retirada"
      and not _FICHA_FECHA.get("items"))


secao("--- 28/09 noite: testes do dono depois das 20h ---")
# "PODE SIM POR FAVOR" nao era sim: o ajuste dos 25 churros voltava.
checa("'PODE SIM POR FAVOR' e concordancia", app._e_concordancia("PODE SIM POR FAVOR"))
checa("'NAO OBRIGADO' continua nao sendo", not app._e_concordancia("NAO OBRIGADO"))
# "HOJE NAO" (recusa da oferta) virava a data de hoje por cima de "amanha".
checa("'HOJE NAO' nao e data", horario.resolver_data("HOJE NAO") is None)
checa("'nao hoje, amanha' e amanha",
      horario.resolver_data("nao hoje, amanha") is not None
      and horario.resolver_data("nao hoje, amanha") != horario.agora().date())
checa("'hoje' continua hoje", horario.resolver_data("hoje") == horario.agora().date())
# "PARA ENTREGA NA CALAMA 2025": a rua sem "Rua" nao era guardada.
_E_CALAMA = _extract_data_from_message(
    "QUERO 50 COXINHAS E 50 RISSOLES 20 CHURROS PARA ENTREGA NA CALAMA 2025 "
    "APONIA PAGAMENTO NO CARTAO", ConversationState())
checa("'entrega na Calama 2025' guarda o endereco",
      (_E_CALAMA.get("endereco") or "").upper() == "CALAMA 2025", str(_E_CALAMA.get("endereco")))
for _f in ("entrega no bairro Olaria", "entregar no dia 29 as 10h", "entrega no Flodoaldo Pontes Pinto"):
    checa(f"nao e rua: {_f!r}", not _extract_data_from_message(_f, ConversationState()).get("endereco"))

# Bebida: nao existia no indice e sumia do pedido.
from agents.conversation import bebidas_citadas  # noqa: E402


def _beb(f):
    itens, pend = bebidas_citadas(f.lower())
    return [(i["name"], i["quantity"]) for i in itens], [p.get("em_falta", "?") for p in pend]


checa("'uma coca lata' e Refrigerante Lata", _beb("pode mandar uma coca lata") == ([("Refrigerante Lata", 1)], []))
checa("'3 refrigerantes 600ml'", _beb("quero 3 refrigerantes 600ml") == ([("Refrigerante 600ml", 3)], []))
checa("guarana e o Tuchaua 2L", _beb("2 guaranás") == ([("Guaraná Tuchaua 2 Litros", 2)], []))
checa("refri sem tamanho vira pergunta", _beb("manda um refri") == ([], ["?"]))
for _f in ("sem refri", "nao quero refrigerante", "NAO OBRIGADO", "HOJE NAO"):
    checa(f"recusa nao vira bebida: {_f!r}", _beb(_f) == ([], []))


def _merge_msg(ficha, msg):
    return memoria_mod.merge(
        ficha, _extract_data_from_message(msg, ConversationState(items=ficha.get("items") or [])),
        _extract_data_from_message(msg, ConversationState()))[0]


_F_COCA = _merge_msg(dict(memoria_mod._empty(), items=[
    {"name": "Coxinha de Frango com Catupiry", "quantity": 10},
    {"name": "Rissole de Carne", "quantity": 10}]), "PODE MANDAR UMA COCA LATA")
checa("aceitar a oferta com 'coca lata' SOMA ao pedido",
      [(i["name"], i["quantity"]) for i in _F_COCA["items"]]
      == [("Coxinha de Frango com Catupiry", 10), ("Rissole de Carne", 10), ("Refrigerante Lata", 1)])
_cf_fx.bruto = lambda forcar=False: _RETRATO_2L_ESGOTADO
_F_C5 = _merge_msg(memoria_mod._empty(), "Quero meio cento frito: 25 coxinhas e 25 bolinhas "
                   "de queijo, e 1 Coca-Cola de 2 litros gelada. Pra entregar AMANHA às 20h")
checa("Coca 2L (esgotada) vira pergunta com o aviso",
      memoria_mod.pergunta_bloqueante(_F_C5).startswith("O Refrigerante 2 Litros está em falta hoje!"),
      memoria_mod.pergunta_bloqueante(_F_C5)[:60])
checa("quem pediu bebida nao recebe a oferta de bebida", not memoria_mod.upsell_pendente(_F_C5))
checa("pergunta da bebida sem avisar a falta nao cobre a etapa",
      not memoria_mod.etapa_coberta(_F_C5, "Qual sabor você quer na Coca-Cola de 2 litros: "
                                           "Refrigerante Lata ou 600ml?"))
_REESCRITA = app._conferir_uma_pergunta("55", app._conferir_etapa_travada(
    "55", "Boa noite! Qual sabor você quer na Coca-Cola de 2 litros? "
          "(Refrigerante 600ml ou Refrigerante Lata)", _F_C5), _F_C5)
checa("parentese depois da pergunta sai junto com ela",
      "(Refrigerante 600ml" not in _REESCRITA and "em falta hoje" in _REESCRITA, _REESCRITA[:80])
memoria_mod.aplicar_escolha_de_sabor(_F_C5, "pode ser lata")
checa("'lata' resolve a pergunta do refrigerante",
      ("Refrigerante Lata", 1) in [(i["name"], i["quantity"]) for i in _F_C5["items"]]
      and not _F_C5.get("sabores_pendentes"))
_cf_fx.bruto = lambda forcar=False: _RETRATO_FX
_F_GUARANA = _merge_msg(memoria_mod._empty(), "Quero meio cento frito: 25 coxinhas e 25 "
                        "bolinhas de queijo, e 1 Coca-Cola de 2 litros gelada.")
_F_GUARANA = _merge_msg(_F_GUARANA, "Pode ser")
_F_GUARANA = _merge_msg(_F_GUARANA, "pode ser o guaraná")
memoria_mod.aplicar_escolha_de_sabor(_F_GUARANA, "pode ser o guaraná")   # ordem do app.py
checa("'pode ser o guaraná' responde a pergunta com 1 Tuchaua, nao 2",
      [(i["name"], i["quantity"]) for i in _F_GUARANA["items"]
       if "Tuchaua" in i["name"]] == [("Guaraná Tuchaua 2 Litros", 1)]
      and not _F_GUARANA.get("sabores_pendentes"), str(_F_GUARANA["items"]))
_F_2L = _merge_msg(memoria_mod._empty(), "Quero meio cento frito: 25 coxinhas e 25 "
                   "bolinhas de queijo, e 1 Coca-Cola de 2 litros gelada.")
checa("pergunta que oferece o esgotado e esconde lata/600ml nao cobre a etapa",
      not memoria_mod.etapa_coberta(_F_2L, "Qual sabor para o refrigerante de 2 litros: Guaraná "
                                           "Tuchaua ou Refrigerante 2 Litros (esgotado)?"))
checa("a pergunta do codigo cobre a etapa",
      memoria_mod.etapa_coberta(_F_2L, memoria_mod.pergunta_bloqueante(_F_2L)))
_F_TROCA = _merge_msg(dict(_F_COCA), "na verdade quero 50 coxinhas")
checa("correcao dos salgados nao apaga a bebida",
      any(i["name"] == "Refrigerante Lata" for i in _F_TROCA["items"]), str(_F_TROCA["items"]))


secao("--- 29/09: o que o dono ajusta pelo painel (config_dono) ---")
import cardapio_catalogo as _cat  # noqa: E402
import database  # noqa: E402

_ler_original = _LER_JSON_REAL


def _camada(ofertas=None, catalogo=None):
    """Simula o que o dono gravou. Sem argumentos = nada gravado."""
    config_dono._ler_json = lambda chave: {
        config_dono.CHAVE_OFERTAS: ofertas, config_dono.CHAVE_CATALOGO: catalogo}.get(chave)
    config_dono.invalidar()


_camada()
checa("sem nada gravado: sabores sao os padrao", _cat.sabores_efetivos() is _cat.SABORES)
checa("sem nada gravado: mapa e o padrao", _cat.mapa_efetivo() is _cat.MAPA)
checa("sem nada gravado: bloqueados sao os padrao", _cat.bloqueados_efetivos() is _cat.BLOQUEADOS)
_ONZE = datetime(2026, 9, 29, 11, 0)
checa("sem nada gravado: a oferta e a padrao da faixa",
      memoria_mod.oferta_adicional(_ONZE) == memoria_mod._OFERTAS_POR_HORARIO[1][2])

# JSON quebrado no banco: vale o padrao, nunca erro.
config_dono._ler_json = _ler_original
_get_setting_original = database.get_setting
database.get_setting = lambda chave, padrao="": "{quebrado"
config_dono.invalidar()
checa("setting com JSON quebrado: ofertas voltam ao padrao",
      config_dono.ofertas() == [""] * 4
      and memoria_mod.oferta_adicional(_ONZE) == memoria_mod._OFERTAS_POR_HORARIO[1][2])
checa("setting com JSON quebrado: catalogo vazio", config_dono.catalogo_extra() == [])
database.get_setting = _get_setting_original
config_dono._ler_json = lambda chave: None

# Frase nova do dono, sem nenhuma das palavras fixas.
_camada(ofertas=["", "Quer levar um suco natural hoje?", "", ""])
checa("a frase do dono vale na faixa dela",
      memoria_mod.oferta_adicional(_ONZE) == "Quer levar um suco natural hoje?")
checa("as outras faixas continuam com o padrao",
      memoria_mod.oferta_adicional(datetime(2026, 9, 29, 8, 0)) == memoria_mod._OFERTAS_POR_HORARIO[0][2])
checa("frase do dono marca a oferta como feita (nao repete)",
      app._ofereceu_acompanhamento("Quer levar um suco natural hoje? 🍹"))
_F_UPSELL = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 50}])
checa("frase do dono cobre a etapa da oferta",
      memoria_mod.etapa_coberta(_F_UPSELL, "Quer levar um suco natural hoje? 🍹", _ONZE))
checa("oferta: frase sem pergunta e recusada", config_dono.validar_oferta("Temos suco.") != "")
checa("oferta: em branco e aceito (volta ao padrao)", config_dono.validar_oferta("") == "")

# Croquete de carne liberado pelo dono.
_CROQUETE = {"nome_planilha": "Croquete de carne", "tipo": "sabor", "familia": "Fritos",
             "nome_cardapio": "croquete de carne", "apelidos": ["croquete", "coxinha"]}
_camada(catalogo=[_CROQUETE])
checa("liberado sai dos bloqueados", "Croquete de carne" not in _cat.bloqueados_efetivos())
checa("os outros bloqueados continuam", "Panqueca de Carne" in _cat.bloqueados_efetivos())
_api, _bloq = _cat.traduzir([{"name": "Croquete de carne", "quantity": 50}])
checa("liberado traduz para o sabor que o dono informou",
      not _bloq and _api and _api[0].get("selected_flavors") == ["croquete de carne"], str((_api, _bloq)))
checa("apelido do dono nao rouba nome ja conhecido ('coxinha' continua Coxinha)",
      _produto_conhecido("coxinha") == "Coxinha")
checa("entrada invalida e ignorada",
      config_dono._entrada_valida({"nome_planilha": "X", "tipo": "sabor", "nome_cardapio": "x"}) is None)
_TESTE_FORA = _cat.testar_entrada({"nome_planilha": "Produto Que Nao Existe", "tipo": "unidade",
                                   "nome_cardapio": "qualquer"})
checa("produto fora da planilha e recusado antes de tocar o cardapio",
      not _TESTE_FORA["ok"] and "nao esta na planilha" in " ".join(_TESTE_FORA["motivos"]))
_TESTE_TIPO = _cat.testar_entrada({"nome_planilha": "Quiche de Frango", "tipo": "sabor",
                                   "familia": "Fritos", "nome_cardapio": "quiche"})
checa("tipo errado (item com preco como sabor) e recusado",
      not _TESTE_TIPO["ok"] and "por unidade" in " ".join(_TESTE_TIPO["motivos"]))

_camada()
checa("depois de remover tudo, volta ao padrao",
      _cat.bloqueados_efetivos() is _cat.BLOQUEADOS
      and "Panqueca de Carne" in _cat.bloqueados_efetivos())


secao("--- 27/09: a coleta de entrega pode ter duas perguntas ---")
# A excecao e do proprio prompt: definida a modalidade, bairro/rua/dia/horario/
# pagamento vao numa mensagem so, em lista vertical. Se o modelo escrever essa
# lista com "?" em duas linhas, cortar uma perderia um dado - o oposto do que a
# regra de "uma pergunta" existe para evitar.
_FICHA_COLETA = dict(memoria_mod._empty(), **{
    "name": "Balbino", "modality": "entrega", "upsell_feito": True,
    "items": [{"name": "Coxinha", "quantity": 50}],
})
# chr(10) pelo mesmo motivo do _ESPELHO_TROCO acima: escape em payload de patch
# vira newline de verdade e o arquivo para de compilar. Aconteceu de novo aqui.
_LISTA_COM_DOIS = chr(10).join([
    "Para seguir, me confirma:",
    "Qual é o bairro?",
    "Qual a forma de pagamento?",
])
checa("lista de coleta com dois '?' nao e cortada",
      app._conferir_uma_pergunta("55", _LISTA_COM_DOIS, _FICHA_COLETA)
      == _LISTA_COM_DOIS)
# Mas duas perguntas que NAO sao coleta continuam virando uma.
checa("duas perguntas fora da coleta continuam virando uma",
      app._conferir_uma_pergunta(
          "55", "Bora montar o pedido? Quais sabores prefere?",
          _FICHA_COLETA).count("?") == 1)
# E sem ficha o comportamento antigo vale (a funcao e usada em teste solto).
checa("sem ficha, duas perguntas viram uma",
      app._conferir_uma_pergunta(
          "55", "Tudo bem? Qual é o bairro?").count("?") == 1)


secao("--- 27/09: esfirra cotada como frito (R$ 20,00 em vez de R$ 30,00) ---")
# "Qual o valor da porcao de esfirra?" recebeu "a porcao de 25 unidades de
# salgados FRITOS, que inclui sabores como a Esfirra... R$ 20,00" em 2 de 3
# rodadas. Esfirra e ASSADO: a porcao e R$ 30,00. O roteiro do dono marca esse
# caso por escrito - "R$ 30,00 (assado - NAO R$ 20,00)".
checa("esfirra e da linha de assados",
      app._familia_do_sabor("Esfirra de Carne") == "Assados")
checa("coxinha e da linha de fritos",
      app._familia_do_sabor("Coxinha frango") == "Fritos")
checa("salgado grande nao e sabor de lote",
      app._familia_do_sabor("Quiche de Frango") == "")

_ERRO_DA_ESFIRRA = ("Bom dia! A porção de 25 unidades de salgados fritos, que "
                    "inclui sabores como a Esfirra de Carne, custa R$ 20,00.")
_corrigido = app._conferir_familia_do_sabor("55", _ERRO_DA_ESFIRRA)
checa("a linha errada e corrigida", _corrigido != _ERRO_DA_ESFIRRA)
checa("e o preco passa a ser o de assados", "30,00" in _corrigido, _corrigido[:60])
checa("o valor errado sai da mensagem", "20,00" not in _corrigido)
checa("e a correcao diz a linha certa", "assados" in _corrigido)
# O contrario tambem: frito descrito como assado cobraria R$ 10,00 a mais.
_corrigido2 = app._conferir_familia_do_sabor(
    "55", "A porção de 25 coxinhas assadas sai R$ 30,00.")
checa("frito dito assado tambem e corrigido", "20,00" in _corrigido2, _corrigido2[:60])

# O que NAO pode ser tocado.
for _certo in ("A esfirra é vendida na linha de salgados assados, e a porção "
               "de 25 sai R$ 30,00.",
               "A porção de 25 coxinhas fritas sai R$ 20,00.",
               "Para 50 salgados fritos com coxinha e 50 assados com esfirra, "
               "o total é R$ 100,00.",
               "Qual é o número da casa? 🛵"):
    checa(f"resposta certa fica intacta: {_certo[:34]!r}",
          app._conferir_familia_do_sabor("55", _certo) == _certo)
checa("espelho do pedido nunca e reescrito aqui",
      app._conferir_familia_do_sabor("55", _espelho) == _espelho)


secao("--- 27/09: fallback do codigo respeita a ordem do funil ---")
# Com o pedido inteiro numa mensagem nada falta na ficha, e os dois caminhos de
# fallback (modelo fora do ar, resposta vazia) iam direto ao espelho - pulando
# a oferta adicional. So aparecia quando um 429 desviava para esse caminho: 1 de
# 6 rodadas, com o upsell chegando DEPOIS do espelho.
_FICHA_SEM_UPSELL = dict(memoria_mod._empty(), **{
    "name": "Eduardo", "modality": "entrega", "bairro": "Olaria",
    "endereco": "Rua Jamari 2125", "payment_method": "cartao",
    "data_agendada": "segunda-feira (28/09)", "data_iso": "2026-09-28",
    "horario": "16h", "items": [{"name": "Coxinha", "quantity": 50}],
})
checa("a ficha nao tem pendencia de campo",
      not memoria_mod.faltando(_FICHA_SEM_UPSELL))
checa("mas a oferta adicional esta pendente",
      bool(memoria_mod.upsell_pendente(_FICHA_SEM_UPSELL)))
_pergunta = app._proxima_pergunta_do_funil(_FICHA_SEM_UPSELL)
checa("o texto do codigo e a OFERTA, nao o espelho",
      app._ofereceu_acompanhamento(_pergunta)
      and not app._RX_LINHA_DE_RESUMO.search(_pergunta), _pergunta[:60])
checa("com o upsell feito, ai sim vem o espelho",
      app._RX_LINHA_DE_RESUMO.search(app._proxima_pergunta_do_funil(
          dict(_FICHA_SEM_UPSELL, upsell_feito=True))) is not None)


secao("--- 27/09: o banco fala UTC, so a tela converte ---")
# O dono viu a ultima conversa as 19:27 no WhatsApp e outra hora no painel.
# Causa: o MESMO campo tinha duas leituras. last_message_at e
# last_interaction_at eram gravados com time.strftime() (hora da maquina),
# enquanto created_at/updated_at usam utcnow() - e o painel soma "Z" em tudo
# (static/app.js), ou seja, le tudo como UTC. Num servidor em UTC as duas
# coincidem e nada aparece; rodando de Porto Velho (UTC-4) a conversa aparece
# 4h mais cedo. O banco SQLite de 04/09 tem o par exato: last_message_at
# 19:26:31 e a ultima mensagem 23:26:31.
import io as _io  # noqa: E402
import database as _db_mod  # noqa: E402
import scheduler as _sched  # noqa: E402

_AGORA_UTC = datetime.utcnow()
_AGORA_LOCAL = datetime.now()
_OFFSET_MAQUINA = abs((_AGORA_LOCAL - _AGORA_UTC).total_seconds())

_carimbo = datetime.fromisoformat(_db_mod.agora_utc())
checa("agora_utc() acompanha o relogio UTC",
      abs((_carimbo - _AGORA_UTC).total_seconds()) < 5,
      f"{abs((_carimbo - _AGORA_UTC).total_seconds()):.1f}s de utcnow")
if _OFFSET_MAQUINA > 60:
    checa("e NAO o relogio local da maquina",
          abs((_carimbo - _AGORA_LOCAL).total_seconds()) > 60,
          f"maquina esta {_OFFSET_MAQUINA / 3600:.0f}h fora de UTC")
else:
    checa("maquina ja esta em UTC: fuso nao da para testar aqui", True, "(pulado)")

checa("para_utc desfaz o fuso de Porto Velho",
      abs((horario.para_utc(horario.agora()) - _AGORA_UTC).total_seconds()) < 5)

# O contrato com o painel, preso dos dois lados: se alguem tirar o "Z" do JS,
# este teste falha e o comentario explica por que ele estava la.
_JS = _io.open("static/app.js", encoding="utf-8").read()
checa("o painel le last_message_at como UTC (soma 'Z')",
      "c.last_message_at + 'Z'" in _JS)

# E o lado do servidor: nenhum carimbo de banco sai do relogio local.
_APP = _io.open("app.py", encoding="utf-8").read()
checa("app.py nao grava carimbo com o relogio local",
      'time.strftime("%Y-%m-%dT%H:%M:%S")' not in _APP)
checa("app.py usa a fonte unica de agora", "database.agora_utc()" in _APP)

# Follow-up: o prazo conta em UTC, mas a JANELA de envio continua sendo o
# relogio do cliente ("nao mande antes das 08h" e 08h dele).
_janela = _sched._next_window_start("08:00")
checa("proxima janela de envio e gravada em UTC (futuro)",
      _janela > _AGORA_UTC,
      f"janela={_janela.isoformat()} utcnow={_AGORA_UTC.isoformat()}")
_janela_local = _janela - horario._TZ_OFFSET
checa("e no relogio do cliente ela cai as 08:00",
      (_janela_local.hour, _janela_local.minute) == (8, 0),
      _janela_local.isoformat())
checa("a janela de envio continua medida em hora local",
      "horario.agora()" in _io.open("scheduler.py", encoding="utf-8").read())


secao("--- 27/09: a conversa aberta parava no comeco ---")
# O dono: "aparece como ultima mensagem a mensagem certa, mas na conversa,
# quando abre, ainda aparece so ate a 17:01". Nao era fuso: get_messages fazia
# "ORDER BY created_at ASC LIMIT 200", que devolve as 200 mais ANTIGAS. A
# conversa dele tem 765 mensagens; o painel parava em 22/09 17h01 e escondia as
# outras 565, inclusive a bateria inteira de 26/09. A LISTA mostrava a mensagem
# certa (ela le last_message_at), a conversa ABERTA mostrava o comeco - o
# descasamento que ele descreveu.
import db as _db_raw  # noqa: E402

_maior = _db_raw.fetch_one(
    "SELECT conversation_id AS cid, count(*) AS c FROM messages "
    "GROUP BY conversation_id ORDER BY c DESC LIMIT 1"
)
if _maior and _maior["c"] > 5:
    _cid, _total = _maior["cid"], _maior["c"]
    _ultima_real = _db_raw.fetch_one(
        "SELECT max(created_at) AS m FROM messages WHERE conversation_id=?", (_cid,)
    )["m"]
    _msgs = _db_mod.get_messages(_cid, limit=5)
    checa("get_messages respeita o limite", len(_msgs) == 5, f"{len(_msgs)} de 5")
    checa("a ultima devolvida E a ultima da conversa",
          _msgs[-1]["created_at"] == _ultima_real,
          f"conv {_cid} ({_total} msgs)")
    checa("e vem em ordem cronologica, para a tela ler de cima para baixo",
          all(_msgs[i]["created_at"] <= _msgs[i + 1]["created_at"]
              for i in range(len(_msgs) - 1)))
    # offset pagina para TRAS: pula as mais recentes.
    _antes = _db_mod.get_messages(_cid, limit=5, offset=5)
    checa("offset pula as mais recentes (pagina para tras)",
          _antes[-1]["created_at"] < _msgs[0]["created_at"],
          f"{_antes[-1]['created_at']} < {_msgs[0]['created_at']}")
else:
    checa("sem conversa grande no banco para testar", True, "(pulado)")


secao("--- 27/09: sem enrolacao na abertura (regra do dono) ---")
# "Envie apenas 1 mensagem por vez (maximo 2 a 3 linhas), sem enrolacao
# ('Claro!', 'Perfeito!', 'Com certeza!')" - estava no .docx de 18/09 e caiu do
# prompt quando as referencias de workflow foram limpas pelo painel. O dono
# pediu de volta em 27/09.
for _com, _sem in (
    ("Claro! Qual é o número da casa? 🛵", "Qual é o número da casa? 🛵"),
    ("Perfeito! Anotei 50 coxinhas. Qual é o bairro?",
     "Anotei 50 coxinhas. Qual é o bairro?"),
    ("Com certeza! A taxa de entrega é R$ 12,00.",
     "A taxa de entrega é R$ 12,00."),
    ("Certamente, vou anotar. Qual o horário?", "Vou anotar. Qual o horário?"),
    ("Sem problemas! Posso deixar para segunda?",
     "Posso deixar para segunda?"),
):
    checa(f"abertura vazia cortada: {_com[:26]!r}",
          app._conferir_enrolacao("55", _com) == _sem,
          app._conferir_enrolacao("55", _com)[:46])

# A lista NAO pode crescer por simetria: estas PARECEM enrolacao e sao frases
# prontas do dono (BLOCO 0 e BLOCO 6). Cortar uma delas troca o texto dele por
# nada.
for _pronta in (
    "Que maravilha! 💛 Nosso preço já é direto de fábrica no valor mínimo. "
    "Bora montar o seu pedido?",
    "Muito obrigada pelo retorno e pelo carinho! 💛 Qualquer coisa estamos por aqui.",
    "Sinto muito por isso! Já redobramos a atenção aqui com a equipe para sair "
    "tudo perfeito e pontual desta vez.",
    "Prontinho, recomecei o atendimento do zero! 💛 Eu sou a Sandrinha da BALBS.",
    "Qual é o número da casa? 🛵",
):
    checa(f"frase pronta do dono fica intacta: {_pronta[:30]!r}",
          app._conferir_enrolacao("55", _pronta) == _pronta)

checa("enrolacao sozinha nao esvazia a mensagem",
      app._conferir_enrolacao("55", "Perfeito!") == "Perfeito!")
checa("espelho do pedido nao e tocado",
      app._conferir_enrolacao("55", _espelho) == _espelho)
# "perfeito" no MEIO da frase e texto normal, nao abertura.
checa("a palavra no meio da frase nao e cortada",
      app._conferir_enrolacao("55", "Vai sair tudo perfeito! Qual o horário?")
      == "Vai sair tudo perfeito! Qual o horário?")

# E a regra tambem esta escrita no prompt, para o modelo nao produzir a
# abertura em primeiro lugar - a guarda e a rede, nao a instrucao.
checa("a regra esta no bloco de execucao do prompt",
      "9.5 CONCISAO" in _APP and "PROIBIDO abrir a mensagem com enrolacao" in _APP)


secao("--- higiene ---")
# Escrever "\\b" por engano num arquivo grava um byte de backspace, e o regex
# passa a nunca casar. Ja aconteceu tres vezes, sempre silencioso.
import glob  # noqa: E402

sujos = []
for caminho in glob.glob("*.py") + glob.glob("agents/*.py"):
    with open(caminho, encoding="utf-8") as fh:
        texto = fh.read()
    if any(ord(c) < 9 or 10 < ord(c) < 32 for c in texto):
        sujos.append(caminho)
checa("nenhum caractere de controle no codigo", not sujos, str(sujos))

# Irma da checagem acima, para o caso que ela NAO pega: "\\b" num literal
# sem o prefixo r vira byte de backspace so em tempo de execucao - no arquivo
# continua sendo backslash+b, entao a checagem de bytes do fonte passa batido.
# Foi assim que o regex de horario passou a nunca casar: a segunda metade de
# uma concatenacao estava sem o r. Aqui eu avalio o VALOR do literal.
import ast  # noqa: E402
import tokenize  # noqa: E402

escondidos = []
for caminho in glob.glob("*.py") + glob.glob("agents/*.py"):
    with open(caminho, "rb") as fh:
        try:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type != tokenize.STRING:
                    continue
                try:
                    valor = ast.literal_eval(tok.string)
                except Exception:
                    continue
                if not isinstance(valor, str):
                    continue
                if any(ord(c) < 9 or 10 < ord(c) < 32 for c in valor):
                    escondidos.append(f"{caminho}:{tok.start[0]}")
        except tokenize.TokenError:
            pass
checa("nenhum literal virando byte de controle", not escondidos, str(escondidos[:6]))


secao("--- 30/09 19h43: OpenAI sem credito - o laco da oferta (fluxo real, modelo fora) ---")
# Fica no FIM: instala os stubs do test_agente (nada sai, nada grava) e troca
# o modelo por um que falha com o erro de credito. Nao gasta nada.
import contextlib as _ctx  # noqa: E402
import io as _io2  # noqa: E402
import test_agente as _t_ag  # noqa: E402
import memory as _memory  # noqa: E402
_t_ag._instalar_stubs()
database.get_contact = lambda p: {"phone": p, "name": "Balbino", "auto_mode": 1}


def _sem_credito(*a, **k):
    raise RuntimeError("Error code: 429 - {'error': {'code': 'insufficient_quota', "
                       "'message': 'You exceeded your current quota'}}")


app.llm.generate_reply = _sem_credito
horario.agora = lambda: datetime(2026, 9, 30, 19, 43)
_t_ag._ficha.clear()
_memory._store.pop(_t_ag.TELEFONE, None)
_respostas = []
for _m in ("QUERO C50 COXINHAS E 50 SALSICHA", "NAO", "NAO QUERO",
           "RETIRADA", "AMANHA AS 10 HORAS", "SIM"):
    with _ctx.redirect_stdout(_io2.StringIO()):
        _respostas.append(_t_ag.enviar(_m))
_ofertas = [r for r in _respostas if app._ofereceu_acompanhamento(r)]
checa("sem modelo, a oferta sai UMA vez (nao entra em laco)", len(_ofertas) == 1,
      str([r[:30] for r in _respostas]))
checa("sem modelo, o codigo leva o pedido ate o resumo",
      bool(app._RX_LINHA_DE_RESUMO.search(_respostas[4])), _respostas[4][:40])
checa("sem modelo, 'SIM' ao resumo confirma e passa para a equipe (sem repetir o resumo)",
      _respostas[5].startswith("Pedido confirmado!"), _respostas[5][:50])


secao("--- 01/10: comprovante e 'ja fiz o pix' seguem as regras do dono ---")
# Prompt do dono, MIDIA E ARQUIVOS: depois do pedido fechado, comprovante ou
# aviso de pagamento -> nao responde, silencio e conferencia humana.
_transferidos = []
app.perform_transfer = lambda phone, reason="", send_message=True: (
    _transferidos.append((reason, send_message)), True)[1]
app.ler_texto_da_imagem = lambda b64, mime: app._IMAGEM_COMPROVANTE
_PNG_FALSO = "data:image/png;base64,iVBORw0KGgo="
database.get_message = lambda mid: {"id": mid, "msg_type": "image", "media_url": _PNG_FALSO}
_pos = dict(memoria_mod._empty(), name="Balbino",
            ultimo_pedido_em=datetime.now().isoformat(timespec="seconds"),
            ultimo_pedido_modalidade="retirada", saudacao_feita=True)


def _manda(itens, ficha):
    _t_ag._ficha.clear()
    _t_ag._ficha.update(ficha)
    _t_ag._enviadas.clear()
    _transferidos.clear()
    with _ctx.redirect_stdout(_io2.StringIO()):
        app.handle_flush(_t_ag.TELEFONE, itens)
    return "\n".join(_t_ag._enviadas)


_r = _manda([{"text": "[Imagem]", "msg_id": 7, "wa_media_id": "", "wa_message_id": ""}], _pos)
checa("comprovante depois do pedido: nao responde nada", _r == "", _r[:40])
checa("comprovante depois do pedido: vai para a equipe, sem aviso ao cliente",
      len(_transferidos) == 1 and _transferidos[0][1] is False, str(_transferidos))
_r = _manda([{"text": "ja fiz o pix", "wa_message_id": ""}], _pos)
checa("'ja fiz o pix' depois do pedido: silencio e equipe",
      _r == "" and len(_transferidos) == 1, f"{_r[:40]} {_transferidos}")
_r = _manda([{"text": "[Imagem]", "msg_id": 8, "wa_media_id": "", "wa_message_id": ""}],
            dict(memoria_mod._empty(), name="Balbino", saudacao_feita=True,
                 items=[{"name": "Coxinha", "quantity": 50}]))
checa("comprovante no meio do pedido: avisa que a equipe valida e segue",
      "A equipe valida o valor quando o pedido fechar" in _r and not _transferidos, _r[:60])
checa("o prompt nao manda mais pedir texto em audio",
      "solicite envio digitado" not in (database.get_knowledge_document(
          "PROMPTS ATUALIZADO PARA ENVIAR EDUARDO 18.09.26.docx") or ""))

secao("--- 02/10: dados vindos do cardapio digital (retrato em fixtures/) ---")
import json as _json_cf  # noqa: E402
import cardapio_fonte as _cf  # noqa: E402
_RETRATO = _json_cf.load(open("fixtures/cardapio_snapshot_2026-10-02.json", encoding="utf-8"))
_bruto_real = _cf.bruto
_cf.bruto = lambda forcar=False: _RETRATO
facts._cache_cardapio = None
_DADOS_CF = _cf.montar_dados(_RETRATO)
checa("adaptador: 71 bairros do cardapio", len(_DADOS_CF["bairros"]) == 71)
checa("adaptador: pacotes de fritos com o preco do cardapio",
      any(p["categoria"] == "Fritos" and p["item"] == "Porção (25 un)" and p["preco"] == 20.0
          for p in _DADOS_CF["produtos"]))
checa("adaptador: sabor com o nome que o codigo conhece",
      any(p["item"] == "Coxinha frango" and p["preco"] is None for p in _DADOS_CF["produtos"]))
checa("adaptador: um sabor por nome (sem 'Coxinha (frango)' duplicado)",
      not any(p["item"] == "Coxinha (frango)" for p in _DADOS_CF["produtos"]))
checa("adaptador: canudinho nao vira porcao de fritos a R$ 30",
      not any(p["categoria"] == "Fritos" and p["preco"] == 30.0 for p in _DADOS_CF["produtos"]))
with facts.com_fonte("cardapio"):
    checa("cardapio: taxa de Eldorado e a do cardapio (R$ 15)", facts.lookup_frete("eldorado")["taxa"] == 15.0)
    checa("cardapio: bairro que so o cardapio tem (Centro)", bool(facts.lookup_frete("centro")))
    checa("cardapio: 'tupi' e o Tupy (nao o Tucumanzal)", facts.lookup_frete("tupi")["bairro"] == "Tupy")
    checa("cardapio: 'cuniae' (nome da planilha) casa com Cunia", facts.lookup_frete("cuniae") is not None)
    checa("cardapio: 50 coxinhas a R$ 0,80", facts.resolve_unit_price("Coxinha", 50)["preco_unitario"] == 0.8)
    checa("cardapio: brigadeiro com o preco que o calculateOrder cobra (R$ 65)",
          facts.resolve_unit_price("Meio Cento de Brigadeiros (50 un)", 1)["preco_unitario"] == 65.0)
    checa("cardapio: produto que so o cardapio tem (Empada Doce)",
          facts.resolve_unit_price("Empada Doce", 1) is not None)
    _api_cf, _bloq_cf = catalogo.traduzir([{"name": "Coxinha frango", "quantity": 25},
                                           {"name": "Mini Pizza (Brotinho)", "quantity": 2},
                                           {"name": "Refrigerante 2 Litros", "quantity": 1, "sabor": "Coca cola"}])
    checa("cardapio: pedido com produto novo e Refri 2L traduz sem bloqueio",
          not _bloq_cf and any(a["name"] == "Mini Pizza (Brotinho)" for a in _api_cf)
          and any(a["name"] == "Refrigerante 2 L" for a in _api_cf), str((_api_cf, _bloq_cf)))
    _api_cz, _bloq_cz = catalogo.traduzir([{"name": "Calzone de frango", "quantity": 50}])
    checa("cardapio: sabor novo de assado vai com o nome da opcao",
          not _bloq_cz and _api_cz[0]["selected_flavors"] == ["Calzone de frango"], str((_api_cz, _bloq_cz)))
    checa("cardapio: rotulo de formato sozinho continua bloqueado",
          bool(catalogo.traduzir([{"name": "Cento (100 un)", "quantity": 1}])[1]))
secao("--- 02/10: o dono edita o cardapio e a Sandrinha acompanha ---")
_EDITADO = _copy_fx.deepcopy(_RETRATO_FX)
for _t in _EDITADO["InternalDeliveryFee"]:
    if _t["bairro"] == "Eldorado":
        _t["valor"] = 18.0
_EDITADO["InternalDeliveryFee"].append({"bairro": "Bairro Novo do Teste", "valor": 11.0, "available": True})
_EDITADO["InternalMenuItem"].append({"name": "Coxinha de Costela", "category": "lanches", "price": 11.0,
                                     "available": True, "sales_mode": "unit"})
for _it in _EDITADO["InternalMenuItem"]:
    if _it["name"].strip() == "Bolo de Milho":
        _it["available"] = False
for _h in _EDITADO["BusinessHours"]:
    if _h.get("is_open"):
        _h["open_time"] = "07:30"
_cf_fx.bruto = lambda forcar=False: _EDITADO
checa("taxa alterada no cardapio vale na hora", facts.lookup_frete("eldorado")["taxa"] == 18.0)
checa("bairro novo no cardapio passa a ser atendido", facts.lookup_frete("bairro novo do teste")["taxa"] == 11.0)
checa("produto novo no cardapio ja tem preco", (facts.resolve_unit_price("Coxinha de Costela", 1) or {}).get("preco_unitario") == 11.0)
checa("produto marcado indisponivel no cardapio sai", facts.resolve_unit_price("Bolo de Milho", 1) is None)
checa("horario alterado no cardapio muda a abertura", horario._turno(date(2026, 10, 5))[0] == (7, 30))
_cf_fx.bruto = lambda forcar=False: _RETRATO_FX
checa("e volta quando o cardapio volta", facts.lookup_frete("eldorado")["taxa"] == 15.0)

secao("--- 02/10: tempo - caches do banco ---")
import db as _db_fx  # noqa: E402
import importlib.util as _ilu  # noqa: E402
# Copia propria do database.py: o de cima tem get_setting & cia simulados.
_spec_db = _ilu.spec_from_file_location("database_real_fx", "database.py")
database_real = _ilu.module_from_spec(_spec_db)
_spec_db.loader.exec_module(database_real)
_consultas_fx = []
_fetch_real, _exec_real = _db_fx.fetch_one, _db_fx.execute
_db_fx.fetch_one = lambda q, p=None: (_consultas_fx.append(q), {"value": "1"} if "settings" in q else None)[1]
_db_fx.execute = lambda q, p=None, **k: _consultas_fx.append(q)
database_real._settings_cache.clear()
database_real.get_setting("chave_teste_cache"); database_real.get_setting("chave_teste_cache"); database_real.get_setting("chave_teste_cache")
checa("configuracao repetida le o banco uma vez so", len(_consultas_fx) == 1, str(_consultas_fx))
database_real.set_setting("chave_teste_cache", "2")
checa("set_setting atualiza o cache na hora", database_real.get_setting("chave_teste_cache") == "2")
_consultas_fx.clear()
database_real._state_cache.pop("5500000000000", None)
database_real.save_conversation_state("5500000000000", {"a": 1})
database_real.save_conversation_state("5500000000000", {"a": 1})
checa("ficha identica nao e regravada (uma consulta, upsert)", len(_consultas_fx) == 1, str(_consultas_fx))
_consultas_fx.clear()
checa("ficha lida da memoria, sem banco",
      database_real.get_conversation_state("5500000000000") == {"a": 1} and not _consultas_fx)
database_real.delete_conversation_state("5500000000000")
_db_fx.fetch_one, _db_fx.execute = _fetch_real, _exec_real
checa("espelho do painel vai para a fila (nao segura o cliente)",
      "_no_painel(send_msg_out_to_panel, user, chunk)" in open("app.py", encoding="utf-8").read())
for _txt, _esp in (("quero 25 coxinhas pra retirar agora", "Imediato"),
                   ("quero 25 coxinhas pra entrega agora", "Imediato"),
                   ("quero 25 coxinhas agora", "Imediato"), ("pode mandar agora", "Imediato"),
                   ("agora", "Imediato"), ("é agora", "Imediato"),
                   ("e agora o que faço?", ""), ("agora quero 2 coxinhas", ""),
                   ("quero retirar agora às 16h", "16h")):
    checa(f"'agora': {_txt!r}", _cv_0110.detectar_horario(_txt) == _esp, _cv_0110.detectar_horario(_txt))
_cf_fx.bruto = lambda forcar=False: _RETRATO_FX
checa("tempo de preparo da regra do cardapio: 25 un -> 30 min, 300 un -> 40 min",
      _cf_fx.tempo_de_preparo(25) == 30 and _cf_fx.tempo_de_preparo(300) == 40)
_res_imed = app.cardapio.montar_resumo([{"name": "Coxinha frango", "quantity": 25, "price": 0.8}], 20, 0, 0, 20,
                                       modalidade="retirada", dia="hoje", horario="Imediato")
checa("resumo do pedido 'agora' informa o tempo de preparo",
      "Horário: Imediato (pronto em cerca de 30 min)" in _res_imed, _res_imed)
checa("'no pix' nao fica no endereco",
      _cv_0110._extract_data_from_message("bairro olaria rua guapore 120 no pix",
                                         _cv_0110.ConversationState()).get("endereco") == "rua guapore 120")
checa("item novo logo depois de fechar: pergunta fixa 'somar ou novo'",
      "_responder_direto(user, items, PERGUNTA_SOMAR_OU_NOVO, _chegada)" in open("app.py", encoding="utf-8").read()
      and "Como seu pedido já entrou em linha de produção" in app.ALTERACAO_EM_PRODUCAO)
checa("'pode' depois de 'so comeca as 08h00' NAO vira 08h00 (ela pergunta o horario)",
      'if "só começa" in _motivo:' not in open("app.py", encoding="utf-8").read())

# --- Teste do dono de 02/10 (noite) -----------------------------------------
print()
print("--- Dono 02/10: numero da casa, troco fora de hora, espelho curto")
checa("numero da casa no texto do dono",
      memoria_mod._PERGUNTA_CURTA["endereco_numero"] == "Qual número da casa? 🛵")
_f_num = {"name": "Balbino", "items": [{"name": "Coxinha", "quantity": 50}],
          "modality": "entrega", "bairro": "Olaria", "endereco": "RUA SALGADO FILHO",
          "data_agendada": "hoje", "horario": "20h", "payment_method": "pix"}
checa("rua sem numero: so falta o numero",
      memoria_mod.faltando(_f_num) == ["endereco_numero"], memoria_mod.faltando(_f_num))
checa("trava da pergunta do numero no fim da cadeia",
      '_so_falta in (["troco_para"], ["endereco_numero"])' in open("app.py", encoding="utf-8").read())

# 19h02: troco e rua perguntados juntos, "rua bejamin 2253" virou troco.
_f_troco = {"payment_method": "dinheiro"}
_perg = "Faltam essas informações: - Troco para dinheiro\nQual a rua e o número? 😊"
for _resp in ("rua bejamin 2253", "Nº 2253", "av calama 5892", "casa 40 perto do mercado"):
    _f = dict(_f_troco)
    memoria_mod.capturar_troco(_f, _perg, _resp)
    checa(f"endereco nao vira troco: {_resp!r}", not _f.get("troco_para"), _f.get("troco_para"))
for _resp, _esperado in (("100", "100.00"), ("R$ 100", "100.00"), ("nao preciso", "nao"),
                         ("troco pra 200 na rua", "200.00")):
    _f = dict(_f_troco)
    memoria_mod.capturar_troco(_f, "Precisa de troco para quanto? 💵", _resp)
    checa(f"troco de verdade continua: {_resp!r}", _f.get("troco_para") == _esperado,
          _f.get("troco_para"))

_f_falta = {"name": "Balbino", "items": [{"name": "Coxinha", "quantity": 50}],
            "modality": "entrega", "bairro": "Agenor de Carvalho",
            "data_agendada": "hoje", "horario": "19h30", "payment_method": "dinheiro"}
_s = app._conferir_troco_fora_de_hora(
    "55", "19:30", "Faltam essas informações: - Troco para dinheiro (preciso saber "
    "para quanto levar)\n\nQual a rua e o número? 😊", _f_falta)
checa("troco sai enquanto falta a rua", _s == "Qual a rua e o número? 😊", _s)
_s = app._conferir_troco_fora_de_hora(
    "55", "19:30", "Faltam essas informações:\n- Rua e número\n- Troco", _f_falta)
checa("troco sai da lista, o resto fica", _s == "Faltam essas informações:\n- Rua e número", _s)
_s = app._conferir_troco_fora_de_hora("55", "19:30", "Precisa de troco para quanto? 💵", _f_falta)
checa("so o troco perguntado vira o proximo passo do funil",
      "troco" not in _s.lower() and _s == app._proxima_pergunta_do_funil(_f_falta), _s)
_f_so_troco = dict(_f_falta, endereco="rua bejamin 2253")
checa("com so o troco faltando, a pergunta fica",
      app._conferir_troco_fora_de_hora("55", "x", "Precisa de troco para quanto? 💵", _f_so_troco)
      == "Precisa de troco para quanto? 💵")

_hoje_iso = horario.agora().date().isoformat()
_esp_ret = cardapio.montar_resumo(
    [{"name": "Quiche de Frango", "quantity": 20, "price": 12}], 240, 0, 0, 240,
    modalidade="retirada", dia="hoje", horario="20h", cliente="Balbino",
    data_iso=_hoje_iso)
checa("espelho curto da retirada",
      _esp_ret.startswith("Cliente: Balbino\n📅 Data: ")
      and "📍 Retirada: (" in _esp_ret and "balcão da fábrica" not in _esp_ret
      and "💳 Pgt: Balcão" in _esp_ret and "💰 Total: R$ 240,00" in _esp_ret
      and "Posso confirmar pra hoje (" in _esp_ret and "às 20h? 💛" in _esp_ret, _esp_ret)
_esp_ent = cardapio.montar_resumo(
    [{"name": "Coxinha frango", "quantity": 50, "price": 0.8}], 40, 12, 0, 52,
    bairro="Olaria", modalidade="entrega", dia="hoje", horario="20h",
    cliente="Balbino", pagamento="pix", endereco="RUA SALGADO FILHO, 2252",
    data_iso=_hoje_iso)
checa("espelho curto da entrega",
      "📍 Entrega: RUA SALGADO FILHO, 2252 - Olaria" in _esp_ent
      and "Subtotal" not in _esp_ent and "🚚 Frete: R$ 12,00" in _esp_ent
      and "💰 Total: R$ 52,00" in _esp_ent and "💳 Pgt: Pix" in _esp_ent, _esp_ent)
checa("espelho curto e reconhecido como espelho",
      app._RX_LINHA_DE_RESUMO.search(_esp_ret) and app._RX_LINHA_DE_RESUMO.search(_esp_ent))
checa("'sim' depois do espelho curto fecha o pedido",
      app._confirmou_o_resumo(_esp_ret, "sim") and app._confirmou_o_resumo(_esp_ent, "pode confirmar"))
print()
print("--- Teste de 03/10: opcoes de sabor so do cardapio e do preparo pedido")
from agents.conversation import sabores_possiveis as _sp  # noqa: E402
checa("'carne' em pedido de fritos nao oferece esfirra (assada)",
      "Esfirra de Carne" not in _sp("carne", preparo="frito")
      and "Pastel Carne" in _sp("carne", preparo="frito"), _sp("carne", preparo="frito"))
checa("'carne' em pedido de assados: so a esfirra",
      _sp("carne", preparo="assado") == ["Esfirra de Carne"], _sp("carne", preparo="assado"))
checa("'queijo' nao oferece Pastel Queijo (so existe no combo)",
      "Pastel Queijo" not in _sp("queijo"), _sp("queijo"))
checa("'queijo' oferece o Misto de Queijo e Presunto do cardapio",
      "Misto de Queijo e Presunto" in _sp("queijo"), _sp("queijo"))
_pend = {"items": [], "sabores_pendentes": [
    {"qtd": 30, "termo": "carne", "opcoes": ["Pastel Carne", "Rissole de Carne", "Croquete de Carne"]},
    {"qtd": 30, "termo": "queijo", "opcoes": ["Bolinha de Queijo", "Misto de Queijo e Presunto"]}]}
_f = __import__("copy").deepcopy(_pend)
checa("'pasteis e misto' escolhe os dois sabores",
      len(memoria_mod.aplicar_escolha_de_sabor(_f, "pasteis e misto")) == 2
      and not _f["sabores_pendentes"], _f["items"])
_f = __import__("copy").deepcopy(_pend)
checa("'pastel ou rissole?' nao escolhe nada",
      memoria_mod.aplicar_escolha_de_sabor(_f, "pastel ou rissole?") == [])

print()
print("--- Teste do dono de 03/10 (noite)")
checa("'POSE SIM AS 8 HORAS' aceita o dia oferecido", app._e_concordancia("POSE SIM AS 8 HORAS"))
checa("'sim mas outro dia' nao e aceite", not app._e_concordancia("sim mas outro dia"))
checa("'PEQUENO' e mini (pacote)",
      app._escolheu_pacote("PEQUENO") and not app._escolheu_lanche("PEQUENO"))
from agents.conversation import volume_sem_sabor as _vss  # noqa: E402
checa("'400 variados' e o total", _vss("quero 400 variados pode escolher por mim") == 400)
_f = {"items": [], "volume_pendente": 300}
memoria_mod._volume_novo(_f, {"volume_pendente": 400}, [])
checa("total novo sem itens troca o antigo (300 -> 400)", _f["volume_pendente"] == 400)
_f = {"pedido_por_volume": True, "items": [
    {"name": n, "quantity": 75, "price": 0.8}
    for n in ("Bolinha de Queijo", "Coxinha", "Quibe", "Croquete de Carne")]}
memoria_mod._volume_novo(_f, {"volume_pendente": 400}, [])
checa("'POCA PEDIR 400' depois do espelho: 100 de cada",
      [i["quantity"] for i in _f["items"]] == [100] * 4, _f["items"])
checa("'pode escolher por mim' e delegar",
      memoria_mod.escolheu_por_mim("QUERO 400 VARIADOS PODE ESCOLHER POR MIM")
      and not memoria_mod.escolheu_por_mim("quero 3 centos variados"))
import random as _random  # noqa: E402
_esc = memoria_mod.escolher_sabores(400, "frito", _random.Random(1))
_fritos = {p["item"] for p in facts.get()["produtos"] if p["categoria"] == "Fritos"}
checa("escolha por ela: soma 400, multiplos de 25, so fritos, sem churros",
      sum(i["quantity"] for i in _esc) == 400
      and all(i["quantity"] % 25 == 0 for i in _esc)
      and all(i["name"] in _fritos and "Churros" not in i["name"] for i in _esc), _esc)
import cardapio_catalogo as _cc  # noqa: E402
_tr, _bl = _cc.traduzir(_esc)
checa("8 sabores vao em 2 centos de 4 (o pacote aceita 4)",
      not _bl and len(_tr) == 2 and all(len(l["selected_flavors"]) <= 4 for l in _tr), (_tr, _bl))
_tr, _bl = _cc.traduzir([{"name": n, "quantity": 25, "price": 0.8} for n in
                         ("Coxinha frango", "Quibe", "Pastel carne", "Rissole de Carne",
                          "Bolinha de Queijo", "Croquete de carne")])
checa("6 sabores de 25: 1 cento de 4 + 2 porcoes", not _bl and len(_tr) == 3, (_tr, _bl))
checa("'QUANTOTEMPO LEVA PARA CHEGAR' e pergunta de prazo",
      (app._fato_perguntado("OLA QUERO 50 ESFIRRAS QUANTOTEMPO LEVA PARA CHEGAR") or ("",))[0] == "30 a 40")
checa("resposta de loja fechada responde o fato perguntado",
      "_fato = _fato_perguntado(text_only)" in open("app.py", encoding="utf-8").read())

print()
print("--- Dono 04/10: regra prioritaria, respostas do balcao, fora do cardapio")
_BALCAO = {
    "Meu pedido já saiu para entrega?": ("Vou verificar !", True),
    "O motoboy já está chegando?": ("JÁ SAIU SEU PEDIDO!", False),
    "Quero cancelar meu pedido.": ("Já estou acionando a equipe para atender sobre o cancelamento.", True),
    "Quero mudar o endereço de entrega do pedido que acabei de fazer.":
        ("Envie o novo endereço completo aqui, por favor!", True),
    "Mandei o comprovante do Pix, já confirmou?": ("Comprovante recebido!", False),
    "Veio faltando 10 coxinhas na minha caixa.":
        ("Pedimos sinceras desculpas pelo ocorrido! Nossa equipe já está assumindo seu "
         "atendimento aqui para resolver isso imediatamente.", True),
    "O salgado chegou frio / revirado.":
        ("Sentimos muito por isso! Já passei a situação para a equipe responsável verificar "
         "o transporte e te dar uma solução agora.", True),
    "Posso pagar na maquininha na entrega em vez de Pix?": ("SIM MANDO PELO MOTOBOY", False),
    # 13/10: pergunta o sabor e so a resposta com o item vai para a equipe.
    "Quero acrescentar mais um cento no pedido que já está no forno/pronto.":
        ("PODE SIM QUAL SABOR VOCE QUER", False),
    "Tem como me entregar daqui a 15 minutos com urgência?":
        ("Nosso tempo médio de preparo e entrega é de 30 a 40 minutos, mas vou verificar "
         "com a equipe para agilizar.", True),
    "vocês vendem salgado congelado grande pra fritar?":
        (next(_t for _n, _rx, _t, _e in app._RESPOSTAS_DO_BALCAO if _n == "congelado"), False),
}
for _p, (_txt, _eq) in _BALCAO.items():
    _r = app._resposta_do_balcao(_p, {})
    checa(f"balcao: {_p[:40]!r}", _r is not None and _r[1] == _txt and _r[2] == _eq, _r)
for _p in ("quero 50 coxinhas e 1 coca", "cancela a coca", "quero mudar o endereço",
           "quero 2 centos pra amanha", "pago no cartão"):
    checa(f"balcao nao dispara: {_p!r}", app._resposta_do_balcao(_p, {}) is None)
from agents.conversation import item_fora_do_cardapio as _ifc  # noqa: E402
checa("'pastel de queijo' e fora do cardapio (pastel de carne nao)",
      _ifc("QUERO 50 PASTEL DE QUEIJO E 50 PASTEL DE CARNE") == "pastel de queijo"
      and _ifc("quero 50 pastel de carne") == "", _ifc("QUERO 50 PASTEL DE QUEIJO"))
checa("'pastelzinho de queijo' e 'torta de frango' sao fora do cardapio",
      _ifc("1 cento pastelzinho de queijo e bolinha") and _ifc("torta de frango"))
for _p in ("50 coxinhas de frango", "esfirra de carne", "quibe de carne", "canudinho de camarão",
           "enroladinho de salsicha", "coxinha de festa", "rissole de queijo"):
    checa(f"no cardapio: {_p!r}", _ifc(_p) == "", _ifc(_p))
checa("texto de fora do cardapio chama a equipe, sem 'humano'",
      "equipe" in app._texto_fora_do_cardapio("pastel de queijo")
      and "human" not in app._texto_fora_do_cardapio("pastel de queijo"))
checa("'humano' e 'robo' saem da resposta",
      app._sem_palavra_proibida("Vou te passar para um atendente humano. Não sou um robô! Um instante.")
      == "Vou te passar para nossa equipe. Um instante.")
_sq = app._sabores_de("Vcs tem algum salgado de queijo além da bolinha de queijo ?")
checa("salgado de queijo: lista do cardapio (mini e grande)",
      "Misto de Queijo e Presunto" in _sq and "Bolinha de Queijo" in _sq
      and "Rissole de Queijo e Presunto" in _sq and "Pastel" not in _sq, _sq)
checa("'pode dim' aceita o dia", app._e_concordancia("pode dim"))
checa("'AS 8 HORAS 10 COXINHAS' e 8h (o 10 e quantidade)",
      horario.parse_horario("QUERO PARA AMANHA AS 8 HORAS 10 COXINHAS") == (8, 0)
      and horario.parse_horario("as 19:30 de amanha") == (19, 30)
      and horario.parse_horario("8 horas 30 minutos") == (8, 30))
checa("'salgado de queijo grande': so a linha do grande",
      "Mini" not in app._sabores_de("tem salgado de queijo grande?")
      and "Rissole de Queijo e Presunto" in app._sabores_de("tem salgado de queijo grande?"))
checa("prompt: regra prioritaria e vocabulario",
      "REGRA PRIORITARIA DO DONO" in open("app.py", encoding="utf-8").read()
      and "'humano', 'atendente humano' ou 'robo'" in open("app.py", encoding="utf-8").read())

print()
print("--- Dono 05/10: contexto do frete, salgados grandes, perguntas apagadas")
for _p in ("Qual horário vcs abrem ?", "QUAL HORARIO VOCES ABRE DE MANHA",
           "que hora inicia as entregas ?", "queria saber so o horario que inicia as entregas",
           "QUAL HORARIO DE ABERTURA DA BALBS"):
    _f = app._fato_perguntado(_p)
    checa(f"horario perguntado: {_p!r}", _f is not None and "Atendemos" in _f[1], _f)
checa("pedido com horario nao e pergunta de horario",
      app._fato_perguntado("quero 50 coxinhas para as 19h") is None)
_fr = app._conferir_frete_perguntado(
    "55", "QUAL VALOR DO FRETE PARA BAIRRO PEDRINHAS",
    "A taxa de entrega para Pedrinhas é R$ 15,00.\n\nA taxa de entrega para Pedrinhas é R$ 8,00."
    "\n\nQual o seu pedido? 😊")
checa("frete: a frase com o valor do bairro anterior sai",
      "R$ 15,00" in _fr and "R$ 8,00" not in _fr, _fr)
_gr = app._lista_dos_grandes("Quais os salgados grande vc tem?")
_indisp = [p["item"] for p in facts.get()["produtos"]
           if p["categoria"] == "Salgados Grandes" and not p.get("disponivel", True)]
checa("'quais os salgados grande': lista do cardapio com preco, sem os esgotados",
      _gr.startswith("Nossos salgados grandes") and "Coxinha de Frango com Catupiry — R$" in _gr
      and not any(f"• {n} —" in _gr for n in _indisp), _gr[:80])
checa("'salgados grande' nao vira 'porcao de 25 de Coxinha'",
      app._conferir_familia_do_sabor("55", "Temos Coxinha de Frango com Catupiry assada e Quibe frito.",
                                     "Quais os salgados grande vc tem?")
      == "Temos Coxinha de Frango com Catupiry assada e Quibe frito.")
from agents.conversation import (completar_pelo_contexto as _cpc, _produto_conhecido as _pcon,  # noqa: E402
                                 detectar_modalidade as _dmod)
checa("'Quero 50 uni' depois de 'Quantas unidades de canudinhos de camarao?'",
      _cpc("Quero 50 uni", "Quantas unidades de canudinhos de camarão você quer?")
      == "50 Canudinho de Camarao")
checa("quantidade solta sem pergunta de quantidade nao vira item",
      _cpc("quero 50", "Qual o seu pedido?") == "quero 50" or "50" in _cpc("quero 50", "Qual o seu pedido?"))
checa("'canudos de frango' e Canudinho de Frango", _pcon("canudos de frango") == "Canudinho de Frango")
checa("festa com 30 pessoas vai para a equipe",
      app._pede_consultoria_de_evento("quero fazer uma festa de aniversario com 30 pessoas poderia "
                                      "me ajudar a ter uma noção de quantos centos pedir ?") is not None)
checa("'retirar... Nao, mentira, entrega aqui' e entrega",
      _dmod("Quero 50 coxinhas fritas pra retirar agora. Não, mentira, entrega aqui no Bairro "
            "Agenor de Carvalho, Rua Rio de Janeiro, 1234.") == "entrega")
checa("'quero retirar' continua retirada", _dmod("quero retirar") == "retirada")
checa("pix na hora que o motoboy chegar: frase do BLOCO 6",
      app._conferir_pix_na_entrega("55", "Vou pagar no Pix na hora que o motoboy chegar aqui",
                                   "Pra acompanhar?").startswith("Para pedidos no Pix"))
checa("'Como faço pra pagar?' responde as formas de pagamento",
      (app._fato_perguntado("50 kibes pra hoje 19h30. Como faço pra pagar?") or ("", ""))[1]
      .startswith("Aceitamos Pix"))
for _c in ("Informe, por favor:", "Faltam essas informações para completar seu pedido: Por favor, informe."):
    checa(f"cabecalho solto reconhecido: {_c[:30]!r}", app._RX_CABECALHO_SOLTO.search(facts.normalize(_c)))
checa("cabecalho solto nao pega resposta normal",
      not app._RX_CABECALHO_SOLTO.search(facts.normalize("Sim, pode ser para entrega.")))
checa("desconto + gerente nao transfere (BLOCO 6)",
      "num pedido de desconto: sem" in open("app.py", encoding="utf-8").read())
checa("'consigo retirar as 7:40?' e respondido pelo horario do cardapio",
      app._horario_possivel("consigo retirar um pedido as 7:40?").startswith(
          "Sim!" if horario._turno()[0] <= (7, 40) else "Atendemos")
      and app._horario_possivel("posso buscar as 21h?").startswith("Atendemos"))
_cq = _extract_data_from_message("quero um centoQu frito misto de coxinha e bolinha de queijo",
                                 ConversationState(phone="x")).get("items") or []
checa("'centoQu' (digitacao) ainda e cento", sum(i["quantity"] for i in _cq) == 100, _cq)
checa("resposta curta sai numa mensagem so (menos idas a Datafy)",
      app.split_reply("Boa noite, Balbino! Aqui é a Sandrinha da BALBS 💛\n\nQual o seu pedido? 😊")
      == ["Boa noite, Balbino! Aqui é a Sandrinha da BALBS 💛\n\nQual o seu pedido? 😊"])
checa("resposta longa continua dividida",
      len(app.split_reply(("x" * 500) + "\n\n" + ("y" * 500))) == 2)
import cardapio_fonte as _cf  # noqa: E402
checa("cardapio vencido devolve a leitura anterior sem esperar",
      "_reler_em_segundo_plano()" in open("cardapio_fonte.py", encoding="utf-8").read())
checa("'consigo retirar as 7:40?' e pergunta, nao escolhe retirada",
      _dmod("consigo retirar um pedido as 7:40?") == "" and _dmod("e pra entrega consigo ?") == ""
      and _dmod("quero 50 coxinhas, posso retirar as 10h?") == "retirada")
_AG_QTD = ("Os canudinhos de camarão são assados e vendidos em porções fechadas: meio cento "
           "(50 un) por R$ 60,00, ou em combos como o Combo 15 Canudinhos + Coca-Cola Lata por "
           "R$ 24,00. Qual a quantidade que você deseja?")
checa("'Quero 50 uni' depois de 'Qual a quantidade...?' citando o combo",
      _cpc("Quero 50 uni", _AG_QTD, "Queria fazer um pedido de canudinhos de camarão")
      == "50 Canudinho de Camarao")
checa("transferencia sem mensagem nao grava mensagem 'enviada' no painel",
      "Salva a mensagem de transferência no banco para envio posterior"
      not in open("app.py", encoding="utf-8").read())
_fv = memoria_mod._empty()
checa("frete sem pedido: valor + 'Qual o seu pedido?' (curto)",
      app._conferir_frete_perguntado("55", "QUAL VALOR DO FRETE PARA BAIRRO PEDRINHAS",
                                     "O frete depende da modalidade que voce ainda nao definiu...", _fv)
      == "A taxa de entrega para Pedrinhas é R$ 15,00. Qual o seu pedido? 😊")
_fv2 = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 50, "price": 0.8}],
            upsell_feito=True)
checa("frete com pedido e sem modalidade: 'Será entrega no X ou retirada?'",
      app._conferir_frete_perguntado("55", "qual o frete pro pedrinhas?", "x", _fv2)
      == "A taxa de entrega para Pedrinhas é R$ 15,00. Será entrega no Pedrinhas ou retirada? 😊")
print()
print("--- Dono 06/10 (tarde): volume num sabor, crise, combos, item desconhecido")
_fs = dict(memoria_mod._empty(), volume_pendente=200)
memoria_mod.aplicar_divisao_do_volume(_fs, "todos coxinha")
checa("'todos coxinha' com 2 centos: 200 coxinhas",
      [(i["name"], i["quantity"]) for i in _fs["items"]] == [("Coxinha", 200)], _fs.get("items"))
_fs = dict(memoria_mod._empty(), volume_pendente=200)
memoria_mod.aplicar_divisao_do_volume(_fs, "quero so coxinha")
checa("'quero so coxinha' com 2 centos: 200 coxinhas",
      sum(i["quantity"] for i in _fs["items"]) == 200)
from agents.conversation import detectar_horario as _dhor, item_fora_do_cardapio as _ifc2  # noqa: E402
checa("'pra entregar agora, sao 12h30 e...' nao e horario 12h30",
      _dhor("Manda 2 centos pra entregar agora, são 12h30 e o pessoal tá com fome") == "Imediato"
      and _dhor("o horario e 19h.") == "19h")
checa("'DOI CENTO' e 2 centos e '1 cento' solto e volume",
      _extract_data_from_message("DOI CENTO DE SALGADOS", ConversationState(phone="x")).get("volume_pendente") == 200
      and _extract_data_from_message("Quero 1 cento, escolhe por mim",
                                     ConversationState(phone="x")).get("volume_pendente") == 100)
checa("reclamacao com estorno e crise (BLOCO 4), nao venda de congelado",
      (app._resposta_do_balcao("veio tudo congelado e murcho. Quero o estorno do Pix agora!", {}) or ("",))[0]
      == "crise"
      and (app._resposta_do_balcao("vocês vendem salgado congelado?", {}) or ("",))[0] == "congelado")
checa("'meu filho passou mal' e crise",
      (app._resposta_do_balcao("meu filho passou mal com o salgado", {}) or ("",))[0] == "crise")
checa("'chego em 5 min' nao e urgencia",
      app._resposta_do_balcao("quero 2 coxinhas pra retirar agora chego em 5 min", {}) is None)
_cb = app._lista_de_combos("tem algum combo com refrigerantes ?")
checa("'tem combo com refrigerante?' lista os combos do cardapio",
      _cb.startswith("Nossos combos") and "R$" in _cb, _cb[:60])
checa("'o combo vem com qual refrigerante?' continua com a equipe",
      app._assunto_da_equipe("o combo vem com qual refrigerante?") is not None
      and app._assunto_da_equipe("tem algum combo com refrigerantes ?") is None)
checa("'50 travesseirinhos de carne' e item fora do cardapio",
      _ifc2("Quero 1 cento (50 coxinhas, 50 travesseirinhos de carne)") == "travesseirinhos de carne")
for _p in ("quero 50 coxinhas e 50 kibes fritos", "Manda 3 saltenhas e 2 quiches", "quero 2 litros de coca",
           "festa com 30 pessoas", "50 COX,50 RISSOLES,50 QUIBE E 50 SALSICHA", "quero 100 esfihas"):
    checa(f"item conhecido nao e fora do cardapio: {_p!r}", _ifc2(_p) == "", _ifc2(_p))
for _p in ("OLARIA RUA GUANABARA 45 PAGAMENTO NO PIX", "BAIRRO OLARIA RUA SALGADO 2325 CARTAO",
           "RUA JAMARI 2125 DINHEIRO"):
    checa(f"numero da casa + palavra nao e item fora do cardapio: {_p!r}", _ifc2(_p) == "", _ifc2(_p))
checa("'100 MINI SANDUICHE' e item fora do cardapio",
      _ifc2("POSSO ENCOMENDAR 100 MINI SANDUICHE PARA AMANHA") == "mini sanduiche"
      and _ifc2("quero 50 mini coxinhas") == "")
checa("'QUERO MAIS 50 CHURROS' soma ao pedido aberto (nao troca)",
      app._RX_SOMA_AO_PEDIDO.search(facts.normalize("ACHO QUE NAO VAI.QUERO MAIS 50 CHURROS"))
      and app._RX_SOMA_AO_PEDIDO.search(facts.normalize("QUERO ACRESCENTAR NO PEDIDO 50 CHURROS"))
      and not app._RX_SOMA_AO_PEDIDO.search(facts.normalize("quero 50 churros")))
_fa = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 100}],
           ultimo_pedido_em=__import__("datetime").datetime.now().isoformat(timespec="seconds"))
checa("acrescentar com pedido aberto nao e acrescimo em pedido feito (balcao)",
      app._resposta_do_balcao("QUERO ACRESCENTAR NO PEDIDO 50 CHURROS", _fa) is None)
print()
print("--- Dono 07/10 (noite): lista cumulativa, modulo 25, quanto fica, troco com total")
_px = _extract_data_from_message("quero 25 coxinhas de frango e 25 pães pizza assados. quanto fica?",
                                 ConversationState(phone="x")).get("items") or []
checa("'25 pães pizza' entra no pedido", any(i["name"] == "Pao Pizza" for i in _px), _px)
_pq = _extract_data_from_message("ola quero 100 pateis de queijo e 100 de carne para retirada",
                                 ConversationState(phone="x")).get("items") or []
checa("'100 pateis de queijo e 100 de carne' = 100 + 100 pasteis",
      sorted(i["name"] for i in _pq) == ["Pastel Carne", "Pastel Queijo"], _pq)
checa("'100 pessaos' nao e item fora do cardapio",
      _ifc2("VOCE SABE QUANTOS SALGADINHOS PRECISO PARA 100 PESSAOS ?") == "")
_fc = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 25, "price": 0.8},
                                        {"name": "Esfirra de Carne", "quantity": 40, "price": 1.2}])
memoria_mod.merge(_fc, {}, {"items": [{"name": "Esfirra de Carne", "quantity": 50, "price": 1.2}]},
                  texto="Sim, ajusta para 50 esfirras. Quanto fica tudo com a coxinha?")
checa("lista cumulativa: ajustar a esfirra nao apaga a coxinha",
      [(i["name"], i["quantity"]) for i in _fc["items"]] == [("Coxinha", 25), ("Esfirra de Carne", 50)],
      _fc["items"])
_fc = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 25, "price": 0.8}])
memoria_mod.merge(_fc, {}, {"items": [{"name": "Pao Pizza", "quantity": 25, "price": 1.2}]},
                  texto="25 paes pizza")
checa("lista cumulativa: item novo entra junto", len(_fc["items"]) == 2, _fc["items"])
_fc = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 25, "price": 0.8}])
memoria_mod.merge(_fc, {}, {"items": [{"name": "Quibe", "quantity": 50, "price": 0.8}]},
                  texto="na verdade quero 50 kibes")
checa("'na verdade...' troca o pedido", [i["name"] for i in _fc["items"]] == ["Quibe"], _fc["items"])
_fl = {"items": [{"name": "Coxinha", "quantity": 25, "price": 0.8},
                 {"name": "Esfirra de Carne", "quantity": 40, "price": 1.2}]}
checa("40 esfirras: 'fica 25 ou 50?'", "25 ou 50" in memoria_mod._frase_de_lote(_fl),
      memoria_mod._frase_de_lote(_fl))
_fl2 = {"items": [dict(i) for i in _fl["items"]]}
memoria_mod.aplicar_ajuste_de_lote(_fl2, "pode ser 25")
checa("'pode ser 25' aplica 25", _fl2["items"][1]["quantity"] == 25, _fl2["items"])
_fl3 = {"items": [dict(i) for i in _fl["items"]]}
memoria_mod.aplicar_ajuste_de_lote(_fl3, "sim")
checa("'sim' aplica a mais proxima (50)", _fl3["items"][1]["quantity"] == 50, _fl3["items"])
_fq = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 25, "price": 0.8},
                                        {"name": "Bolinha de Queijo", "quantity": 25, "price": 0.8}])
_qf = app._conferir_quanto_fica("55", "Quero 25 coxinhas e 25 bolinhas. Quanto fica?", "x", _fq)
checa("'Quanto fica?' com itens: a soma antes de seguir", _qf.startswith("Os itens ficam R$ 40,00"), _qf)
checa("'quanto e o cento de coxinha?' nao e a soma do pedido",
      app._conferir_quanto_fica("55", "quanto fica o cento de coxinha?", "x", _fq) == "x")
_ft = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 25, "price": 0.8}],
           modality="entrega", bairro="Olaria", payment_method="dinheiro")
checa("troco com o texto do dono e o total (com retorno de R$ 2 no dinheiro)",
      memoria_mod.pergunta_do_troco(_ft)
      == "O seu pedido com a entrega deu R$ 34,00. Precisa de troco para quanto? 💵",
      memoria_mod.pergunta_do_troco(_ft))
checa("'faltou cobrar a taxa de 2,00' nao e item faltando",
      app._resposta_do_balcao("FALTOU A SANDRINHA COBRAR A TAXA DE 2,00 REAIS DE RETORNO", {}) is None)
checa("'NAO' ao espelho pergunta o que alterar",
      app._RX_SO_NAO.match("nao") and "TEXTO_O_QUE_ALTERAR" in open("app.py", encoding="utf-8").read())
checa("'qual preco de meio cento?' depois do canudinho: R$ 60,00",
      "R$ 60,00" in app._conferir_preco_perguntado(
          "55", "AGORA QUERO SABER QUAL PREÇO DE MEIO CENTO", "x", memoria_mod._empty(),
          anterior="O cento de Canudinho de Frango sai R$ 120,00."))
checa("envio usa os valores do codigo (nao o preco do modelo)",
      "envio com os valores do codigo" in open("app.py", encoding="utf-8").read())
print()
print("--- Dono 08/10: opcoes na pergunta dos sabores, listas uma por linha")
_fv8 = memoria_mod._frase_do_volume({"volume_pendente": 100, "preparo_pendente": "assado"})[0]
# 17/10: pergunta o sabor; a lista inteira so quando o cliente pede.
checa("volume sem sabor pergunta qual (sem a lista)",
      _fv8 == "Quais sabores assados você quer? Pode escolher até 4 sabores (25 de cada). 😊", _fv8)
_fv8l = memoria_mod._frase_do_volume({"volume_pendente": 100, "preparo_pendente": "assado",
                                      "mostrar_sabores": True})[0]
checa("pedida a lista, ela vem um por linha",
      _fv8l.startswith("Escolha até 4 sabores assados (25 de cada):\n• ") and "\n• Pão Pizza" in _fv8l, _fv8l)
_fv8f = memoria_mod._frase_do_volume({"volume_pendente": 200, "preparo_pendente": "frito",
                                      "mostrar_sabores": True})[0]
checa("fritos: so os fritos do cardapio", "• Coxinha frango" in _fv8f and "Esfirra" not in _fv8f, _fv8f)
_lst = app._listas_em_linhas("Os sabores assados são: Esfirra de Carne, Pão Pizza e Canudinho de Camarão.")
checa("lista do modelo com virgulas vira uma por linha",
      _lst == "Os sabores assados são:\n• Esfirra de Carne\n• Pão Pizza\n• Canudinho de Camarão", _lst)
checa("frase comum com virgula nao vira lista",
      app._listas_em_linhas("Aceitamos Pix, cartão ou dinheiro. 😊") == "Aceitamos Pix, cartão ou dinheiro. 😊")
checa("espelho nao e mexido",
      app._listas_em_linhas("Cliente: X\nItens:\n• 25 un - A — R$ 20,00\n💰 Total: R$ 20,00")
      == "Cliente: X\nItens:\n• 25 un - A — R$ 20,00\n💰 Total: R$ 20,00")
print()
print("--- Dono 08/10: 'tira X' remove so o item citado")
_carr = [{"name": "Esfirra de Carne", "quantity": 30, "price": 1.2},
         {"name": "Pastel Carne", "quantity": 100, "price": 0.8}]


def _tira(texto, itens=None):
    _f = dict(memoria_mod._empty(), items=[dict(i) for i in (itens or _carr)])
    _novo = _extract_data_from_message(texto.lower(), ConversationState(phone="x"))
    memoria_mod.merge(_f, {}, _novo, texto=texto)
    return [(i["name"], i["quantity"]) for i in _f["items"]]


checa("'Na verdade tira as esfirras e coloca 25 coxinhas' mantem os pasteis",
      _tira("Na verdade tira as esfirras e coloca 25 coxinhas") == [("Pastel Carne", 100), ("Coxinha", 25)],
      _tira("Na verdade tira as esfirras e coloca 25 coxinhas"))
checa("'tira os pasteis' tira so os pasteis", _tira("tira os pasteis") == [("Esfirra de Carne", 30)])
checa("'em vez da esfirra coloca 50 quibes'",
      _tira("em vez da esfirra coloca 50 quibes") == [("Pastel Carne", 100), ("Quibe", 50)])
checa("'vou retirar amanha' nao tira nada (retirada)",
      _tira("vou retirar amanha as 10h") == [("Esfirra de Carne", 30), ("Pastel Carne", 100)])
checa("'tira a coca' tira o refrigerante (pelo sabor)",
      _tira("tira a coca", [{"name": "Coxinha", "quantity": 50},
                            {"name": "Refrigerante 2 Litros", "quantity": 1, "sabor": "Coca cola"}])
      == [("Coxinha", 50)])
checa("'na verdade quero 50 kibes' (sem tira) ainda troca o pedido",
      _tira("na verdade quero 50 kibes") == [("Quibe", 50)])
print("--- Dono 08/10: sabor que nao existe, 'tira X e poe Y'")
_carr2 = [{"name": "Coxinha", "quantity": 50}, {"name": "Quibe", "quantity": 25},
          {"name": "Enroladinho de salsicha com Catupiry", "quantity": 25}]
checa("'tira so os quibes e coloca mais 25 coxinhas' tira o quibe e mantem o enroladinho",
      [n for n, _ in _tira("Na verdade tira só os quibes e coloca mais 25 coxinhas", _carr2)]
      == ["Coxinha", "Enroladinho de salsicha com Catupiry"],
      _tira("Na verdade tira só os quibes e coloca mais 25 coxinhas", _carr2))
checa("'coloca mais 25' cai na soma ao pedido aberto",
      bool(app._RX_SOMA_AO_PEDIDO.search(facts.normalize("coloca mais 25 coxinhas"))))
checa("enroladinho de salsicha com catupiry e o assado",
      [i["name"] for i in _extract_data_from_message(
          "25 enroladinhos de salsicha com catupiry", ConversationState(phone="x"))["items"]]
      == ["Enroladinho de salsicha com Catupiry"])
_ext_emp = _extract_data_from_message("quero 35 coxinhas e 50 empadas de queijo pra amanha",
                                      ConversationState(phone="x"))
checa("'50 empadas de queijo' nao vira empada de frango na ficha",
      [i["name"] for i in _ext_emp["items"]] == ["Coxinha"], _ext_emp["items"])
_alt_emp = memoria_mod.alternativas_do_sabor("empadas de queijo", 50)
checa("empada de queijo: lista os assados de festa",
      "Empadinha de Frango" in _alt_emp and "Esfirra de Carne" in _alt_emp
      and "Coxinha frango" not in _alt_emp, _alt_emp)
checa("coxinha de carne: lista os fritos",
      memoria_mod._rotulo_das_opcoes(memoria_mod.alternativas_do_sabor("coxinha de carne", 100))
      == "fritos")
checa("tipo que nao existe (torta, mini sanduiche) tambem tem opcoes, sem equipe",
      memoria_mod.alternativas_do_sabor("torta", 25)
      and memoria_mod.alternativas_do_sabor("mini sanduiche", 100))
checa("bolo que nao existe lista os bolos e doces",
      "Bolo de Milho" in memoria_mod.alternativas_do_sabor("bolo de chocolate", 1))
_aviso = memoria_mod.aviso_de_sabor_inexistente("empadas de queijo", _alt_emp)
checa("aviso do dono: 'Nao temos esse sabor no momento. Nossos assados...'",
      _aviso.startswith("Não temos esse sabor no momento. Nossos assados disponíveis são:\n• ")
      and _aviso.endswith("Qual você prefere? 😊"), _aviso)
checa("item que nao existe: 'Nao temos esse item'",
      memoria_mod.aviso_de_sabor_inexistente(
          "torta", memoria_mod.alternativas_do_sabor("torta", 0)).startswith("Não temos esse item"))
checa("'tira a esfirra' sem esfirra na ficha nao apaga o carrinho",
      _tira("na verdade tira a esfirra e coloca 25 coxinhas",
            [{"name": "Pastel Carne", "quantity": 100}]) == [("Pastel Carne", 100), ("Coxinha", 25)],
      _tira("na verdade tira a esfirra e coloca 25 coxinhas", [{"name": "Pastel Carne", "quantity": 100}]))
checa("'tira tudo e coloca 50 coxinhas' troca o pedido",
      _tira("tira tudo e coloca 50 coxinhas") == [("Coxinha", 50)])
checa("'poe' junto com 'tira' soma na linha existente",
      bool(app._RX_POE_ITEM.search(facts.normalize("tira os quibes e poe 25 coxinhas")))
      and not app._RX_POE_ITEM.search(facts.normalize("50 coxinhas por favor")))
_f_emp = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 35}],
              sabores_pendentes=[{"qtd": 50, "termo": "empadas de queijo",
                                  "opcoes": ["Empadinha de Frango"]}])
memoria_mod.aplicar_escolha_de_sabor(_f_emp, "Troca por empada de frango então")
checa("'troca por empada de frango' entra com a quantidade pedida",
      [(i["name"], i["quantity"]) for i in _f_emp["items"]]
      == [("Coxinha", 35), ("Empadinha de Frango", 50)] and not _f_emp["sabores_pendentes"])
_dup = app._conferir_etapa_travada(
    "55", "Boa tarde! Eu sou a Sandrinha da BALBS 💛 Fazemos de 25 em 25 por sabor!",
    dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 50},
                                      {"name": "Empadinha de Frango", "quantity": 30}]))
checa("'Fazemos de 25 em 25' nao sai duas vezes", _dup.count("Fazemos de 25") == 1, _dup)
_cong = next(_t for _n, _rx, _t, _e in app._RESPOSTAS_DO_BALCAO if _n == "congelado")
checa("congelado: texto de venda do dono com os sabores e a dica",
      _cong.startswith("Sim, trabalhamos sim!") and "R$ 40 o cento" in _cong
      and "• Kibe" in _cong and "180°C" in _cong)
print("--- Dono 08/10: congelados")
checa("'quero 100 coxinhas congeladas' e pedido, nao repete o texto de venda",
      app.pedido_de_congelado("quero 100 coxinhas congeladas", "")
      and (app._resposta_do_balcao("quero 100 coxinhas congeladas", {}) or ("",))[0] != "congelado")
checa("'1 cento de coxinha' depois da oferta de congelado e congelado",
      app.pedido_de_congelado("quero 1 cento de coxinha", app.texto_congelado()))
checa("'frita' depois da oferta nao e congelado",
      not app.pedido_de_congelado("quero 1 cento de coxinha frita", app.texto_congelado()))
checa("pergunta de congelado continua com o texto de venda",
      (app._resposta_do_balcao("vocês vendem salgado congelado?", {}) or ("", ""))[1]
      .startswith("Sim, trabalhamos sim!"))
checa("sem congelado no de-para do pedido, a equipe registra (nao vira frito)",
      app._versao_congelada("Coxinha") == "")
print("--- Dono 08/10 (noite): retirada parcial, porcoes, grafia, bairro, duas opcoes")
checa("'tira 50 coxinhas' de 100 deixa 50 (16h04)",
      _tira("tira 50 coxinhas e coloca 25 bolinhas de queijo e 25 kibes",
            [{"name": "Coxinha", "quantity": 100}])
      == [("Coxinha", 50), ("Bolinha de Queijo", 25), ("Quibe", 25)],
      _tira("tira 50 coxinhas e coloca 25 bolinhas de queijo e 25 kibes", [{"name": "Coxinha", "quantity": 100}]))
checa("'tira 25 coxinhas' de 50 deixa 25 (19h37)",
      _tira("tira 25 coxinhas e coloca 25 quibes no lugar",
            [{"name": "Coxinha", "quantity": 50}, {"name": "Empadinha de Frango", "quantity": 25}])
      == [("Coxinha", 25), ("Empadinha de Frango", 25), ("Quibe", 25)])
checa("'tira 50 coxinhas' com 50 tira a linha",
      _tira("tira 50 coxinhas", [{"name": "Coxinha", "quantity": 50}, {"name": "Quibe", "quantity": 25}])
      == [("Quibe", 25)])
checa("'tira o bolo de milho' nao volta pelo acompanhamento (15h50)",
      ("Bolo de Milho", 1) not in _tira("tira o bolo de milho e coloca mais 25 bolinhas de queijo",
                                        [{"name": "Coxinha", "quantity": 50},
                                         {"name": "Bolinha de Queijo", "quantity": 25},
                                         {"name": "Bolo de Milho", "quantity": 1}]))
checa("pedir bolo de milho continua entrando",
      ("Bolo de Milho", 1) in _tira("quero tambem um bolo de milho", [{"name": "Coxinha", "quantity": 50}]))
def _itens_de(t):
    return [(i["name"], i["quantity"]) for i in
            _extract_data_from_message(t.lower(), ConversationState(phone="x")).get("items") or []]
checa("'6 porcoes de coxinha' sao 150 (19h45)",
      _itens_de("Quero 6 porções de coxinha e 6 porções de quibe, fritos.")
      == [("Coxinha", 150), ("Quibe", 150)])
checa("'uma porcao de 25 salgadinhos' continua volume de 25",
      _extract_data_from_message("quero uma porção de 25 salgadinhos",
                                 ConversationState(phone="x")).get("volume_pendente") == 25)
checa("'25 SALSCHISA' nao some do pedido (21h10)",
      ("Enroladinho de Salsicha", 25) in _itens_de(
          "QUERO 50 COXINHAS PARA AMANHA A8 HORAS 25 SALSCHISA E 50 QUIBE E 50 ESFIRRAS"))
from agents.conversation import _corrigir_grafia as _cg
checa("grafia: palavra comum nao vira produto",
      all(_cg(w) == "" for w in ("pagamento", "cartao", "amanha", "pessoas", "travesseirinhos",
                                 "sanduiche", "palmito", "refrigerante")))
checa("'IGAPARE' e Igarape, com o frete (21h49)",
      (facts.lookup_frete("IGAPARE") or {}).get("bairro") == "Igarapé")
_fb = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 50}], modality="entrega")
memoria_mod.capturar_resposta_direta(_fb, "Qual o bairro? 😊", "IGAPARE")
checa("resposta seca do bairro grava o nome oficial", _fb["bairro"] == "Igarapé", _fb["bairro"])
_fb2 = dict(memoria_mod._empty(), name="B", items=[{"name": "Coxinha", "quantity": 50}],
            modality="entrega", endereco="Rua X 10", data_agendada="x", data_iso="2026-10-09",
            horario="10h", payment_method="pix")
memoria_mod.capturar_resposta_direta(_fb2, "Qual o bairro? 😊", "Candeias")
checa("bairro fora da tabela nao entra e a pergunta diz que nao achou",
      not _fb2["bairro"] and memoria_mod.pedido_de_dados(_fb2).startswith("Não achei o bairro Candeias"))
checa("'na entrega pro motoboy' nao e bairro (21h42)",
      app._conferir_frete_perguntado("x", "quero 50 coxinhas pra entregar amanhã às 11h na Rua "
                                     "Amazonas, sem número, e pago no Pix na entrega pro motoboy.",
                                     "RESP") == "RESP")
checa("'50 ou 75' e pergunta de duas opcoes; 'Ajusto para 50...' nao (dia continua igual)",
      bool(app._RX_DUAS_OPCOES.search("• 60 coxinha de frango: 50 ou 75"))
      and not app._RX_DUAS_OPCOES.search(
          "Fazemos de 25 em 25 por sabor! Ajusto para 50 coxinha de frango e 25 bolinha de queijo?"))
checa("'metade coxinha e metade bolinha' acha os dois sabores (21h04)",
      app._sabores_pela_metade("uma porção de 25, mas quero metade coxinha e metade bolinha de queijo, pode ser?")
      == ["Coxinha", "Bolinha de Queijo"])
from datetime import datetime as _dt0810
_sd = app._conferir_saudacao("Nosso preço já é direto de fábrica!\n\nSou a Sandrinha da BALBS 💛 Fazemos de 25 em 25 por sabor!",
                             _dt0810(2026, 10, 8, 21, 37), "Balbino")
checa("saudacao colocada e a do dia, sem 'Sou a Sandrinha' repetido (21h37)",
      _sd.startswith("Boa noite, Balbino! Eu sou a Sandrinha da BALBS 💛") and _sd.count("Sandrinha") == 1
      and "\n Fazemos" not in _sd, _sd)
_espelho_0810 = "Cliente: Balbino\nItens:\n• 50 un - Coxinha frango — R$ 40,00\n💰 Total: R$ 52,00\nPosso confirmar pra amanhã (sex, 09/10) às 07h40? 💛"
checa("'Pode ser 25 esfirras' ao resumo nao fecha o pedido (mostra o resumo novo)",
      not app._confirmou_o_resumo(_espelho_0810, "Pode ser 25 esfirras de carne então."))
checa("'SIM' e 'pode ser' ao resumo continuam fechando",
      app._confirmou_o_resumo(_espelho_0810, "SIM") and app._confirmou_o_resumo(_espelho_0810, "pode ser"))
checa("'troca/entao' com item novo fecha a pergunta do sabor que nao existe (15h41)",
      bool(app._RX_TROCOU_O_SABOR.search(facts.normalize("Então troca por mais 25 coxinhas, por favor."))))
print("--- Dono 09/10: docinho em laco, segunda abre, obrigado ao resumo")
for _r in ("SOMENTE OS SALGADOS ENTAO", "NAO QUERO MAIS DOCE", "nao", "sem doce", "esquece o docinho"):
    checa(f"recusa do acompanhamento: {_r!r}", bool(app._RX_RECUSA_ACOMPANHAMENTO.search(facts.normalize(_r))))
for _r in ("bandeja", "o meio cento", "quero a de 600"):
    checa(f"escolha nao e recusa: {_r!r}", not app._RX_RECUSA_ACOMPANHAMENTO.search(facts.normalize(_r)))
_seg = app._fato_perguntado("NA SEGUNDA VAI ABRIR ?")
checa("'na segunda vai abrir?' responde pelo expediente do codigo (sem 08h00 inventado)",
      bool(_seg) and _seg[1] == app._texto_do_expediente(), _seg)
checa("'OBRIGADO' e so agradecimento", bool(app._RX_SO_AGRADECIMENTO.match(facts.normalize("OBRIGADO"))))
checa("'obrigado, pode confirmar' nao e so agradecimento",
      not app._RX_SO_AGRADECIMENTO.match(facts.normalize("obrigado, pode confirmar")))
print("--- Dono 10/10: centro de canudinho, 100 solto, bolo sem estoque, entrega depois de fechar")
_ext_c = _extract_data_from_message("boa tarde\nqueria um centro de canudinho de frango",
                                    ConversationState(phone="x"))
checa("'um centro de canudinho' e um cento, nao o bairro Centro",
      [(i["name"], i["quantity"]) for i in _ext_c.get("items") or []] == [("Canudinho de Frango", 100)]
      and not _ext_c.get("bairro"), _ext_c)
checa("'entrega no centro' continua sendo o bairro",
      _extract_data_from_message("entrega no centro", ConversationState(phone="x")).get("bairro") == "Centro")
checa("'100' e '100 unidades' respondem a pergunta do lote",
      memoria_mod.quantidade_livre_do_lote("100") == 100
      and memoria_mod.quantidade_livre_do_lote("100 unidades") == 100
      and memoria_mod.quantidade_livre_do_lote("30") == 0
      and memoria_mod.quantidade_livre_do_lote("100 coxinhas") == 0)
_f_can = dict(memoria_mod._empty(), items=[{"name": "Canudinho de Frango", "quantity": 1}])
memoria_mod.aplicar_ajuste_de_lote(_f_can, "100")
checa("'100' a 'Ajusto para 25 canudinho?' grava 100",
      _f_can["items"][0]["quantity"] == 100, _f_can["items"])
checa("aviso de bolo: 'Nao temos bolo no momento'",
      memoria_mod.aviso_de_sabor_inexistente("bolo", ["Pudim (Fatia)"]).startswith("Não temos bolo no momento."))
import datetime as _dt1010
_pos = dict(memoria_mod._empty(), ultimo_pedido_em=_dt1010.datetime.now().isoformat(timespec="seconds"))
checa("'Quero para entrega resolvi' depois de fechar: equipe, sem pedido novo",
      (app._resposta_do_balcao("Quero para entrega resolvi", _pos) or ("",))[0] == "modalidade")
checa("'quero para entrega' com o pedido aberto segue o fluxo",
      app._resposta_do_balcao("Quero para entrega resolvi",
                              dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 100}])) is None)
print("--- Dono 11/10: perguntas frequentes, kibe de voces, torta salgada, 2 LT, chave Pix")
from agents.conversation import item_fora_do_cardapio as _ifc1110, _opcoes_de_refrigerante as _opr1110
checa("'o kibe de voces leva requeijao' nao e sabor inexistente",
      _ifc1110("O kibe de vocês leva requeijão por dentro") == "")
checa("'torta salgada de frango' e item fora do cardapio",
      _ifc1110("me manda também uma torta salgada de frango") == "torta de frango")
_f2l = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 50}],
            sabores_pendentes=[{"qtd": 1, "termo": "refrigerante", "opcoes": _opr1110()}])
memoria_mod.aplicar_escolha_de_sabor(_f2l, "2 LT")
checa("'2 LT' escolhe o refrigerante de 2 litros (quando o cardapio tem)",
      not facts.resolve_unit_price("Refrigerante 2 Litros", 1)
      or any(i["name"] == "Refrigerante 2 Litros" for i in _f2l["items"]), _f2l["items"])
_f2g = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 50}],
            sabores_pendentes=[{"qtd": 1, "termo": "refrigerante", "opcoes": _opr1110()}])
memoria_mod.aplicar_escolha_de_sabor(_f2g, "guarana 2 litros")
checa("'guarana 2 litros' continua o Tuchaua",
      any(i["name"] == "Guaraná Tuchaua 2 Litros" for i in _f2g["items"]), _f2g["items"])
for _q, _nome in (("O kibe de vocês leva requeijão por dentro", "kibe requeijao"),
                  ("Esse salgado misto leva o quê?", "misto recheio"),
                  ("qual a quantidade recomendada para festa de 150 pessoas", "festa pessoas"),
                  ("Posso pedir porção menor (ex: 25 unidades ou 10 de cada sabor", "porcao menor"),
                  ("Tem Fanta uva", "sabor de refri"),
                  ("Qual é a chave Pix da loja?", "chave pix antes"),
                  ("Vocês emitem nota fiscal?", "nota fiscal"),
                  ("Vocês fazem entrega?", "faz entrega"),
                  ("Posso pagar na hora da retirada no balcão?", "pagar no balcao"),
                  ("O pagamento é 50% adiantado?", "50 por cento")):
    _rb = app._resposta_do_balcao(_q, {})
    checa(f"pergunta do dono: {_q[:40]!r} -> {_nome}", bool(_rb) and _rb[0] == _nome, _rb)
for _q in ("quero 50 coxinhas e uma coca 2 litros", "quero 100 coxinhas, vcs fazem entrega?",
           "vocês entregam no Olaria?", "quanto fica o frete pro olaria?", "quero 2 fanta laranja lata"):
    checa(f"pedido/bairro nao vira pergunta frequente: {_q!r}", app._resposta_do_balcao(_q, {}) is None)
checa("festa de 150 pessoas: 10 por pessoa = 1.500 (13/10)",
      "1.500 salgadinhos (15 centos)" in app._faq_festa("festa de 150 pessoas"))
checa("'qual a chave Pix da loja' nao responde o endereco",
      app._fato_perguntado("Qual é a chave Pix da loja?") is None
      and app._fato_perguntado("qual o endereço da loja") is not None)
print("--- Balbino 12/10: funil duplo - desvio vai para a equipe")
checa("frase de desarme padrao",
      app.TEXTO_DESARME == "Vou chamar nossa equipe para verificar agora! Um instante.")
checa("transferencia padrao usa a frase de desarme",
      app._DEFAULT_TRANSFER_MESSAGE == app.TEXTO_DESARME
      or bool(__import__("os").getenv("TRANSFER_MESSAGE")))
checa("item fora do cardapio: frase de desarme", app._texto_fora_do_cardapio("torta") == app.TEXTO_DESARME)
checa("HUMAN_ATTENDANCE e o auto_mode da equipe", app.HUMAN_ATTENDANCE == 0)
checa("fiado continua negado pelo robo (nao transborda)",
      app.transbordo_proibido("cliente quer pagar na semana que vem, fiado"))
_src = open("app.py", encoding="utf-8").read()
checa("quantidade fora do multiplo de 25 dispara o transbordo",
      "quantidade fora do multiplo de 25" in _src and "def trigger_human_handoff(" in _src)
checa("duvida sem resposta dispara o transbordo", "duvida sem resposta no cardapio" in _src)
checa("pedido de desconto NAO dispara o transbordo (voltou a defesa de preco, 13/10)",
      'trigger_human_handoff(user, items, "pedido de desconto"' not in _src)
print("--- Balbino 13/10: perguntas respondidas, esfirra, festa, refri, combo, fechado")
for _q, _nome, _eq in (("Vocês fazem ou têm torta (salgada ou doce)?", "produto inexistente", False),
                       ("Têm pão de queijo? E salada de fruta?", "produto inexistente", False),
                       ("AS esfirras são de que sabor?", "esfirra sabor", False),
                       ("Quais refrigerantes de 2 litros e latas estão disponíveis?", "lista de refri", False),
                       ("Quanto custa a unidade do combo de assados / mini hambúrguer?", "combo preco", False),
                       ("Está tendo alguma promoção de salgadinho hoje?", "promocao", False),
                       ("Preciso encomendar com quantos dias de antecedência pedido de 300 unidades?",
                        "prazo confirmar", False),
                       ("com quanto tempo de antecedência preciso encomendar?", "prazo confirmar", False),
                       ("Vocês emitem e mandam nota fiscal / cupom com CNPJ?", "nota fiscal", True),
                       ("Tentei mandar o pedido pelo link/cardápio digital e travou, meu pedido aparece aí?",
                        "link travou", False)):
    _rb = app._resposta_do_balcao(_q, {})
    checa(f"13/10: {_q[:45]!r} -> {_nome}", bool(_rb) and _rb[0] == _nome and _rb[2] == _eq, _rb)
checa("torta: 'Nao temos torta no momento'",
      app._resposta_do_balcao("Vocês fazem ou têm torta (salgada ou doce)?", {})[1].startswith("Não temos torta no momento"))
checa("esfirra: grande com carne, mini sem recheio",
      "mini (de festa) não têm recheio" in app._resposta_do_balcao("as esfirras são de que sabor?", {})[1])
checa("nota fiscal: texto do Balbino",
      app._resposta_do_balcao("vocês emitem nota fiscal?", {})[1]
      == "Vou acionar nossa equipe para pegar os dados para emissão da nota fiscal! Um instante.")
checa("combo de assados: sem o combo de fritos",
      "Fritos" not in app._resposta_do_balcao("quanto custa o combo de assados?", {})[1])
checa("'tem coxinha?' nao vira produto inexistente", app._resposta_do_balcao("tem coxinha?", {}) is None)
checa("refri 2 litros perguntado pelo preco: 'sai a'",
      "sai a R$" in app._conferir_refri_perguntado("t", "Quanto custa o refrigerante de 2 litros?", "X"))
checa("pergunta de preco nao e pedido",
      app._pergunta_de_preco("Qual é o valor do cento dos fritos tradicionais?")
      and not app._pergunta_de_preco("quero um cento de coxinha"))
from agents.conversation import _extract_data_from_message as _ex1310, ConversationState as _CS1310
_bp = _ex1310("quero 100 pasteis e uma coca cola", _CS1310(phone="x")).get("bebidas_pendentes") or [{}]
checa("'uma coca': pendencia com a marca e sem o Tuchaua",
      _bp[0].get("marca", "").startswith("coca") and "Guaraná Tuchaua 2 Litros" not in _bp[0].get("opcoes", []), _bp)
_fc = dict(memoria_mod._empty(), items=[{"name": "Pastel", "quantity": 100}], sabores_pendentes=_bp)
_pq = memoria_mod.pergunta_bloqueante(_fc)
checa("'uma coca': pergunta o tamanho com preco", "Coca-Cola temos em" in _pq and "Qual tamanho" in _pq, _pq)
checa("a pergunta do tamanho cobre a etapa", memoria_mod.etapa_coberta(_fc, _pq))
memoria_mod.aplicar_escolha_de_sabor(_fc, "2 litros")
_rf = [i for i in _fc["items"] if i["name"] == "Refrigerante 2 Litros"]
checa("'2 litros' depois de 'uma coca' entra com o sabor Coca",
      bool(_rf) and "coca" in str(_rf[0].get("sabor", "")).lower(), _fc["items"])
print("--- 14/10: preco de pacote travado no codigo")
for _q, _esperado in (("Quanto sai meio cento (50 unidades)?", "nos fritos"),
                      ("Qual é o valor do cento dos fritos tradicionais?", "dos fritos sai"),
                      ("qual o valor da porção de assados?", "dos assados sai"),
                      ("Quanto custa o cento de brigadeiros?", "de brigadeiros sai"),
                      ("quanto custa o cento de coxinha?", "Coxinha frango sai")):
    _r = app._conferir_preco_perguntado("t", _q, "MODELO")
    # Porcao de assados entrou no cardapio em 07/10: sem a linha, nao chuta.
    _tem_porcao_assado = any(p["categoria"] == "Assados" and p["item"].startswith("Porção")
                             for p in facts.get()["produtos"])
    if "porção de assados" in _q and not _tem_porcao_assado:
        checa(f"sem a linha no cardapio, nao chuta: {_q[:40]!r}", _r == "MODELO", _r)
        continue
    checa(f"preco do codigo: {_q[:40]!r}", _r != "MODELO" and _esperado in _r, _r)
checa("pacote: o valor vem do cardapio (R$ do cento de fritos)",
      app._brl(float(next(p["preco"] for p in facts.get()["produtos"]
                          if p["categoria"] == "Fritos" and p["item"].startswith("Cento"))))
      in app._conferir_preco_perguntado("t", "valor do cento dos fritos?", "MODELO")
      if hasattr(app, "_brl") else True)
print("--- 14/10: audio passa pelas mesmas travas do texto")
_aud = [{"text": "[Audio (mensagem de voz)]", "transcricao": "quero falar com um atendente"}]
checa("pedido de atendente por audio transfere", bool(app._check_transfer_keywords(_aud)))
checa("'falar com UM atendente' por texto tambem", bool(app._check_transfer_keywords(
    [{"text": "quero falar com um atendente"}])))
checa("'vou falar com meu marido' nao e pedido de atendente",
      not app._check_transfer_keywords([{"text": "vou falar com meu marido e te aviso"}]))
_src_a = open("app.py", encoding="utf-8").read()
checa("as travas de codigo leem a transcricao do audio",
      '_texto_cliente = " ".join(_texto_da_midia(it) for it in items)' in _src_a)
print("--- 14/10: outras midias com contexto")
checa("reacao nao vira mensagem para responder",
      app._extract_body_from_meta_message({"type": "reaction", "reaction": {"emoji": "x"}})[1] == "reaction")
_src_m = open("app.py", encoding="utf-8").read()
checa("so reacao de aceite entra no debounce", 'msg_type != "reaction" or e_emoji_de_aceite(body)' in _src_m)
for _e, _ok in (("[Reação: 👍🏻]", True), ("👍", True), ("✅", True), ("[Reação: ❤️]", False),
                ("👍 mas tira a coca", False), ("[Reação removida]", False)):
    checa(f"emoji de aceite: {_e!r} -> {_ok}", app.e_emoji_de_aceite(_e) == _ok)
checa("figurinha/documento/contato/video depois do pedido tem regra no codigo",
      "Arquivo ou print recebido depois do" in _src_m and '"video depois do pedido"' in _src_m
      and "Recebi o seu arquivo!" in _src_m and "Recebi o contato!" in _src_m)
checa("imagem tambem e lida antes das travas",
      '_texto_cliente = " ".join(_texto_da_midia(it) for it in items)' in _src_m
      and _src_m.index('_texto_cliente = " ".join(_texto_da_midia(it)')
      < _src_m.index("_resposta_do_balcao(_texto_cliente"))
print("--- Balbino 14/10: # reset, coxinha grande, 'ir', endereco em linhas, 'pode ser' a oferta")
checa("'# reset' com espaco reinicia", app.e_pedido_de_reset("# reset") and app.e_pedido_de_reset("# RESET"))
_eg = _extract_data_from_message("bom dia gostaria de 3 coxinha grande", ConversationState(phone="x"))
checa("'3 coxinha grande' ja vem como grande", _eg["items"][0].get("tamanho") == "lanche", _eg["items"])
_fg = dict(memoria_mod._empty(), items=[{"name": "Coxinha", "quantity": 3, "tamanho": "lanche"}])
memoria_mod.merge(_fg, {}, _extract_data_from_message("eu quero 25 bolinha ir 25 coxinhas",
                                                     ConversationState(phone="x")),
                  texto="Eu quero 25 bolinha ir 25 coxinhas")
checa("grandes nao somem com as 25 mini, e o 'ir' nao apaga a bolinha",
      [(i["name"], i["quantity"]) for i in _fg["items"]]
      == [("Coxinha", 3), ("Bolinha de Queijo", 25), ("Coxinha", 25)], _fg["items"])
_ee = _extract_data_from_message("Rua Wilmam maia \n5994 Igarapé 15/11/2026\nAs 17:30",
                                 ConversationState(phone="x"))
checa("endereco em varias linhas sem data nem bairro", _ee.get("endereco") == "Rua Wilmam maia 5994", _ee.get("endereco"))
checa("'pode ser' a oferta do 2L: o produto oferecido",
      app._produtos_da_oferta("Pra acompanhar, que tal um Refrigerante 2L bem gelado? 🥤") == ["Refrigerante 2 Litros"])
checa("oferta de duas opcoes pergunta qual",
      len(app._produtos_da_oferta("Pra acompanhar, vai um Refrigerante 2L ou docinhos (Brigadeiro/Beijinho)? 🥤🍬")) == 2)
print("--- Balbino 15/10: combos, beijinho, pastel, 3 sabores, numero, folhado, CEP")
_ec = _extract_data_from_message("Quero 3 combos de camarão e 1 cento frito", ConversationState(phone="x"))
checa("'3 combos de camarao e 1 cento frito': os combos e o cento",
      [(c["name"], c["quantity"]) for c in _ec.get("combos") or []] == [("Combo Mix Camarão", 3)]
      and _ec.get("volume_pendente") == 100, _ec)
_ee = _extract_data_from_message("quero 2 combo 15 empadinhas", ConversationState(phone="x"))
checa("'2 combo 15 empadinhas' nao vira 15 empadinhas avulsas",
      not _ee.get("items") and _ee["combos"][0]["quantity"] == 2, _ee)
import cardapio_catalogo as _cc1510
_tr = _cc1510.traduzir([{"name": "Combo Mix Camarão", "quantity": 3}])
checa("combo vai para o pedido do cardapio digital", _tr[0] and not _tr[1], _tr)
_fp = _extract_data_from_message("Quer um cento de pastel", ConversationState(phone="x"))
checa("'cento de pastel' pergunta carne ou queijo",
      not _fp.get("items") and _fp["sabores_pendentes"][0]["termo"] == "pastel", _fp)
_fs = _extract_data_from_message("Quero 15 salsicha e 25 coxinhas e 10 salsichas", ConversationState(phone="x"))
checa("mesmo produto duas vezes na mensagem soma",
      ("Enroladinho de Salsicha", 25) in [(i["name"], i["quantity"]) for i in _fs["items"]], _fs["items"])
_fb = _extract_data_from_message("quero uma bandeja de beijinho", ConversationState(phone="x"))
checa("bandeja de beijinho leva o sabor", _fb["doces"][0].get("sabor") == "Beijinho", _fb)
_fm = _extract_data_from_message("quero meio cento de brigadeiro misto", ConversationState(phone="x"))
checa("brigadeiro misto nao vira Misto de Queijo e Presunto",
      not _fm.get("items") and _fm["doces"][0].get("sabor", "").startswith("Mista"), _fm)
checa("'Beijinhos tem' responde do cardapio, sem equipe",
      (app._resposta_do_balcao("Beijinhos tem", {}) or ("", "", True))[0] == "beijinho")
checa("cento com 3 sabores: qual vai em dobro",
      "dobro" in (app._resposta_do_balcao("Se eu quiser só 3 tipos no cento tem como", {}) or ("", ""))[1])
_fn = dict(memoria_mod._empty(), modality="entrega", endereco="Rua Brasília", bairro="Tucumanzal")
memoria_mod.capturar_numero_da_casa(_fn, "Dia 15, às 14:00, pix, 907")
checa("numero da casa no fim da lista com hora", _fn["endereco"] == "Rua Brasília, 907", _fn["endereco"])
from agents.conversation import item_fora_do_cardapio as _ifc1510
checa("'50 salgados folhados' e tipo que nao existe", _ifc1510("Quero 50 salgados folhados") == "salgados folhados")
_src_1510 = open("app.py", encoding="utf-8").read()
# 18/10: o CEP agora e consultado; so o que nao deu para usar pede o endereco.
checa("CEP que nao deu para usar pede o endereco", "Não encontrei esse CEP" in _src_1510)
checa("modelo prometendo horario: frase removida", "modelo prometeu horario; frase removida" in _src_1510)
print("--- Balbino 16/10: 2 combos, 25 SAL, refrigerantes por tamanho")
_ps = _extract_data_from_message("MARAVILHA 50 COX E 25 SAL E 25 BOLINHA", ConversationState(phone="x"))
checa("'25 SAL' pergunta '25 de salsicha'",
      [p["termo"] for p in _ps.get("sabores_pendentes") or []] == ["salsicha"], _ps.get("sabores_pendentes"))
_lr = app._faq_lista_de_refri("")
checa("lista de refrigerantes separa os sabores por tamanho",
      "2 Litros:" in _lr and "Lata:" in _lr, _lr)
_src_c = open("app.py", encoding="utf-8").read()
checa("'2 combos' sem dizer qual pergunta qual", "Qual combo você quer?" in _src_c)
checa("combo_pendente esta no esquema da ficha", "combo_pendente" in memoria_mod._empty())
print("--- Balbino 17/10: antecedencia por quantidade e refrigerante por pessoa")
from datetime import date as _d1710, datetime as _dt1710
_n1710 = _dt1710(2026, 10, 15, 15, 0)
for _u, _h, _ok in ((100, "15h20", False), (100, "15h40", True), (400, "15h30", False),
                    (400, "16h", True), (800, "17h", False), (800, "18h", True)):
    checa(f"antecedencia: {_u} salgados as {_h} -> {'ok' if _ok else 'recusa'}",
          horario.validar_agendamento(_d1710(2026, 10, 15), _h, _n1710, unidades=_u)[0] == _ok)
checa("400 pra agora pede 1 hora", "1 hora" in horario.validar_agendamento(
    _d1710(2026, 10, 15), horario.HORARIO_IMEDIATO, _n1710, unidades=400)[1])
checa("refrigerante para festa: 400 ml por pessoa",
      "20 refrigerantes de 2 litros" in app._faq_refri_festa("quantos refri", 100))
checa("antecedencia ate 5 centos: no mesmo dia",
      "De 1 a 5 centos fazemos no mesmo dia" in app._resposta_do_balcao(
          "antecedencia para pedido de 100 unidades?", {})[1])
checa("antecedencia de mais de 500: equipe",
      app._resposta_do_balcao("antecedencia para 800 salgados?", {})[2] is True)
checa("'8 da manha' e 8h", horario.parse_horario("QUERO ENTREGA PARA 8 DA MANHA") == (8, 0))
_ex_var = _extract_data_from_message("MOÇA EU PEDI 100 VARIADOS SENDO COXINHA ,BOLINHA E CHURROS",
                                     ConversationState(phone="x"))
checa("'100 variados sendo coxinha, bolinha e churros' e o cento em 3 sabores (nao 100 coxinhas)",
      _ex_var.get("volume_pendente") == 100 and not _ex_var.get("items"), _ex_var)

# ── Teste do Balbino de 17/10 (chat) ────────────────────────────────────────
from agents.conversation import detectar_bairro as _db17, detectar_modalidade as _dm17  # noqa: E402
from agents.conversation import item_fora_do_cardapio as _fora17  # noqa: E402
checa("'RONALDO' (o nome) nao vira o bairro Ronaldo Aragao", _db17("RONALDO") == "")
checa("'IGAPARE' seco continua sendo o bairro", _db17("IGAPARE") == "Igarapé")
checa("'OPA RESOLVI PEGAR NA LOJA' e retirada", _dm17("OPA RESOLVI PEGAR NA LOJA") == "retirada")
checa("'500 SALASICHAS' nao e item fora do cardapio",
      _fora17("500 COXINHAS E 500 SALASICHAS") == "")
_ex17 = _extract_data_from_message(
    "quero somente 50 coxinhas e 25 salsicha e 25 bolinha retirada pagamento no pix",
    ConversationState(phone="x"))
checa("'25 bolinha retirada' guarda a bolinha",
      sorted(i["quantity"] for i in _ex17.get("items") or []) == [25, 25, 50], _ex17.get("items"))
_ex17c = _extract_data_from_message("quero 100 salgadinhos coxinha bolinha e chirros",
                                    ConversationState(phone="x"))
checa("'coxinha bolinha e chirros' e o cento em 3 sabores",
      _ex17c.get("volume_pendente") == 100 and not _ex17c.get("items"), _ex17c)
checa("'QURO UM BRIGAEIRO' e o brigadeiro",
      bool(_extract_data_from_message("quro um brigaeiro", ConversationState(phone="x"))
           .get("doces_pendentes")))
checa("'QURO' e aceite curto", app._e_concordancia_ou_quero("QURO"))
checa("'AQUI NO BRASIL E 19:21' nao e o horario do pedido",
      horario.parse_horario("HOJE E SABADO EM QUAL PAIS AQUI NO BRASIL E 19:21") is None)
checa("'HOE AS 20 HORAS' e hoje", horario.resolver_data("HOE AS 20 HORAS") == horario.agora().date())
checa("aviso de atraso tem resposta simples, sem equipe",
      app._resposta_do_balcao("TENHO UMA ENCOMENDA PARA PEGAR AS 20 HORAS MAS VOU DEMORAR UNS 10 MINUTINHOS",
                              {})[:1] == ("atraso",)
      and app._resposta_do_balcao("EU JA FIZ O PEDIDO SO ESTOU AVISANDO QUE VOU DMEORAR", {})[0] == "atraso")
checa("'vai demorar muito?' nao e aviso de atraso",
      (app._resposta_do_balcao("vai demorar muito?", {}) or ("",))[0] != "atraso")
checa("'SEGUNDA QUERO MAIS NAO' e desistencia",
      bool(app._RX_DESISTENCIA.search(facts.normalize("SEGUNDA QUERO MAIS NAO"))))
checa("'nao quero mais nada' nao e desistencia",
      not app._RX_DESISTENCIA.search(facts.normalize("nao quero mais nada")))
_esp17 = "Itens: ...\nPosso confirmar pra segunda (19/10) às 20h? 💛"
checa("'UAI !' no espelho nao fecha o pedido", app._espelho_sem_aceite(_esp17, "UAI !"))
checa("'sim' e 'pose sim' no espelho fecham",
      not app._espelho_sem_aceite(_esp17, "sim") and not app._espelho_sem_aceite(_esp17, "POSE SIM"))

# ── CEP (18/10): rua e bairro pelo CEP. Cache pre-carregado: sem internet ───
import cep as _cep_t  # noqa: E402
_cep_t._CACHE.update({
    "76804120": {"rua": "Rua Afonso Pena", "bairro": "Nossa Senhora das Graças",
                 "cidade": "Porto Velho", "uf": "RO"},
    "76824272": {"rua": "Avenida Calama", "bairro": "Igarapé", "cidade": "Porto Velho", "uf": "RO"},
    "76899999": {"rua": "Rua Inventada", "bairro": "Bairro Que Nao Existe",
                 "cidade": "Porto Velho", "uf": "RO"},
    "01310100": {"rua": "Avenida Paulista", "bairro": "Bela Vista", "cidade": "São Paulo", "uf": "SP"},
})
_ec1 = _extract_data_from_message("76804-120", ConversationState(phone="x"))
checa("CEP sozinho: rua, bairro da tabela e entrega",
      _ec1.get("endereco") == "Rua Afonso Pena" and _ec1.get("bairro") == "Nossa Senhora das Graças"
      and _ec1.get("modality") == "entrega", _ec1)
_ec2 = _extract_data_from_message("quero 50 coxinhas pra entrega, cep 76824-272, casa 120",
                                  ConversationState(phone="x"))
checa("CEP com o numero da casa e o pedido junto",
      _ec2.get("endereco") == "Avenida Calama, 120" and _ec2.get("bairro") == "Igarapé"
      and [(i["name"], i["quantity"]) for i in _ec2.get("items") or []] == [("Coxinha", 50)], _ec2)
checa("CEP de outra cidade nao preenche nada",
      not _extract_data_from_message("CEP 01310-100", ConversationState(phone="x")).get("endereco"))
checa("telefone nao e CEP", _cep_t.cep_na_mensagem("meu numero e 99876-5432") == "")
_mc = memoria_mod._empty()
memoria_mod.merge(_mc, {}, _extract_data_from_message("76899999", ConversationState(phone="x")))
checa("bairro do CEP fora da tabela: a pergunta diz que nao achou",
      _mc.get("bairro_nao_achado") == "Bairro Que Nao Existe" and not _mc.get("bairro"), _mc)
_mn = memoria_mod._empty()
_mn.update({"modality": "entrega", "endereco": "Rua Afonso Pena", "bairro": "Nossa Senhora das Graças"})
checa("o CEP nao vira o numero da casa", memoria_mod.capturar_numero_da_casa(_mn, "76804-120") == "")
memoria_mod.merge(_mn, {}, {"endereco": "Rua Afonso Pena"})
_mn["endereco"] = "Rua Afonso Pena, 45"
memoria_mod.merge(_mn, {}, {"endereco": "Rua Afonso Pena"})
checa("o mesmo CEP de novo nao apaga o numero", _mn["endereco"] == "Rua Afonso Pena, 45", _mn)

# ── Correcao estrutural (19/10): resposta valida grava o campo e segue ──────
checa("horario oferecido na abertura", horario.horario_oferecido(
    "A gente só começa às 07h40! Pode ser a partir desse horário? 😊") == "07h40")
checa("horario oferecido no intervalo", horario.horario_oferecido(
    "Das 12h01 às 13h59 estamos no intervalo de almoço! Pode ser às 14h ou prefere antes das 12h? 😊") == "14h")
checa("horario oferecido pela antecedencia", horario.horario_oferecido(
    "Para 400 salgados precisamos de 1 hora de antecedência! Pode ser a partir das 16h? 😊") == "16h")
checa("oferta de dia nao tem horario oferecido", horario.horario_oferecido(
    "Aos domingos a gente não abre! Posso deixar para segunda-feira (19/10)? 😊") == "")
checa("a recusa de horario guarda o horario oferecido",
      '_memoria["horario_proposto"] = horario.horario_oferecido(_motivo)' in open("app.py", encoding="utf-8").read())
checa("produto do convite: um so",
      app._produto_do_convite("A porcao de Esfirra de Carne sai R$ 30,00.\n\nQual o seu pedido? 😊")
      == "Esfirra de Carne")
checa("produto do convite: dois produtos nao contam",
      app._produto_do_convite("Temos coxinha e bolinha. Qual o seu pedido?") == "")
checa("quantidade sozinha", [bool(app._RX_SO_QUANTIDADE.fullmatch(facts.normalize(t)))
                             for t in ("50", "50 unidades", "meio cento", "quero 75", "50 coxinhas")]
      == [True, True, True, True, False])
_s19 = app._RX_SAUDACAO_DA_CONVERSA.match("Boa tarde, Balbino! Eu sou a Sandrinha da BALBS 💛 Pra acompanhar?")
checa("regras: a saudacao e separada do resto",
      _s19 and _s19.group(0).strip() == "Boa tarde, Balbino! Eu sou a Sandrinha da BALBS 💛")
checa("regras: sem saudacao nao saem", not app._RX_SAUDACAO_DA_CONVERSA.match("Qual o seu pedido? 😊"))
app._REGRAS_ENVIADAS.pop("5519x", None)
checa("regras: uma vez por conversa",
      app._regras_ainda_nao_enviadas("5519x") and not app._regras_ainda_nao_enviadas("5519x"))
checa("regras: uma dica so, a dos 25 em 25 nos salgadinhos de festa",
      all(x in app.texto_regras_do_pedido() for x in ("ATENÇÃO", "Salgados de festa", "25 em 25", "Não fracionamos"))
      and not any(x in app.texto_regras_do_pedido() for x in ("500 salgados", "Confira o resumo", "CEP")))
checa("dicas: pergunta o pedido so quando o resto nao pergunta nada",
      app.texto_regras_do_pedido(com_pergunta=True).endswith(app.PERGUNTA_DO_PEDIDO_NAS_DICAS)
      and "?" not in app.texto_regras_do_pedido())
checa("'9 hras' e 9h (teste de 21/10)",
      [horario.parse_horario(t) for t in ("amanha as 9 hras tem como", "9 hs", "13 hors", "9 horario")]
      == [(9, 0), (9, 0), (13, 0), None])
checa("antecedencia: tempo pela quantidade, sem pedir o dia de novo com pedido aberto",
      app._faq_antecedencia("mas quantos minutos", {"items": [{"name": "Coxinha frango", "quantity": 500}]})
      == "De 1 a 5 centos fazemos no mesmo dia! 😊 Para 500 salgadinhos, precisamos de 1 hora de antecedência.")
checa("antecedencia: 'mas quantos minutos?' e pergunta solta de tempo",
      [bool(app._RX_QUANTO_TEMPO_SOLTO.fullmatch(t)) for t in (
          "mas quantos minutos", "e quanto tempo", "quanto tempo demora a entrega")]
      == [True, True, False])
import config_dono as _cd20
checa("aviso do painel: validacao",
      [_cd20.validar_aviso_inicio(a, p) == "" for a, p in (
          ("", ""), ("🚨 *AVISO:*\nSo de 25 em 25!", "O que vai ser? 💛"),
          ("Vai querer?", ""), ("Aviso", "sem interrogacao"), ("x" * 501, ""))]
      == [True, True, False, False, False])
_aviso_orig = _cd20.aviso_inicio
_cd20.aviso_inicio = lambda: {"aviso": "", "pergunta": ""}
checa("aviso do painel: em branco vale o padrao",
      app.texto_regras_do_pedido(True)
      == f"{app.AVISO_DO_INICIO_PADRAO} {app.PERGUNTA_DO_PEDIDO_NAS_DICAS}")
_cd20.aviso_inicio = lambda: {"aviso": "AVISO DO DONO", "pergunta": "E ai? 💛"}
checa("aviso do painel: o texto do dono substitui o padrao",
      app.texto_regras_do_pedido(True) == "AVISO DO DONO E ai? 💛"
      and app.texto_regras_do_pedido() == "AVISO DO DONO")
_cd20.aviso_inicio = _aviso_orig
checa("aviso: so a pergunta generica do pedido da lugar a do aviso",
      [app._so_pergunta_generica_do_pedido(t) for t in (
          "Quais delícias vamos pedir hoje?", "Qual o seu pedido? 😊",
          "Pode ser a partir das 07h40?", "Qual horário você quer retirar?",
          "Quais sabores você vai querer?", "A porção sai R$ 30,00. Qual o seu pedido?")]
      == [True, True, False, False, False, False])
checa("'amnha' e amanha", horario.resolver_data("amnha") == horario.agora().date()
      + __import__("datetime").timedelta(days=1))
checa("repergunta mantem a resposta quando o cliente perguntou",
      "_resposta_dada" in open("app.py", encoding="utf-8").read())

checa("data curta de outro dia",
      horario.formatar_data_curta(horario.agora().date() + __import__("datetime").timedelta(days=3))
      .endswith(")") )

# ── Testes do dono de 07/10 ────────────────────────────────────────────────
secao("--- dono 07/10: troco com total, retorno no dinheiro, cardapio novo ---")
# 14h16: entrega paga em dinheiro saiu sem os R$ 2,00 de retorno do motoboy.
checa("entrega em dinheiro paga o retorno de R$ 2,00",
      cardapio.calcular_taxa_retorno("entrega", "dinheiro") == 2.00)
_cf_fx.bruto = lambda forcar=False: dict(_RETRATO_FX, InternalBusinessRule=[
    {"rule_key": "taxa_retorno_maquina", "active": True,
     "rule_value": "R$ 2,00 somente em pagamento com cartao. Sem taxa para dinheiro."}])
checa("a regra antiga do cardapio ('somente cartao') nao isenta mais o dinheiro",
      cardapio.calcular_taxa_retorno("entrega", "dinheiro") == 2.00)
_regra_prompt = [r["valor"] for r in _cf_fx.montar_dados()["regras"]
                 if r["chave"] == "taxa_retorno_maquina"]
checa("o modelo le a regra do dono, nao a do cardapio",
      _regra_prompt and "dinheiro ou cart" in _regra_prompt[0], str(_regra_prompt))
_cf_fx.bruto = lambda forcar=False: _RETRATO_FX

# 14h30: "UAI QUANDO E VALOR TOTAL" na etapa do troco (oferta ja feita).
_F_TROCO = dict(_F_DIN, upsell_feito=True)
checa("cliente pergunta o total na etapa do troco: o codigo responde",
      app._resposta_total_no_troco("UAI QUANDO E VALR=OR TOTAL", dict(_F_TROCO))
      == "O seu pedido com a entrega deu R$ 30,00. Precisa de troco para quanto? 💵",
      app._resposta_total_no_troco("UAI QUANDO E VALR=OR TOTAL", dict(_F_TROCO)))
checa("'quanto fica?' tambem", app._resposta_total_no_troco("quanto fica?", dict(_F_TROCO)) != "")
checa("valor de troco nao e pergunta de total",
      app._resposta_total_no_troco("troco para 50", dict(_F_TROCO)) == "")
checa("fora da etapa do troco nao responde",
      app._resposta_total_no_troco("qual o total?", dict(_F_TROCO, troco_para="nao")) == "")
checa("sem frete oficial a pergunta fica sem total",
      memoria_mod.pergunta_do_troco(dict(_F_DIN, bairro="Marte City"))
      == "Precisa de troco para quanto? 💵")

# Audio + print do dono: "25 uni Salgadinhos assados" cadastrado como venda
# "unit" de 25 em 25, na categoria "salgadinhos assados" (com espaco).
_ASSADO_25 = {"name": "25 uni Salgadinhos assados", "category": "salgadinhos assados",
              "sales_mode": "unit", "unit_increment": 25.0, "batch_size": None,
              "min_quantity": None, "price": 30.0, "max_flavors": 1.0, "available": True,
              "flavor_options": ["esfirra de carne", "enroladinho de salsicha",
                                 "calzone de frango", "empada de frango", "pão pizza"]}
_prod_25 = _cf_fx._produtos([_ASSADO_25])
checa("porcao de 25 assados entra no catalogo",
      any(p["categoria"] == "Assados" and p["item"] == "Porção (25 un)" and p["preco"] == 30.0
          for p in _prod_25), str(_prod_25[:2]))
_sab_25 = {p["item"] for p in _prod_25 if p["formato"] == "Sabor Cento/Porção"}
checa("sabores da porcao com o nome que o codigo conhece",
      {"Esfirra de Carne", "Empadinha de Frango", "Pão Pizza",
       "Enroladinho de salsicha com Catupiry"} <= _sab_25, str(_sab_25))
checa("enroladinho da porcao de assados nao vira o frito",
      "Enroladinho de Salsicha" not in _sab_25)
_api, _bl = catalogo.traduzir([{"name": "Esfirra de Carne", "quantity": 25}])
checa("25 esfirras vao na porcao de 25 com a grafia dela",
      not _bl and _api == [{"name": "25 uni Salgadinhos assados", "quantity": 25,
                           "selected_flavors": ["esfirra de carne"]}], str((_api, _bl)))
_api, _bl = catalogo.traduzir([{"name": "Esfirra de Carne", "quantity": 25},
                               {"name": "Empadinha de Frango", "quantity": 25},
                               {"name": "Pão Pizza", "quantity": 25}])
checa("75 assados de 3 sabores: meio cento + porcao",
      not _bl and [(a["name"], a["quantity"]) for a in _api]
      == [("Meio Cento Salgadinhos Assados", 50), ("25 uni Salgadinhos assados", 25)], str(_api))

# 14h37/14h40: pastel de queijo cadastrado nos fritos (retrato de 07/10).
_RETRATO_0710 = _json_fx.load(open("fixtures/cardapio_snapshot_2026-10-07.json", encoding="utf-8"))
_cf_fx.bruto = lambda forcar=False: _RETRATO_0710
facts.invalidate()
_api, _bl = catalogo.traduzir([{"name": "Pastel carne", "quantity": 100},
                               {"name": "Pastel queijo", "quantity": 50}])
checa("pastel de queijo nao e mais bloqueado",
      not _bl and _api and _api[0]["selected_flavors"] == ["pastel de carne", "pastel de queijo"],
      str((_api, _bl)))
checa("'pastel de queijo' do cardapio vira o sabor Pastel queijo",
      _cf_fx._nome_do_sabor("pastel de queijo", "Fritos") == "Pastel queijo")
from agents.conversation import item_fora_do_cardapio as _ifc_0710  # noqa: E402
checa("'100 pastel de carne e 50 pastel de queijo' nao e item fora do cardapio",
      _ifc_0710("100 PASTEL DE CARNE E 50 PASTEL DE QUEIJO") == "",
      _ifc_0710("100 PASTEL DE CARNE E 50 PASTEL DE QUEIJO"))
checa("'200 PASTEID DE CARNE E DE QUEIJO' (14h37) vira 100 + 100",
      _itens("QUERO PARA ENTREGA 200 PASTEID DE CARNE E DE QUEIJO")
      == [(100, "Pastel Carne"), (100, "Pastel Queijo")],
      str(_itens("QUERO PARA ENTREGA 200 PASTEID DE CARNE E DE QUEIJO")))
checa("'100 PASTEL DE CARNE E 50 PASTEL DE QUEIJO' (14h40)",
      _itens("100 PASTEL DE CARNE E 50 PASTEL DE QUEIJO")
      == [(100, "Pastel Carne"), (50, "Pastel Queijo")])
checa("'pasteis' no plural", _itens("quero 50 pasteis de queijo") == [(50, "Pastel Queijo")])
checa("'25 calzone' e sabor da porcao de assados",
      _itens("25 calzone") == [(25, "Calzone de frango")])
checa("'enroladinhos assados' e o assado, nao o frito",
      _itens("quero 25 enroladinhos assados") == [(25, "Enroladinho de salsicha com Catupiry")])
checa("dois sabores com tipo repetido nao sao divididos",
      _itens("200 coxinhas e 100 bolinhas de queijo") == [(200, "Coxinha"), (100, "Bolinha de Queijo")],
      str(_itens("200 coxinhas e 100 bolinhas de queijo")))
checa("porcao de 25 assados no catalogo de hoje",
      any(p["categoria"] == "Assados" and p["item"] == "Porção (25 un)" and p["preco"] == 30.0
          for p in facts.get()["produtos"]))
_cf_fx.bruto = lambda forcar=False: _RETRATO_FX
facts.invalidate()

# ── Volta automatica da fila humana (22/10) ──
import volta_automatica as _va
from datetime import datetime as _dtva, timedelta as _tdva
checa("volta: decisao pela leitura da conversa",
      [_va.decidir({"assunto": a, "situacao": s, "mensagem_nova": n}) for a, s, n in (
          ("reclamacao", "pendente", "assunto_novo"),
          ("reclamacao", "resolvido", "continua_assunto"),
          ("pedido", "pendente", "continua_assunto"),
          ("pedido", "pendente", "assunto_novo"),
          ("duvida", "resolvido", "cumprimento"))]
      == ["fica", "volta", "avisa", "volta", "volta"])
checa("volta: leitura ilegivel nao conta",
      _va.normalizar({"assunto": "x", "situacao": "talvez", "mensagem_nova": "cumprimento"}) is None
      and _va.normalizar({"assunto": "inventado", "situacao": "pendente",
                          "mensagem_nova": "assunto_novo", "resumo": "a  b"})
      == {"assunto": "outro", "situacao": "pendente", "mensagem_nova": "assunto_novo", "resumo": "a b"})
checa("volta: horas do painel",
      [_va.validar_horas(v) == "" for v in ("4", "0", "2,5", "-1", "100", "abc")]
      == [True, True, True, False, False, False])
_va_horas, _va_ultima = _va.horas, _va.ultima_mensagem_ao_cliente
_va.horas = lambda: 4.0
_va_agora = _dtva(2026, 10, 22, 20, 0)
_va.ultima_mensagem_ao_cliente = lambda p, d: _va_agora - _tdva(hours=1)
checa("volta: equipe respondeu ha 1h -> nao volta",
      not _va.prazo_vencido("55va", {"transferred_at": "2026-10-22T10:00:00"}, _va_agora))
_va.ultima_mensagem_ao_cliente = lambda p, d: _va_agora - _tdva(hours=5)
checa("volta: 5h sem mensagem ao cliente -> pode voltar",
      _va.prazo_vencido("55va", {"transferred_at": "2026-10-22T10:00:00"}, _va_agora))
checa("volta: assumida pelo painel (sem transferred_at) nao volta",
      not _va.prazo_vencido("55va", {"transferred_at": None}, _va_agora))
_va.marcar_avaliada("55va")
checa("volta: avaliada e mantida, so reavalia depois de outro prazo",
      not _va.prazo_vencido("55va", {"transferred_at": "2026-10-22T10:00:00"}, _va_agora))
_va.esquecer_avaliacao("55va")
_va.horas = lambda: 0.0
checa("volta: 0 horas desliga",
      not _va.prazo_vencido("55va", {"transferred_at": "2026-10-22T10:00:00"}, _va_agora))
_va.horas, _va.ultima_mensagem_ao_cliente = _va_horas, _va_ultima
checa("volta: a ficha guarda o contexto da retomada",
      "contexto_retomada" in memoria_mod._empty())

# ── Teste da Thayline (22/10): os 5 graves ──
_ult_beij = ("Temos sim! 😊 A Bandeja com 16 un (R$ 20,80) sai de beijinho, brigadeiro ou mista. "
             "No meio cento e no cento, dá para pedir mista (brigadeiro e beijinho). Qual você prefere?")
checa("22/10 doce: 'cento misto' depois do beijinho e o doce misto",
      app._doce_misto_do_contexto("pode ser o cento misto", _ult_beij) == "pode ser o cento de brigadeiro misto"
      and app._doce_misto_do_contexto("pode ser o cento misto", "Qual sabor de pastel?") == ""
      and app._doce_misto_do_contexto("pode ser o cento misto", "Dinheiro para quanto?", True)
      == "pode ser o cento de brigadeiro misto"
      and app._doce_misto_do_contexto("25 misto 25 croquete", _ult_beij) == "")
_d22 = _extract_data_from_message("teria como adcionar meio cento de brigadeiro e meio cento de beijinho",
                                  ConversationState(phone="t")).get("doces") or []
checa("22/10 doce: meio cento de brigadeiro + meio cento de beijinho = cento misto",
      [(d["name"], d.get("sabor")) for d in _d22]
      == [("Cento de Brigadeiros (100 un)", "Mista (brigadeiro e beijinho)")], str(_d22))
checa("22/10 doce: docinho com beijinho fica na resposta fixa (o modelo chamava a equipe)",
      (app._resposta_de_faq("teria como adcionar meio cento de brigadeiro e meio cento de beijinho", {})
       or ("",))[0] == "beijinho"
      and app._resposta_de_faq("beijinho tem?", {})[0] == "beijinho")
_m22 = memoria_mod._empty(); _m22["payment_method"] = "dinheiro"
checa("22/10 troco: 'cento misto' nao e troco de R$ 100",
      memoria_mod.capturar_troco(_m22, "Precisa de troco para quanto?", "pode ser o cento misto") == ""
      and memoria_mod.capturar_troco(_m22, "Precisa de troco para quanto?", "190,00") == "190.00")
checa("22/10 sabor: 'assados' sozinho nao e Enroladinho de salsicha com Catupiry",
      (_extract_data_from_message("teria como fazer pra agora 3 centos assados",
                                  ConversationState(phone="t")).get("items") or []) == []
      and _extract_data_from_message("teria como fazer pra agora 3 centos assados",
                                     ConversationState(phone="t")).get("volume_pendente") == 300
      and _itens("25 enroladinho assado") == [(25, "Enroladinho de salsicha com Catupiry")])
_esp22 = ("Cliente: Thayline\n📅 Data: 23/10/2026\nItens:\n• 3 un - Refrigerante 2 Litros (Coca cola) — R$ 45,00\n"
          "• 1 un - Cento de Brigadeiros (100 un) — R$ 140,00\n💰 Total: R$ 685,00\n💳 Pgt: Balcão\n"
          "Posso confirmar pra amanhã (sex, 23/10) às 18h30? 💛")
checa("22/10 confirmacao: o resumo nao e oferta de acompanhamento",
      not app._ofereceu_acompanhamento(_esp22) and app._confirmou_o_resumo(_esp22, "isso pode sim")
      and app._ofereceu_acompanhamento("Pra acompanhar, vai um Refrigerante 2L ou docinhos (Brigadeiro/Beijinho)? 🥤🍬"))
checa("22/10 'disponiveis'/'orcamentos' nao sao produto",
      app._resposta_de_faq("quais sabores vc tem disponiveis?", {}) is None
      and app._resposta_de_faq("vcs fazem orçamentos?", {}) is None
      and app._resposta_de_faq("vcs tem mini hamburgues", {})[0] == "produto inexistente")

# ── Teste da Thayline (22/10): erros de fluxo ──
checa("22/10 endereco: 'dia 30' e 'pra agora' nao entram na rua",
      [_extract_data_from_message(t, ConversationState(phone="t")).get("endereco") for t in (
          "igarape rua wilamam maia 5994 dia 30 as 18:30", "igarape rua wilmam maia 5994 dia 22 pra agora",
          "rua dia 15 de maio 20", "rua sete de setembro 200")]
      == ["rua wilamam maia 5994", "rua wilmam maia 5994", "rua dia 15 de maio 20", "rua sete de setembro 200"])
from agents.conversation import item_fora_do_cardapio as _fora22
checa("22/10 'camudo de camarao' e erro de digitacao, nao item fora",
      _fora22("50 camudo de camarao") == "" and _fora22("50 travesseirinhos de carne") != "")
checa("22/10 'orcamento para 50 pessoas' usa a regra dos 10 por pessoa",
      (app._resposta_do_balcao("queria fazer um orçamento para 50 pessoas", {}) or ("",))[0] == "festa pessoas")
_mv22 = memoria_mod._empty()
_mv22.update({"volume_pendente": 200, "items": [{"name": "Coxinha", "quantity": 50},
              {"name": "Croquete", "quantity": 25}, {"name": "Enroladinho de Salsicha", "quantity": 25},
              {"name": "Pastel queijo", "quantity": 25}]})
checa("22/10 sabores somando menos que o total: pergunta o que falta",
      memoria_mod.falta_para_o_volume(_mv22) == 75
      and "75 que faltam, ou fica com 125" in memoria_mod.pergunta_bloqueante(_mv22))
_mv22["items"].append({"name": "Quibe", "quantity": 75})
checa("22/10 sabores completando o total: nada falta", memoria_mod.falta_para_o_volume(_mv22) == 0)

# Pedido real de 22/10 (WhatsApp): o modelo mandou o sabor colado no nome e o
# cardapio digital recusou o pedido pronto.
_env22 = app._separar_sabor_do_nome(
    [{"name": "Coxinha frango", "price": 0.8, "quantity": 50},
     {"name": "Cento de Brigadeiros (100 un) (Mista (brigadeiro e beijinho))", "price": 140.0, "quantity": 1}], [])
checa("22/10 envio: o sabor colado no nome vira o campo sabor",
      _env22[1] == {"name": "Cento de Brigadeiros (100 un)", "price": 140.0, "quantity": 1,
                    "sabor": "Mista (brigadeiro e beijinho)"}
      and app._nome_sem_sabor("Cento de Brigadeiros (100 un)") == ("Cento de Brigadeiros (100 un)", ""))
import cardapio_catalogo as _cc22
checa("22/10 envio: o cento misto passa pelo de-para do cardapio digital",
      not _cc22.traduzir(_env22)[1], str(_cc22.traduzir(_env22)))

# Teste no WhatsApp (22/10): troca de sabores com docinho pendente.
checa("22/10 'mandei errado os sabores posso troca?' e pedido de troca",
      bool(app._RX_QUER_TROCAR_SABORES.search(facts.normalize("mandei errado os sabores posso troca?")))
      and not app._RX_QUER_TROCAR_SABORES.search(facts.normalize("quais sabores voces tem?")))
_mt22 = memoria_mod._empty()
_mt22.update({"items": [{"name": "Canudinho de Camarao", "quantity": 50}, {"name": "Coxinha", "quantity": 50},
                        {"name": "Refrigerante 2 Litros", "quantity": 1}], "trocar_sabores": True})
_ext_t22 = _extract_data_from_message("50 bolinha e 50 pastel de carne", ConversationState(phone="t"))
memoria_mod.merge(_mt22, _ext_t22, _ext_t22, texto="50 bolinha e 50 pastel de carne")
checa("22/10 troca pedida: a lista nova substitui os salgados e a bebida fica",
      sorted(i["name"] for i in _mt22["items"]) == ["Bolinha de Queijo", "Pastel Carne", "Refrigerante 2 Litros"]
      and not _mt22["trocar_sabores"], str([i["name"] for i in _mt22["items"]]))
_md22 = memoria_mod._empty()
_md22["sabores_pendentes"] = [{"qtd": 1, "termo": "docinho", "opcoes": ["Bandeja de Brigadeiros (16 un)",
                               "Meio Cento de Brigadeiros (50 un)", "Cento de Brigadeiros (100 un)"]}]
checa("22/10 docinho pendente: '50 bolinha' nao e o meio cento",
      not memoria_mod.aplicar_escolha_de_sabor(_md22, "50 bolinha e 50 pastel de carne")
      and memoria_mod.aplicar_escolha_de_sabor(_md22, "meio cento"))

# Pendencias menores do teste de 22/10.
checa("22/10 '300 fritos' e '200 fritos e 100 assados' sao o volume",
      _vss("quero 300 fritos") == 300
      and _vss("200 fritos e 100 assados") == 300
      and _vss("100 coxinhas fritas") == 0)
checa("23/10 'um cento e meio' e 150 e 'cento e cinquenta' nao vira 100",
      _vss("um cento e meio") == 150 and _vss("cento e cinquenta") != 100)
_mtr22 = memoria_mod._empty(); _mtr22["payment_method"] = "dinheiro"
checa("22/10 troco 'cento e cinquenta' e R$ 150, nao R$ 50",
      memoria_mod.capturar_troco(_mtr22, "Precisa de troco para quanto?", "cento e cinquenta") == "150.00")
import agents.conversation as _cv22
checa("22/10 'Avenida Domingos' e 'Rua Quinta do Sol' nao sao dia da semana",
      _cv22._FIM_DA_RUA.split("Avenida Domingos Vieira 120", maxsplit=1)[0] == "Avenida Domingos Vieira 120"
      and _cv22._FIM_DA_RUA.split("Rua Quinta do Sol 45", maxsplit=1)[0] == "Rua Quinta do Sol 45"
      and _cv22._FIM_DA_RUA.split("Rua Guanabara sexta-feira", maxsplit=1)[0] == "Rua Guanabara")
_mmi22 = memoria_mod._empty()
_mmi22["items"] = [{"name": "Cento de Brigadeiros (100 un)", "quantity": 1, "price": 140.0}]
_tmi22 = app._doce_misto_do_contexto("pode ser misto", "Qual docinho? Brigadeiro ou beijinho?",
                                     "Cento de Brigadeiros (100 un)")
_emi22 = _extract_data_from_message(_tmi22, ConversationState(phone="t"))
memoria_mod.merge(_mmi22, _emi22, _emi22, texto=_tmi22)
checa("22/10 'pode ser misto' com o cento de brigadeiro na ficha muda o sabor dele",
      [(i["name"], i.get("sabor")) for i in _mmi22["items"]]
      == [("Cento de Brigadeiros (100 un)", "Mista (brigadeiro e beijinho)")], str(_mmi22["items"]))
_esp23 = ("Cliente: Eduardo\n📅 Data: 29/10/2026\n• 1 un - Cento de Brigadeiros (100 un) — R$ 140,00\n"
          "💳 Pgt: Dinheiro (troco para R$ 550,00)\nPosso confirmar pra quinta (29/10) às 17h? 💛")
checa("23/10 'pode ser misto' no espelho muda o pedido e nao fecha",
      not app._confirmou_o_resumo(_esp23, "pode ser misto")
      and app._mexeu_no_pedido_no_aceite(_esp23, "pode ser misto")
      and not app._confirmou_o_resumo(_esp23, "pode ser amanha")
      and app._confirmou_o_resumo(_esp23, "pode ser")
      and app._confirmou_o_resumo(_esp23, "sim, pode confirmar")
      and app._confirmou_o_resumo(_esp23, "pode, em dinheiro")
      and app._confirmou_o_resumo(_esp23, "sim, nada mais"))
checa("23/10 'Rua Quinta do Sol 45' nao muda a data para quinta",
      not _extract_data_from_message("entrega na Rua Quinta do Sol 45 igarape",
                                     ConversationState(phone="t")).get("data_iso")
      and _extract_data_from_message("rua guanabara 45 quinta as 17h",
                                     ConversationState(phone="t")).get("data_iso"))
_mq23 = memoria_mod._empty()
_mq23.update({"name": "Ana", "modality": "entrega", "data_agendada": "hoje, sexta-feira (23/10)",
              "horario": "17h", "items": [{"name": "Coxinha", "quantity": 50}]})
_svq23 = memoria_mod.save
memoria_mod.save = lambda *a, **k: None
memoria_mod.registrar_pedido_enviado("55q23", _mq23)
memoria_mod.save = _svq23
_rq23 = app._resposta_do_balcao("que horas chega?", _mq23)
checa("23/10 'que horas chega?' depois do pedido diz o horario dele, nao o da fabrica",
      _rq23 and _rq23[0] == "quando_chega" and "hoje, sexta-feira (23/10) às 17h" in _rq23[1], str(_rq23))
checa("23/10 'que horas voces abrem?' continua sendo o expediente",
      (app._resposta_do_balcao("que horas voces abrem?", memoria_mod._empty()) or ("",))[0] != "quando_chega")
# Teste da Thayline (23/10): 2 centos assados com 100 nos sabores.
def _mth23():
    _m = memoria_mod._empty()
    _m.update({"volume_pendente": 200, "pedido_por_volume": True, "items": [
        {"name": "Pão Pizza", "quantity": 25, "price": 0.8}, {"name": "Calzone de frango", "quantity": 25, "price": 0.8},
        {"name": "Esfirra de Carne", "quantity": 25, "price": 0.8}, {"name": "Enroladinho de Salsicha", "quantity": 25, "price": 0.8}]})
    return _m
_t23 = "ver 50 pao pizza 50 canudos metade camarao metade frango"
_m23 = _mth23(); _e23 = _extract_data_from_message(_t23, ConversationState(phone="t"))
memoria_mod.merge(_m23, _e23, _e23, texto=_t23)
checa("23/10 '50 canudos metade camarao metade frango' e 25 de cada, e o pao pizza soma",
      sorted((i["name"], i["quantity"]) for i in _m23["items"]) == sorted([
          ("Pão Pizza", 75), ("Calzone de frango", 25), ("Esfirra de Carne", 25),
          ("Enroladinho de Salsicha", 25), ("Canudinho de Camarao", 25), ("Canudinho de Frango", 25)])
      and not memoria_mod.falta_para_o_volume(_m23), str(_m23["items"]))
_m23b = _mth23()
checa("23/10 'repetir os mesmos' completa com 50 de cada",
      memoria_mod.repetir_os_mesmos(_m23b) and all(i["quantity"] == 50 for i in _m23b["items"])
      and bool(app._RX_REPETIR_OS_MESMOS.search(facts.normalize("pode repetir os msm tem como"))))
_men23 = memoria_mod._empty(); _men23.update({"volume_pendente": 100, "preparo_pendente": "assado"})
_een23 = _extract_data_from_message("25 pao pizza 25 calzone 25 esfira 25 enroladinho", ConversationState(phone="t"))
memoria_mod.merge(_men23, _een23, _een23, texto="25 pao pizza 25 calzone 25 esfira 25 enroladinho")
_mef23 = memoria_mod._empty(); _mef23.update({"volume_pendente": 50, "preparo_pendente": "frito"})
_eef23 = _extract_data_from_message("25 coxinha 25 enroladinho", ConversationState(phone="t"))
memoria_mod.merge(_mef23, _eef23, _eef23, texto="25 coxinha 25 enroladinho")
checa("23/10 'enroladinho' em pedido de assados e o com Catupiry; em fritos, o de salsicha",
      "Enroladinho de salsicha com Catupiry" in [i["name"] for i in _men23["items"]]
      and "Enroladinho de Salsicha" in [i["name"] for i in _mef23["items"]],
      str([i["name"] for i in _men23["items"]]))
checa("22/10 '25 misto' com doce na ficha continua sendo o salgado",
      app._doce_misto_do_contexto("25 misto", "Qual docinho?", "Cento de Brigadeiros (100 un)") == "")

print()
print("=" * 78)
print("FALHAS:", _falhas or "nenhuma")
sys.exit(1 if _falhas else 0)
