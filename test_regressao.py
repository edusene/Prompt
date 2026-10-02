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
checa("25 assados sozinhos sobem para 50",
      _sugestoes([{"name": "Esfirra de Carne", "quantity": 25}]) == [50])
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
checa("o campo invalido e o HORARIO, nao a data", _campo_h == "horario")
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
checa("e traz o multiplo de 25 mais proximo de cada um",
      "25 coxinha" in _frase_lote.lower() and "50 bolinha" in _frase_lote.lower(),
      _frase_lote)

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
checa("espelho que ja saiu nao e refeito",
      _espelho_obrig(_espelho) == _espelho)
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
                "Pedido de fiado e liberação de produção sem pagamento antecipado",
                "cliente insiste em desconto no frete",
                "cliente pede abatimento no valor",
                "cliente diz ser amigo do dono e quer condição especial",
                "negociação de preço fora da tabela"):
    checa(f"BLOCO 6 nao transborda: {_motivo[:38]!r}",
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
checa("a extracao usa a divisao e marca pedido por volume",
      sorted((i["name"], i["quantity"]) for i in _EXTR.get("items", []))
      == [("Bolinha de Queijo", 100), ("Pastel", 100)]
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
checa("prompt sem 'Aviso obrigatorio' depois do aviso dado",
      "Aviso obrigatorio" not in horario.contexto_para_prompt(
          datetime(2026, 9, 30, 12, 51), aviso_ja_dado=True)
      and "JA FOI DADO" in horario.contexto_para_prompt(
          datetime(2026, 9, 30, 12, 51), aviso_ja_dado=True))
checa("prompt com 'Aviso obrigatorio' antes do aviso",
      "Aviso obrigatorio" in horario.contexto_para_prompt(datetime(2026, 9, 30, 12, 51)))
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
# Texto do dono, reuniao de 01/10.
checa("3 sabores num cento: pergunta do dono",
      memoria_mod.pergunta_bloqueante(_F_DIV)
      == "Você quer acrescentar um sabor ou aumentar uma porção dos 3 sabores? 😊",
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
      "- Data do pedido\n- Horário desejado\n- Forma de pagamento (Pix, Cartão ou Dinheiro)",
      memoria_mod.pedido_de_dados(_F_ENT))
checa("parte informada: 'Faltam essas informacoes:' com 'Rua e numero'",
      memoria_mod.pedido_de_dados(dict(_F_ENT, bairro="Olaria")) == "Faltam essas informações:\n"
      "- Rua e número\n- Data do pedido\n- Horário desejado\n- Forma de pagamento (Pix, Cartão ou Dinheiro)")
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
      memoria_mod.pedido_de_dados(_F_PROP, ["data_agendada"]).startswith("Posso deixar para segunda-feira (05/10)"),
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
checa("a pergunta do troco e a do BLOCO 3",
      memoria_mod.pedido_de_dados(_F_DIN) == "Precisa de troco para quanto? 💵")
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
    checa("e sai a pergunta do dono (acrescentar ou aumentar)",
          memoria_mod.pergunta_bloqueante(_F3)
          == "Você quer acrescentar um sabor ou aumentar uma porção dos 3 sabores? 😊",
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
checa("beijinho: avisa a falta e oferece os formatos de brigadeiro",
      _P_DOCE.startswith("O Beijinho está em falta hoje! Qual docinho você prefere:")
      and "Meio Cento de Brigadeiros (50 un)" in _P_DOCE, _P_DOCE)
memoria_mod.aplicar_escolha_de_sabor(_F_DOCE, "meio cento")
checa("'meio cento' responde a pergunta do doce",
      any(i["name"] == "Meio Cento de Brigadeiros (50 un)" for i in _F_DOCE["items"]), str(_F_DOCE["items"]))
_api_doce, _ = _cat.traduzir([{"name": "Bandeja de Brigadeiros (16 un)", "quantity": 1}])
checa("a bandeja vai com o sabor Brigadeiro", _api_doce[0].get("selected_flavors") == ["Brigadeiro"])
secao("--- 02/10: nao inventar nada (varredura de 46 perguntas) ---")
for _txt in ("voces estao admitindo atendente?", "tem vaga de emprego ai?", "posso parcelar no cartão?",
             "posso pagar metade no pix e metade no cartão?", "vocês vendem salgado congelado?",
             "qual o instagram de vocês?", "vocês emitem nota fiscal?", "vocês abrem no feriado dia 12?",
             "qual o telefone da loja?", "vocês tem kit festa?", "quem é o dono da fábrica?",
             "tem estacionamento na loja?", "com quanto tempo de antecedência preciso encomendar?",
             "o combo vem com qual refrigerante?", "tem pedido mínimo pra entrega?", "tem promoção hoje?",
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
checa("coca 2 litros: em falta",
      "em falta" in app._conferir_refri_perguntado("t", "tem coca de 2 litros?", "X"))
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
checa("item novo logo depois de fechar: pergunta fixa 'somar ou novo'",
      "_responder_direto(user, items, PERGUNTA_SOMAR_OU_NOVO, _chegada)" in open("app.py", encoding="utf-8").read()
      and "Como seu pedido já entrou em linha de produção" in app.ALTERACAO_EM_PRODUCAO)
checa("'pode' depois de 'so comeca as 08h00' guarda a hora oferecida",
      'if "só começa" in _motivo:' in open("app.py", encoding="utf-8").read())

print()
print("=" * 78)
print("FALHAS:", _falhas or "nenhuma")
sys.exit(1 if _falhas else 0)
