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
import conversation_memory as memoria_mod  # noqa: E402
import facts  # noqa: E402
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
checa("data com mes que ja passou vai para o ano seguinte",
      detectar_data("16/09", AGORA) == date(2027, 9, 16))

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
      _sugestoes([{"name": "Quibe", "quantity": 5}]) == [25])

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
      not _envio_aceita([{"name": "Quibe", "quantity": 5}]))

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
checa("manda isolar so o item sem tamanho", "ISOLANDO SO ESSE ITEM" in _dir)

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
    ok, _msg, _dia = horario.validar_agendamento(_HOJE, texto, _MANHA_DE_HOJE)
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
checa("ficha incompleta nao barra ainda",
      _troco(troco_para="50.00", horario="") == _ESPELHO_TROCO)
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

print()
print("=" * 78)
print("FALHAS:", _falhas or "nenhuma")
sys.exit(1 if _falhas else 0)
